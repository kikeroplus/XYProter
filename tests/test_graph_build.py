import numpy as np

from xyproter.graph_build import build_skeleton_graph


def test_straight_line_has_two_endpoints_and_one_edge():
    skel = np.zeros((20, 20), dtype=bool)
    skel[10, 2:18] = True
    graph = build_skeleton_graph(skel)
    assert len(graph.nodes) == 2
    assert all(n.role == "endpoint" for n in graph.nodes.values())
    assert len(graph.edges) == 1
    for n in graph.nodes:
        assert graph.degree(n) == 1


def test_t_junction_has_one_junction_three_endpoints():
    skel = np.zeros((30, 30), dtype=bool)
    skel[15, 5:26] = True  # 横棒 (row15, col5..25)
    skel[0:15, 15] = True  # 縦棒 (col15, row0..14), row15,col15で横棒と接続
    graph = build_skeleton_graph(skel)

    roles = sorted(n.role for n in graph.nodes.values())
    assert roles == ["endpoint", "endpoint", "endpoint", "junction"]
    assert len(graph.edges) == 3

    junction_id = next(nid for nid, n in graph.nodes.items() if n.role == "junction")
    assert graph.degree(junction_id) == 3


def test_cross_shape_has_one_junction_degree_four():
    skel = np.zeros((30, 30), dtype=bool)
    skel[15, 5:26] = True  # 横棒
    skel[5:26, 15] = True  # 縦棒（横棒の中央で交差）
    graph = build_skeleton_graph(skel)

    roles = sorted(n.role for n in graph.nodes.values())
    assert roles == ["endpoint", "endpoint", "endpoint", "endpoint", "junction"]
    assert len(graph.edges) == 4
    junction_id = next(nid for nid, n in graph.nodes.items() if n.role == "junction")
    assert graph.degree(junction_id) == 4


def test_pure_closed_loop_gets_synthetic_node_and_self_loop_edge(pure_loop_skeleton):
    skel = pure_loop_skeleton
    graph = build_skeleton_graph(skel)

    assert len(graph.nodes) == 1
    node = next(iter(graph.nodes.values()))
    assert node.role == "synthetic"
    assert len(graph.edges) == 1
    edge = next(iter(graph.edges.values()))
    assert edge.node_a == edge.node_b == node.id
    # ループ全周分の画素数を辿っているはず（始点重複込みでほぼ全周+1）
    assert len(edge.pixels) >= int(skel.sum())


def test_isolated_pixel_becomes_node_with_no_edges():
    skel = np.zeros((10, 10), dtype=bool)
    skel[3, 3] = True
    graph = build_skeleton_graph(skel)
    assert len(graph.nodes) == 1
    node = next(iter(graph.nodes.values()))
    assert node.role == "endpoint"
    assert len(graph.edges) == 0


def test_empty_skeleton_yields_empty_graph():
    skel = np.zeros((10, 10), dtype=bool)
    graph = build_skeleton_graph(skel)
    assert graph.nodes == {}
    assert graph.edges == {}
