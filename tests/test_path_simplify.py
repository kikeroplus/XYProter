import numpy as np

from xyproter.path_simplify import douglas_peucker, simplify_polyline, simplify_polylines
from xyproter.types import Polyline


def test_straight_line_collapses_to_two_points():
    points = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]])
    result = douglas_peucker(points, tolerance=0.01)
    assert len(result) == 2
    assert np.allclose(result[0], points[0])
    assert np.allclose(result[-1], points[-1])


def test_sharp_corner_is_preserved_when_tolerance_small():
    points = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 5.0], [1.0, 10.0]])
    result = douglas_peucker(points, tolerance=0.1)
    assert len(result) == 3
    assert np.allclose(result[1], [1.0, 0.0])


def test_sharp_corner_is_dropped_when_tolerance_large():
    points = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 5.0], [1.0, 10.0]])
    result = douglas_peucker(points, tolerance=100.0)
    assert len(result) == 2


def test_tolerance_zero_or_short_input_is_unchanged():
    points = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 0.5]])
    assert np.array_equal(douglas_peucker(points, tolerance=0.0), points)
    two_points = np.array([[0.0, 0.0], [1.0, 1.0]])
    assert np.array_equal(douglas_peucker(two_points, tolerance=10.0), two_points)


def test_simplify_polyline_preserves_closed_and_source_edge_ids():
    points = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    poly = Polyline(points=points, closed=True, source_edge_ids=[1, 2])
    simplified = simplify_polyline(poly, tolerance=0.01)
    assert simplified.closed is True
    assert simplified.source_edge_ids == [1, 2]
    assert len(simplified.points) == 2


def test_simplify_polylines_applies_to_each():
    poly_a = Polyline(points=np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]))
    poly_b = Polyline(points=np.array([[0.0, 0.0], [1.0, 5.0], [2.0, 0.0]]))
    result = simplify_polylines([poly_a, poly_b], tolerance=0.01)
    assert len(result[0].points) == 2  # 直線は間引かれる
    assert len(result[1].points) == 3  # 鋭角は残る
