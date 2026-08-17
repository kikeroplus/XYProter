import numpy as np

from xyproter.gcode_export import build_gcode_lines, build_outline_check_gcode
from xyproter.pen_control import CustomCommandPenController, ZAxisPenController
from xyproter.types import PlotJob, PlotStats, Polyline


def _small_job(next_line_start_mm: tuple[float, float] = (0.0, -20.0)) -> PlotJob:
    polylines = [
        Polyline(points=np.array([[0.0, 0.0], [10.0, 0.0]])),
        Polyline(points=np.array([[5.0, 5.0], [5.0, 15.0]])),
    ]
    stats = PlotStats(n_paths=2, total_draw_distance=20.0, total_travel_distance=5.0, n_pen_lifts=2)
    return PlotJob(
        polylines=polylines,
        canvas_size_mm=(50.0, 50.0),
        stats=stats,
        next_line_start_mm=next_line_start_mm,
    )


def test_gcode_pen_up_down_count_matches_n_paths():
    job = _small_job()
    pen = CustomCommandPenController(up_cmds=["PENUP"], down_cmds=["PENDOWN"])
    lines = build_gcode_lines(job, pen=pen)

    n_down = sum(1 for line in lines if line == "PENDOWN")
    n_up_total = sum(1 for line in lines if line == "PENUP")
    # pen_up は各パス終了時 + 冒頭の初期上げで n_paths+1 回呼ばれる
    assert n_down == job.stats.n_paths
    assert n_up_total == job.stats.n_paths + 1


def test_gcode_contains_all_polyline_points():
    job = _small_job()
    lines = build_gcode_lines(job, pen=ZAxisPenController())
    text = "\n".join(lines)
    assert "X0.000 Y0.000" in text
    assert "X10.000 Y0.000" in text
    assert "X5.000 Y15.000" in text


def test_gcode_advances_to_next_line_start_after_teardown_by_default():
    job = _small_job(next_line_start_mm=(0.0, -20.0))
    pen = CustomCommandPenController(
        up_cmds=["PENUP"], down_cmds=["PENDOWN"], teardown_cmds=["TEARDOWN"]
    )
    lines = build_gcode_lines(job, pen=pen)
    # 末尾は teardown -> 移動F指定 -> 次の行の先頭への移動、の順になるはず
    assert lines[-3] == "TEARDOWN"
    assert lines[-1] == "G0 X0.000 Y-20.000"


def test_gcode_advance_to_next_line_can_be_disabled():
    job = _small_job(next_line_start_mm=(0.0, -20.0))
    lines = build_gcode_lines(job, pen=ZAxisPenController(), advance_to_next_line=False)
    assert lines[-1] != "G0 X0.000 Y-20.000"


def test_gcode_is_placeholder_swappable_via_pen_controller():
    job = _small_job()
    custom = CustomCommandPenController(up_cmds=["CUSTOM_UP"], down_cmds=["CUSTOM_DOWN"])
    lines = build_gcode_lines(job, pen=custom)
    assert "CUSTOM_UP" in lines
    assert "CUSTOM_DOWN" in lines
    assert not any("M280" in line or "M42" in line for line in lines)


def test_outline_check_gcode_has_no_z_commands():
    # 原点(0,0)=文章の左上、矩形はX+(右)・Y-(下)方向へ展開する
    lines = build_outline_check_gcode(30.0, 40.0, feed_rate=100.0)
    assert not any("Z" in line for line in lines)
    assert "G1 X0.000 Y0.000" in lines
    assert "G1 X30.000 Y0.000" in lines
    assert "G1 X30.000 Y-40.000" in lines
    assert "G1 X0.000 Y-40.000" in lines
    # 最初に(0,0)へ移動し、最後にも(0,0)に戻って矩形を閉じる
    assert lines.count("G1 X0.000 Y0.000") == 2
