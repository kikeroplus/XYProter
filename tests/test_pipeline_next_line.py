from pathlib import Path

from xyproter.pipeline import PipelineConfig, run_text_pipeline


def test_next_line_start_mm_is_below_last_line(jp_font_path):
    config = PipelineConfig(
        font_path=Path(jp_font_path),
        font_size_pt=200.0,
        cell_px=(256, 256),
        canvas_size_mm=(100.0, 100.0),
    )
    _, job = run_text_pipeline("永\n語", config)

    x, y = job.next_line_start_mm
    assert x == 0.0
    # 2行分書いたので、次の行の先頭は2セル分下(mm換算)にあるはず
    px_to_mm = job.canvas_size_mm[0] / 256
    assert y == -2 * 256 * px_to_mm


def test_next_line_start_mm_respects_line_spacing_factor(jp_font_path):
    config = PipelineConfig(
        font_path=Path(jp_font_path),
        font_size_pt=200.0,
        cell_px=(256, 256),
        canvas_size_mm=(100.0, 100.0),
        line_spacing_factor=1.5,
    )
    _, job = run_text_pipeline("永\n語", config)

    x, y = job.next_line_start_mm
    assert x == 0.0
    px_to_mm = job.canvas_size_mm[0] / 256
    # 2行書いたので、2行目の原点(row_idx=1) + row_pitch(=256*1.5) が次の行の先頭
    row_pitch_px = 256 * 1.5
    assert y == -(2 * row_pitch_px) * px_to_mm
