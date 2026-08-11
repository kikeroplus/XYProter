import numpy as np

from xyproter.grbl_sender import GrblConnection, GrblError, check_xy_bounds, parse_status
from xyproter.types import PlotJob, PlotStats, Polyline


class _FakeSerial:
    """send_line()の同期プロトコルだけをテストするための最小限のシリアルもどき。"""

    def __init__(self, responses: list[bytes] | None = None):
        self.sent: list[str] = []
        self._responses = list(responses) if responses is not None else []

    def write(self, data: bytes) -> None:
        self.sent.append(data.decode())

    def readline(self) -> bytes:
        if self._responses:
            return self._responses.pop(0)
        return b"ok\n"

    def reset_input_buffer(self) -> None:
        pass


def _job_with_points(points: list[tuple[float, float]]) -> PlotJob:
    poly = Polyline(points=np.array(points, dtype=float))
    stats = PlotStats(n_paths=1, total_draw_distance=0.0, total_travel_distance=0.0, n_pen_lifts=1)
    return PlotJob(polylines=[poly], canvas_size_mm=(200.0, 200.0), stats=stats)


def test_check_xy_bounds_within_range_has_no_violations():
    # 原点(0,0)=先頭文字の左上、Yは0以下(下方向)が正常範囲
    job = _job_with_points([[0.0, 0.0], [50.0, -100.0], [199.9, -199.9]])
    assert check_xy_bounds(job, max_x=200.0, max_y=200.0) == []


def test_check_xy_bounds_detects_x_overflow():
    job = _job_with_points([[0.0, 0.0], [250.0, -10.0]])
    violations = check_xy_bounds(job, max_x=200.0, max_y=200.0)
    assert len(violations) == 1
    assert "X座標" in violations[0]


def test_check_xy_bounds_detects_negative_x_coordinate():
    job = _job_with_points([[-5.0, 0.0], [10.0, -10.0]])
    violations = check_xy_bounds(job, max_x=200.0, max_y=200.0)
    assert len(violations) == 1
    assert "X座標" in violations[0]


def test_check_xy_bounds_detects_positive_y_coordinate():
    # Y>0(原点より上)は書き始めの文字より上にはみ出すので範囲外
    job = _job_with_points([[0.0, 0.0], [10.0, 5.0]])
    violations = check_xy_bounds(job, max_x=200.0, max_y=200.0)
    assert len(violations) == 1
    assert "Y座標" in violations[0]


def test_check_xy_bounds_detects_both_axes():
    job = _job_with_points([[0.0, 0.0], [250.0, -300.0]])
    violations = check_xy_bounds(job, max_x=200.0, max_y=200.0)
    assert len(violations) == 2


def test_zero_work_origin_sends_g92_regardless_of_prior_offset():
    conn = GrblConnection("COM_FAKE")
    conn._ser = _FakeSerial()
    resp = conn.zero_work_origin()
    assert resp == "ok"
    assert conn._ser.sent == ["G92 X0 Y0 Z0\n"]


def test_send_line_raises_on_error_response():
    conn = GrblConnection("COM_FAKE")
    conn._ser = _FakeSerial(responses=[b"error:9\n"])
    try:
        conn.send_line("G0 X99999")
        assert False, "GrblErrorが送出されるはず"
    except GrblError as e:
        assert "error:9" in str(e)


def test_send_line_retries_through_transient_empty_responses():
    conn = GrblConnection("COM_FAKE", timeout=2.0, response_timeout=10.0)
    conn._ser = _FakeSerial(responses=[b"", b"", b"ok\n"])
    assert conn.send_line("G1 X10") == "ok"


def test_send_line_raises_after_exceeding_response_timeout():
    conn = GrblConnection("COM_FAKE", timeout=2.0, response_timeout=5.0)
    conn._ser = _FakeSerial(responses=[b"", b"", b"", b"ok\n"])  # 3回空応答 = 6.0秒 > 5.0秒
    try:
        conn.send_line("G1 X10")
        assert False, "GrblErrorが送出されるはず"
    except GrblError as e:
        assert "タイムアウト" in str(e)


def test_send_line_raises_on_alarm_response():
    conn = GrblConnection("COM_FAKE")
    conn._ser = _FakeSerial(responses=[b"ALARM:1\n"])
    try:
        conn.send_line("$H")
        assert False, "GrblErrorが送出されるはず"
    except GrblError as e:
        assert "ALARM" in str(e)


def test_parse_status_with_wco():
    line = "<Idle|MPos:1.000,2.000,3.000|FS:0,0|WCO:-180.000,-240.000,0.000>"
    parsed = parse_status(line)
    assert parsed["state"] == "Idle"
    assert parsed["machine_position"] == (1.0, 2.0, 3.0)
    assert parsed["work_offset"] == (-180.0, -240.0, 0.0)


def test_parse_status_without_wco():
    line = "<Run|MPos:1.000,2.000,3.000|FS:100,0>"
    parsed = parse_status(line)
    assert parsed["state"] == "Run"
    assert parsed["machine_position"] == (1.0, 2.0, 3.0)
    assert parsed["work_offset"] is None


def test_parse_status_invalid_line_returns_none_fields():
    parsed = parse_status("garbage")
    assert parsed["state"] is None
    assert parsed["machine_position"] is None
    assert parsed["work_offset"] is None


def test_feed_hold_sends_bang_realtime_command():
    conn = GrblConnection("COM_FAKE")
    conn._ser = _FakeSerial()
    conn.feed_hold()
    assert conn._ser.sent == ["!"]


def test_cycle_resume_sends_tilde_realtime_command():
    conn = GrblConnection("COM_FAKE")
    conn._ser = _FakeSerial()
    conn.cycle_resume()
    assert conn._ser.sent == ["~"]


def test_soft_reset_sends_ctrl_x(monkeypatch):
    import xyproter.grbl_sender as grbl_sender

    monkeypatch.setattr(grbl_sender.time, "sleep", lambda s: None)
    conn = GrblConnection("COM_FAKE")
    conn._ser = _FakeSerial()
    conn.soft_reset()
    assert conn._ser.sent == ["\x18"]
