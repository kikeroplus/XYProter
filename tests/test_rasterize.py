import numpy as np
import pytest

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


def _ink_start_px(raster, cell_w: float) -> float:
    """rasterize_glyphが文字を中央揃えする際のorigin_x(=(cell_w-advance)/2)を
    cell_origin_pxに足し戻した、合成画像上でのインク開始x座標。"""
    pad = (cell_w - raster.advance_px) / 2
    return raster.cell_origin_px[1] + pad


def test_rasterize_grid_proportional_spacing_uses_advance_width(jp_font_path):
    # 「永」(全角)と「i」(半角欧文)は送り幅(advance)が大きく異なるはずなので、
    # プロポーショナルモードではインクの開始位置の間隔(=実質の列ピッチ)が
    # 文字ごとに変わる。
    cell_w = 256
    rasters = rasterize_grid(
        jp_font_path, "永i", 300.0, (cell_w, cell_w), proportional_spacing=True
    )
    ei, i_char = rasters[0], rasters[1]
    assert ei.advance_px > 0
    assert i_char.advance_px > 0
    assert i_char.advance_px < ei.advance_px
    # インク開始位置の差(=実際の列ピッチ)は1文字目の送り幅と一致するはず
    ink_pitch = _ink_start_px(i_char, cell_w) - _ink_start_px(ei, cell_w)
    assert ink_pitch == pytest.approx(ei.advance_px)


def test_rasterize_grid_proportional_spacing_respects_letter_spacing_factor(jp_font_path):
    cell_w = 256
    rasters = rasterize_grid(
        jp_font_path,
        "永語",
        300.0,
        (cell_w, cell_w),
        proportional_spacing=True,
        letter_spacing_factor=0.5,
    )
    ei, go = rasters[0], rasters[1]
    ink_pitch = _ink_start_px(go, cell_w) - _ink_start_px(ei, cell_w)
    assert ink_pitch == pytest.approx(ei.advance_px * 0.5)


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
