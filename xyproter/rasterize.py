"""ステージ1: フォントグリフのラスタライズ。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .types import GlyphRaster


def load_font(font_path: str | Path, font_size_pt: float, font_index: int = 0) -> ImageFont.FreeTypeFont:
    """フォントを読み込む。.ttc コレクションの場合は font_index でフェイスを選択する。"""
    return ImageFont.truetype(str(font_path), size=round(font_size_pt), index=font_index)


def rasterize_glyph(
    font_path: str | Path,
    char: str,
    font_size_pt: float,
    canvas_px: tuple[int, int],
    font_index: int = 0,
    threshold: int = 128,
) -> GlyphRaster:
    """1文字をラスタライズしキャンバス中央に配置、二値化する。

    Args:
        font_path: .ttf/.ttc/.otf のパス
        char: 描画する1文字（複数文字を渡した場合はまとめて1グリフ画像として扱われる）
        font_size_pt: フォントサイズ（pt、実質はPillowのピクセルサイズとして扱う）
        canvas_px: (width, height)
        font_index: .ttc コレクション内のフェイスインデックス
        threshold: 二値化閾値（0-255グレースケールに対して）
    """
    font = load_font(font_path, font_size_pt, font_index)
    width, height = canvas_px
    image = Image.new("L", (width, height), color=0)
    draw = ImageDraw.Draw(image)

    # インクのbbox基準ではなく、フォントの全角ボディ(advance幅×ascent+descent高さ)を
    # セル中央に配置する。bbox中央揃えだと「。」「、」のようにインクが小さく
    # 偏った文字は、本来デザイン上は左下寄りのはずがセル中央に来てしまう。
    ascent, descent = font.getmetrics()
    advance = draw.textlength(char, font=font)
    origin_x = (width - advance) / 2
    origin_y = (height - (ascent + descent)) / 2

    draw.text((origin_x, origin_y), char, font=font, fill=255)

    arr = np.array(image)
    binary = arr >= threshold

    return GlyphRaster(
        char=char,
        binary=binary,
        canvas_px=(width, height),
        font_path=Path(font_path),
        font_index=font_index,
        font_size_pt=font_size_pt,
    )


def rasterize_grid(
    font_path: str | Path,
    text: str,
    font_size_pt: float,
    cell_px: tuple[int, int],
    columns: int | None = None,
    font_index: int = 0,
    threshold: int = 128,
    letter_spacing_factor: float = 1.0,
) -> list[GlyphRaster]:
    """複数文字をそれぞれラスタライズし、グリッド配置用の cell_origin_px を付与する。

    改行 `\\n` はグリッドの行区切りとして扱う。letter_spacing_factorは列方向の
    配置ピッチをセル幅(cell_w)に対する倍率で調整する（既定1.0=セル幅そのまま、
    1未満で文字間を詰める）。文字自体の描画サイズ(cell_px, font_size_pt)には影響しない。
    """
    lines = text.split("\n")
    if columns is None:
        columns = max((len(line) for line in lines), default=1)

    rasters: list[GlyphRaster] = []
    cell_w, cell_h = cell_px
    col_pitch = cell_w * letter_spacing_factor
    for row_idx, line in enumerate(lines):
        for col_idx, ch in enumerate(line):
            raster = rasterize_glyph(
                font_path, ch, font_size_pt, cell_px, font_index=font_index, threshold=threshold
            )
            raster.cell_origin_px = (row_idx * cell_h, col_idx * col_pitch)
            rasters.append(raster)
    return rasters
