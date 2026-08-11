import numpy as np

from xyproter.graph_build import build_skeleton_graph
from xyproter.intersection_merge import check_merge_safety, merge_close_junctions
from xyproter.metrics import estimate_merge_radius, estimate_stroke_width
from xyproter.skeletonize_stage import skeletonize_glyph
from xyproter.types import SkeletonEdge, SkeletonGraph, SkeletonNode


def test_merge_does_not_touch_endpoints_even_if_close():
    nodes = {
        1: SkeletonNode(id=1, pixels=[(0, 0)], position=(0.0, 0.0), role="junction"),
        2: SkeletonNode(id=2, pixels=[(0, 2)], position=(0.0, 2.0), role="junction"),
        3: SkeletonNode(id=3, pixels=[(0, -2)], position=(0.0, -2.0), role="endpoint"),
    }
    edges = {
        1: SkeletonEdge(id=1, node_a=1, node_b=2, pixels=[(0, 0), (0, 1), (0, 2)], length_px=2.0),
        2: SkeletonEdge(id=2, node_a=1, node_b=3, pixels=[(0, 0), (0, -1), (0, -2)], length_px=2.0),
    }
    graph = SkeletonGraph(nodes=nodes, edges=edges, image_shape=(10, 10))

    merged = merge_close_junctions(graph, radius_px=3.0)

    assert len(merged.nodes) == 2  # J1,J2は統合、E1は独立のまま
    roles = sorted(n.role for n in merged.nodes.values())
    assert roles == ["endpoint", "junction"]
    endpoint = next(n for n in merged.nodes.values() if n.role == "endpoint")
    assert endpoint.position == (0.0, -2.0)


def test_merge_transitively_collapses_chain_of_junctions():
    # J1-J2-J3 が数珠つなぎに近接している場合、1パスで1ノードに統合される。
    # 統合前のエッジは実画素列(輪の一部等になり得る)を表すため、破棄せず
    # 自己ループとして残す(破棄すると「な」「ま」の結びの丸が消えるバグになる)。
    nodes = {
        1: SkeletonNode(id=1, pixels=[(0, 0)], position=(0.0, 0.0), role="junction"),
        2: SkeletonNode(id=2, pixels=[(0, 2)], position=(0.0, 2.0), role="junction"),
        3: SkeletonNode(id=3, pixels=[(0, 4)], position=(0.0, 4.0), role="junction"),
    }
    edges = {
        1: SkeletonEdge(id=1, node_a=1, node_b=2, pixels=[(0, 0), (0, 1), (0, 2)], length_px=2.0),
        2: SkeletonEdge(id=2, node_a=2, node_b=3, pixels=[(0, 2), (0, 3), (0, 4)], length_px=2.0),
    }
    graph = SkeletonGraph(nodes=nodes, edges=edges, image_shape=(10, 10))

    merged = merge_close_junctions(graph, radius_px=3.0)

    assert len(merged.nodes) == 1
    assert len(merged.edges) == 2
    assert all(e.node_a == e.node_b for e in merged.edges.values())


def test_merge_on_real_cross_shape_yields_single_junction_degree_four():
    binary = np.zeros((60, 60), dtype=bool)
    binary[27:33, 5:55] = True  # 太い横棒
    binary[5:55, 27:33] = True  # 太い縦棒（中央で交差）
    skel = skeletonize_glyph(binary, clean_noise=False)
    graph = build_skeleton_graph(skel)

    stroke_width = estimate_stroke_width(binary, skel)
    radius = estimate_merge_radius(stroke_width)
    merged = merge_close_junctions(graph, radius)

    junctions = [n for n in merged.nodes.values() if n.role == "junction"]
    endpoints = [n for n in merged.nodes.values() if n.role == "endpoint"]
    assert len(junctions) == 1
    assert merged.degree(junctions[0].id) == 4
    assert len(endpoints) == 4

    assert check_merge_safety(graph, merged) == []


def test_merge_radius_zero_is_noop():
    binary = np.zeros((60, 60), dtype=bool)
    binary[27:33, 5:55] = True
    binary[5:55, 27:33] = True
    skel = skeletonize_glyph(binary, clean_noise=False)
    graph = build_skeleton_graph(skel)
    merged = merge_close_junctions(graph, radius_px=0.0)
    assert len(merged.nodes) == len(graph.nodes)
    assert len(merged.edges) == len(graph.edges)
