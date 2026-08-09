import numpy as np

from xyproter.graph_build import build_skeleton_graph
from xyproter.path_extraction import (
    compute_stats,
    extract_trails,
    order_trails_nearest_neighbor,
)


def _all_covered_edge_ids(graph, trails) -> set[int]:
    covered: set[int] = set()
    for t in trails:
        covered.update(t.source_edge_ids)
    return covered


def test_closed_loop_yields_single_trail_no_pen_lift_needed(pure_loop_skeleton):
    graph = build_skeleton_graph(pure_loop_skeleton)

    trails = extract_trails(graph)
    assert len(trails) == 1
    assert trails[0].closed
    assert set(graph.edges.keys()) == _all_covered_edge_ids(graph, trails)


def test_cross_shape_covers_every_edge_exactly_once():
    skel = np.zeros((30, 30), dtype=bool)
    skel[15, 5:26] = True
    skel[5:26, 15] = True
    graph = build_skeleton_graph(skel)

    trails = extract_trails(graph)

    used_edge_ids: list[int] = []
    for t in trails:
        used_edge_ids.extend(t.source_edge_ids)
    assert sorted(used_edge_ids) == sorted(graph.edges.keys())
    assert len(used_edge_ids) == len(set(used_edge_ids))  # 重複なし


def test_straight_line_single_trail_zero_travel():
    skel = np.zeros((20, 20), dtype=bool)
    skel[10, 2:18] = True
    graph = build_skeleton_graph(skel)
    trails = extract_trails(graph)
    assert len(trails) == 1
    ordered = order_trails_nearest_neighbor(trails, start_pos=(10.0, 2.0))
    stats = compute_stats(ordered, start_pos=(10.0, 2.0))
    assert stats.n_paths == 1
    assert stats.total_travel_distance == 0.0
    assert stats.total_draw_distance > 0.0


def test_isolated_point_becomes_zero_length_trail():
    skel = np.zeros((10, 10), dtype=bool)
    skel[3, 3] = True
    graph = build_skeleton_graph(skel)
    trails = extract_trails(graph)
    assert len(trails) == 1
    assert trails[0].length() == 0.0


def test_order_trails_nearest_neighbor_reduces_or_equal_travel():
    # 3本の離れた水平線分。始点に近い順に並べれば移動距離は素朴な順序以下になるはず
    skel = np.zeros((10, 100), dtype=bool)
    skel[5, 0:5] = True
    skel[5, 50:55] = True
    skel[5, 90:95] = True
    graph = build_skeleton_graph(skel)
    trails = extract_trails(graph)
    assert len(trails) == 3

    naive_stats = compute_stats(trails, start_pos=(0.0, 0.0))
    ordered = order_trails_nearest_neighbor(trails, start_pos=(0.0, 0.0))
    ordered_stats = compute_stats(ordered, start_pos=(0.0, 0.0))

    assert ordered_stats.total_travel_distance <= naive_stats.total_travel_distance
