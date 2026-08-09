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

    bbox = draw.textbbox((0, 0), char, font=font)
    glyph_w = bbox[2] - bbox[0]
    glyph_h = bbox[3] - bbox[1]
    origin_x = (width - glyph_w) / 2 - bbox[0]
    origin_y = (height - glyph_h) / 2 - bbox[1]

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
) -> list[GlyphRaster]:
    """複数文字をそれぞれラスタライズし、グリッド配置用の cell_origin_px を付与する。

    改行 `\\n` はグリッドの行区切りとして扱う。
    """
    lines = text.split("\n")
    if columns is None:
        columns = max((len(line) for line in lines), default=1)

    rasters: list[GlyphRaster] = []
    cell_w, cell_h = cell_px
    for row_idx, line in enumerate(lines):
        for col_idx, ch in enumerate(line):
            raster = rasterize_glyph(
                font_path, ch, font_size_pt, cell_px, font_index=font_index, threshold=threshold
            )
            raster.cell_origin_px = (row_idx * cell_h, col_idx * cell_w)
            rasters.append(raster)
    return rasters
