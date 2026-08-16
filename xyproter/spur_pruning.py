"""ステージ3: スパー（ヒゲ）除去。

事前プロトタイプ検証済みのロジック（neighbor_count/get_neighbors/prune_spurs）を
そのまま移植し、閾値の自動計算と安全弁（連結成分数チェック）を追加する。
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import convolve, distance_transform_edt

from .metrics import count_components, estimate_spur_threshold, estimate_stroke_width
from .types import SkeletonResult

KERNEL = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]])


def neighbor_count(skel: np.ndarray) -> np.ndarray:
    return convolve(skel.astype(int), KERNEL, mode="constant", cval=0)


def get_neighbors(skel: np.ndarray, pt: tuple[int, int]) -> list[tuple[int, int]]:
    y, x = pt
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < skel.shape[0] and 0 <= nx < skel.shape[1] and skel[ny, nx]:
                out.append((ny, nx))
    return out


def prune_spurs(
    skel: np.ndarray,
    min_length: float,
    dist: np.ndarray | None = None,
    stroke_width_px: float = 0.0,
    endpoint_radius_ratio: float = 1.0,
) -> np.ndarray:
    """スケルトンの端点から分岐点までの経路長が min_length 以下なら
    トメ・ハネ由来のノイズとみなして除去する。

    経路が分岐点に到達せずもう一方の端点で終わる場合（他画から独立した
    孤立ストローク全体）は、「実」「音」の一番上の点のような意図的な点画の
    可能性があるため、たとえ短くても除去しない。

    さらに dist（元の塗りつぶし画像の距離変換）と stroke_width_px を指定すると、
    分岐点に繋がる経路であっても、その端点（トメの先端）の太さがストローク半径の
    endpoint_radius_ratio 倍以上ある場合は除去しない。トメ・ハネの細線化アーティ
    ファクトは先端に向かって細くなるのに対し、フォントデザイン上メインストローク
    に接触して描かれた点画（「実」「音」の一番上の点など）は先端でも本体と同程度
    以上の太さを保つため、この違いで区別する。
    """
    skel = skel.copy()
    nb = neighbor_count(skel)
    endpoints = list(zip(*np.where(skel & (nb == 1))))
    to_remove = set()
    stroke_radius = stroke_width_px / 2.0
    for ep in endpoints:
        if ep in to_remove:
            continue
        path = [ep]
        prev, cur = None, ep
        reached_branch = False
        while True:
            nbrs = [n for n in get_neighbors(skel, cur) if n != prev]
            if len(nbrs) == 0:
                break
            if len(nbrs) > 1:
                break
            nxt = nbrs[0]
            if nb[nxt] >= 3:
                reached_branch = True
                break
            path.append(nxt)
            prev, cur = cur, nxt
        if not reached_branch or len(path) > min_length:
            continue
        if dist is not None and stroke_radius > 0 and dist[ep] >= endpoint_radius_ratio * stroke_radius:
            continue
        to_remove.update(path)
    for (y, x) in to_remove:
        skel[y, x] = False
    return skel


def remove_spurs(
    binary: np.ndarray,
    raw_skeleton: np.ndarray,
    threshold_px: float | None = None,
    factor: float = 1.4,
) -> SkeletonResult:
    """スパー除去を実行し、統計・警告を含む SkeletonResult を返す。

    threshold_px を指定しない場合は、ストローク幅から自動算出する
    （文字ごとに手動調整しなくて済むようにするため）。
    """
    stroke_width_px = estimate_stroke_width(binary, raw_skeleton)
    threshold = threshold_px if threshold_px is not None else estimate_spur_threshold(
        stroke_width_px, factor
    )

    dist = distance_transform_edt(binary)

    n_before = count_components(raw_skeleton)
    pruned = prune_spurs(raw_skeleton, threshold, dist=dist, stroke_width_px=stroke_width_px)
    n_after = count_components(pruned)

    warnings: list[str] = []
    if n_after != n_before:
        warnings.append(
            f"スパー除去前後で連結成分数が変化しました ({n_before} -> {n_after})。"
            "閾値が大きすぎて本画を切断した可能性があります。"
        )

    return SkeletonResult(
        raw_skeleton=raw_skeleton,
        pruned_skeleton=pruned,
        stroke_width_px=stroke_width_px,
        spur_threshold_px=threshold,
        n_components_before=n_before,
        n_components_after=n_after,
        warnings=warnings,
    )
