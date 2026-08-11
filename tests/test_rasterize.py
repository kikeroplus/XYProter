import numpy as np

from xyproter.rasterize import rasterize_glyph, rasterize_grid


def test_rasterize_glyph_produces_ink(jp_font_path):
    raster = rasterize_glyph(jp_font_path, "一", 420.0, (512, 512))
    assert raster.binary.dtype == bool
    assert raster.binary.shape == (512, 512)
    assert raster.binary.any()


def test_rasterize_glyph_horizontal_wider_than_tall_for_ichi(jp_font_path):
    # 「一」は横棒1本なので、インクのbboxは横長になるはず
    raster = rasterize_glyph(jp_font_path, "一", 420.0, (512, 512))
    ys, xs = np.where(raster.binary)
    bbox_w = xs.max() - xs.min()
    bbox_h = ys.max() - ys.min()
    assert bbox_w > bbox_h


def test_rasterize_grid_sets_cell_origin(jp_font_path):
    rasters = rasterize_grid(jp_font_path, "永語\nあア", 300.0, (256, 256))
    assert len(rasters) == 4
    origins = {r.char: r.cell_origin_px for r in rasters}
    assert origins["永"] == (0, 0)
    assert origins["語"] == (0, 256)
    assert origins["あ"] == (256, 0)
    assert origins["ア"] == (256, 256)


def test_rasterize_grid_letter_spacing_factor_shrinks_column_pitch(jp_font_path):
    rasters = rasterize_grid(jp_font_path, "永語", 300.0, (256, 256), letter_spacing_factor=0.5)
    origins = {r.char: r.cell_origin_px for r in rasters}
    assert origins["永"] == (0, 0)
    assert origins["語"] == (0, 128)  # 256px * 0.5


def test_rasterize_ttc_font_index_selects_different_face():
    # meiryo.ttc は index0=Meiryo, index1=Meiryo UI のように複数フェイスを持つ
    from pathlib import Path

    path = r"C:\Windows\Fonts\meiryo.ttc"
    if not Path(path).exists():
        import pytest

        pytest.skip("meiryo.ttc が見つかりません")
    raster0 = rasterize_glyph(path, "A", 300.0, (256, 256), font_index=0)
    raster1 = rasterize_glyph(path, "A", 300.0, (256, 256), font_index=1)
    assert raster0.binary.any()
    assert raster1.binary.any()
