"""ストローク幅推定・各種閾値の自動計算。

スパー除去（stage3）と交差点集約（stage4）の両方が、
「文字ごとに手動チューニングしなくて済む」よう、この基準値を共有する。
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt
from skimage.measure import label


def count_components(mask: np.ndarray) -> int:
    """2値マスクの8連結成分数を数える。"""
    if not np.any(mask):
        return 0
    _, n = label(mask, connectivity=2, return_num=True)
    return int(n)


def estimate_stroke_width(binary: np.ndarray, skeleton: np.ndarray) -> float:
    """二値画像とそのスケルトンから、代表的なストローク幅(px)を推定する。

    塗りつぶし画像の距離変換をスケルトン画素位置でサンプリングし、
    中央値の2倍（中心から両端までの距離の合計）をストローク幅とする。
    スケルトンが空の場合は0.0を返す。
    """
    if not np.any(skeleton):
        return 0.0
    dist = distance_transform_edt(binary)
    radii = dist[skeleton]
    radii = radii[radii > 0]
    if radii.size == 0:
        return 0.0
    return float(2.0 * np.median(radii))


def estimate_spur_threshold(stroke_width_px: float, factor: float = 1.4) -> float:
    """スパー（ヒゲ）除去の長さ閾値をストローク幅から自動算出する。

    検証済み実測値（512pxキャンバス・420ptフォント、ストローク幅目安約20px）で
    閾値28px程度が良好だったことから、factor≈1.4を初期値とする。
    """
    if stroke_width_px <= 0:
        return 0.0
    return factor * stroke_width_px


def estimate_merge_radius(stroke_width_px: float, factor: float = 0.85) -> float:
    """交差点集約（junction merge）の半径をストローク幅から自動算出する。

    factor≈0.75〜1.0を目安とし、初期値0.85とする。
    """
    if stroke_width_px <= 0:
        return 0.0
    return factor * stroke_width_px
