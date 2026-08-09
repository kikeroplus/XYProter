"""ステージ5: パス（ポリライン）抽出とペンアップ移動最小化。

厳密な中国人郵便配達問題（奇数次数頂点の最小重みマッチング）は実装せず、
「各エッジを一度だけ描く」制約の下で貪欲にトレイル（連続描画区間）へ
分割するヒューリスティックを使う。奇数次数ノードから開始することで
オイラー路が存在する場合はペンアップ0で1本にまとまる。
"""
from __future__ import annotations

import math

import numpy as np

from .graph_build import edge_points
from .types import PlotStats, Polyline, SkeletonEdge, SkeletonGraph


def _connected_components(graph: SkeletonGraph) -> list[set[int]]:
    parent = {n: n for n in graph.nodes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in graph.edges.values():
        union(e.node_a, e.node_b)

    groups: dict[int, set[int]] = {}
    for n in graph.nodes:
        groups.setdefault(find(n), set()).add(n)
    return list(groups.values())


def _pick_start_node(
    comp_nodes: set[int], comp_edges: list[SkeletonEdge], used_edges: set[int]
) -> int:
    degree = {n: 0 for n in comp_nodes}
    for e in comp_edges:
        if e.id in used_edges:
            continue
        degree[e.node_a] += 1
        degree[e.node_b] += 1
    odd = [n for n, d in degree.items() if d > 0 and d % 2 == 1]
    if odd:
        return odd[0]
    any_active = [n for n, d in degree.items() if d > 0]
    return any_active[0]


def _oriented_points(graph: SkeletonGraph, edge: SkeletonEdge, cur: int) -> tuple[np.ndarray, int]:
    pts = edge_points(edge).copy()
    if edge.node_a == cur:
        start_node, end_node = edge.node_a, edge.node_b
    else:
        pts = pts[::-1].copy()
        start_node, end_node = edge.node_b, edge.node_a
    pts[0] = graph.nodes[start_node].position
    pts[-1] = graph.nodes[end_node].position
    return pts, end_node


def _walk_trail(
    graph: SkeletonGraph, comp_edges: list[SkeletonEdge], used_edges: set[int], start: int
) -> Polyline:
    cur = start
    points: np.ndarray | None = None
    edge_ids: list[int] = []
    while True:
        avail = [e for e in comp_edges if e.id not in used_edges and (e.node_a == cur or e.node_b == cur)]
        if not avail:
            break
        e = avail[0]
        used_edges.add(e.id)
        edge_ids.append(e.id)
        pts, nxt = _oriented_points(graph, e, cur)
        points = pts if points is None else np.vstack([points, pts[1:]])
        cur = nxt
    assert points is not None
    closed = start == cur and len(points) > 2
    return Polyline(points=points, closed=closed, source_edge_ids=edge_ids)


def _point_trail(graph: SkeletonGraph, node_id: int) -> Polyline:
    pos = graph.nodes[node_id].position
    return Polyline(points=np.array([pos], dtype=float), closed=False, source_edge_ids=[])


def extract_trails(graph: SkeletonGraph) -> list[Polyline]:
    """グラフを、各エッジをちょうど1回描画するトレイル群に分解する。"""
    if not graph.nodes:
        return []

    used_edges: set[int] = set()
    trails: list[Polyline] = []

    for comp_nodes in _connected_components(graph):
        comp_edges = [e for e in graph.edges.values() if e.node_a in comp_nodes]
        if not comp_edges:
            for n in comp_nodes:
                trails.append(_point_trail(graph, n))
            continue
        while any(e.id not in used_edges for e in comp_edges):
            start = _pick_start_node(comp_nodes, comp_edges, used_edges)
            trails.append(_walk_trail(graph, comp_edges, used_edges, start))

    return trails


def order_trails_nearest_neighbor(
    trails: list[Polyline], start_pos: tuple[float, float] = (0.0, 0.0)
) -> list[Polyline]:
    """最近傍法でトレイルの描画順を決め、ペンアップ移動を減らす。

    各トレイルは閉ループでなければ両端どちらからでも開始できるとみなす。
    """
    remaining = list(trails)
    ordered: list[Polyline] = []
    cur = np.array(start_pos, dtype=float)

    while remaining:
        best_idx = -1
        best_reversed = False
        best_d = math.inf
        for i, t in enumerate(remaining):
            d_fwd = float(np.hypot(*(t.points[0] - cur)))
            if d_fwd < best_d:
                best_d, best_idx, best_reversed = d_fwd, i, False
            if len(t.points) > 1 and not t.closed:
                d_rev = float(np.hypot(*(t.points[-1] - cur)))
                if d_rev < best_d:
                    best_d, best_idx, best_reversed = d_rev, i, True
        chosen = remaining.pop(best_idx)
        if best_reversed:
            chosen = chosen.reversed()
        ordered.append(chosen)
        cur = chosen.points[-1]

    return ordered


def compute_stats(ordered_trails: list[Polyline], start_pos: tuple[float, float] = (0.0, 0.0)) -> PlotStats:
    n_paths = len(ordered_trails)
    total_draw = sum(t.length() for t in ordered_trails)
    total_travel = 0.0
    cur = np.array(start_pos, dtype=float)
    for t in ordered_trails:
        total_travel += float(np.hypot(*(t.points[0] - cur)))
        cur = t.points[-1]
    return PlotStats(
        n_paths=n_paths,
        total_draw_distance=total_draw,
        total_travel_distance=total_travel,
        n_pen_lifts=n_paths,
    )


def extract_and_order_paths(
    graph: SkeletonGraph, start_pos: tuple[float, float] = (0.0, 0.0)
) -> tuple[list[Polyline], PlotStats]:
    trails = extract_trails(graph)
    ordered = order_trails_nearest_neighbor(trails, start_pos)
    stats = compute_stats(ordered, start_pos)
    return ordered, stats
