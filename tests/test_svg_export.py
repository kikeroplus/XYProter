import numpy as np

from xyproter.svg_export import build_svg
from xyproter.types import PlotJob, PlotStats, Polyline


def _small_job() -> PlotJob:
    polylines = [
        Polyline(points=np.array([[0.0, 0.0], [10.0, 0.0]])),
        Polyline(points=np.array([[5.0, 5.0], [5.0, 15.0]])),
    ]
    stats = PlotStats(n_paths=2, total_draw_distance=20.0, total_travel_distance=5.0, n_pen_lifts=2)
    return PlotJob(polylines=polylines, canvas_size_mm=(50.0, 50.0), stats=stats)


def test_svg_polyline_count_matches_n_paths():
    job = _small_job()
    svg = build_svg(job)
    assert svg.count("<polyline") == job.stats.n_paths
    assert "<svg" in svg and "</svg>" in svg


def test_svg_point_trail_renders_as_circle():
    job = PlotJob(
        polylines=[Polyline(points=np.array([[5.0, 5.0]]))],
        canvas_size_mm=(50.0, 50.0),
        stats=PlotStats(n_paths=1, total_draw_distance=0.0, total_travel_distance=0.0, n_pen_lifts=1),
    )
    svg = build_svg(job)
    assert "<circle" in svg
    assert "<polyline" not in svg
