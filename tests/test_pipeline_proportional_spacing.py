from pathlib import Path

from xyproter.pipeline import PipelineConfig, run_text_pipeline


def test_proportional_spacing_narrower_than_equal_width(jp_font_path):
    # 「永i」を等幅/プロポーショナルの両モードで通し、プロポーショナルの方が
    # 全角+半角混在で全体の描画幅が狭くなる(=canvas_size_mm[0]がより小さいまま
    # 使われず、grid_colsが小さくなる分px_to_mmが上がる)ことを確認する。
    base_kwargs = dict(
        font_path=Path(jp_font_path),
        font_size_pt=200.0,
        cell_px=(256, 256),
        canvas_size_mm=(100.0, 100.0),
    )
    _, job_equal = run_text_pipeline("永i", PipelineConfig(**base_kwargs))
    _, job_prop = run_text_pipeline(
        "永i", PipelineConfig(**base_kwargs, proportional_spacing=True)
    )

    assert job_equal.canvas_size_mm[0] == 100.0
    assert job_prop.canvas_size_mm[0] == 100.0
    # 実際に文字が占めるpx幅(grid_cols)は半角文字がある分プロポーショナルの方が
    # 狭いはずなので、同じ100mmに引き伸ばした結果、px_to_mm(=文字の実サイズ)は
    # プロポーショナルの方が大きくなり、描画距離も変わる。
    assert job_prop.stats.total_draw_distance != job_equal.stats.total_draw_distance


def test_proportional_spacing_polylines_stay_within_canvas(jp_font_path):
    config = PipelineConfig(
        font_path=Path(jp_font_path),
        font_size_pt=200.0,
        cell_px=(256, 256),
        canvas_size_mm=(100.0, 100.0),
        proportional_spacing=True,
    )
    _, job = run_text_pipeline("永語i", config)
    assert job.polylines
    xs = [p for poly in job.polylines for p in poly.points[:, 0]]
    assert min(xs) >= -1e-6
    assert max(xs) <= job.canvas_size_mm[0] + 1e-6
