from xyproter.pen_control import RelativeZPenController


def test_relative_pen_starts_up_and_first_down_moves():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0)
    pen.setup()
    down_cmds = pen.pen_down()
    assert down_cmds == ["G91", "G1 Z-3.000 F200.0", "G90"]


def test_relative_pen_repeated_down_is_noop():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0)
    pen.setup()
    pen.pen_down()
    assert pen.pen_down() == []  # 既にダウン状態なので何もしない


def test_relative_pen_up_after_down_returns_to_zero():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0)
    pen.setup()
    pen.pen_down()
    up_cmds = pen.pen_up()
    assert up_cmds == ["G91", "G1 Z3.000 F200.0", "G90"]


def test_relative_pen_up_when_already_up_is_noop():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0)
    pen.setup()
    assert pen.pen_up() == []  # 開始時点で既にアップ状態


def test_relative_pen_teardown_ensures_up():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0)
    pen.setup()
    pen.pen_down()
    teardown_cmds = pen.teardown()
    assert teardown_cmds == ["G91", "G1 Z3.000 F200.0", "G90"]
    assert pen.teardown() == []  # 既に上がっているので2回目は何もしない


def test_relative_pen_teardown_with_final_lift_adds_extra_retract():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0, final_lift_mm=10.0)
    pen.setup()
    pen.pen_down()
    teardown_cmds = pen.teardown()
    assert teardown_cmds == ["G91", "G1 Z3.000 F200.0", "G90", "G91", "G1 Z10.000 F200.0", "G90"]


def test_relative_pen_teardown_with_final_lift_when_already_up():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0, final_lift_mm=10.0)
    pen.setup()
    teardown_cmds = pen.teardown()
    assert teardown_cmds == ["G91", "G1 Z10.000 F200.0", "G90"]


def test_relative_pen_initial_extra_down_applies_only_to_first_down():
    # G92 Z0を毎回送る運用では、前回teardownの退避量が新しいZ=0になるため、
    # 最初のpen_downだけ追加で下げてズレを補正する必要がある。
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0, initial_extra_down_mm=15.0)
    pen.setup()
    assert pen.pen_down() == ["G91", "G1 Z-18.000 F200.0", "G90"]
    pen.pen_up()
    assert pen.pen_down() == ["G91", "G1 Z-3.000 F200.0", "G90"]  # 2回目以降は通常量のみ


def test_relative_pen_initial_extra_down_reapplies_after_setup():
    pen = RelativeZPenController(down_travel_mm=3.0, z_feed=200.0, initial_extra_down_mm=15.0)
    pen.setup()
    pen.pen_down()
    pen.pen_up()
    pen.setup()  # インスタンス再利用時も「最初のpen_down」として扱われる
    assert pen.pen_down() == ["G91", "G1 Z-18.000 F200.0", "G90"]
