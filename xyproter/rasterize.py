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
        advance_px=advance,
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
    line_spacing_factor: float = 1.0,
    proportional_spacing: bool = False,
) -> list[GlyphRaster]:
    """複数文字をそれぞれラスタライズし、グリッド配置用の cell_origin_px を付与する。

    改行 `\\n` はグリッドの行区切りとして扱う。letter_spacing_factorは列方向の
    配置ピッチの倍率、line_spacing_factorは行方向の配置ピッチをセル高さ(cell_h)
    に対する倍率で調整する（既定1.0=そのまま、値を大きくすると間隔が広がる）。
    文字自体の描画サイズ(cell_px, font_size_pt)には影響しないため、小さい文字を
    広い行間で配置することも、大きい文字を狭い行間で重ねて配置することもできる
    (重なりの禁止制御はしない)。

    proportional_spacing=False(既定、モード1)では、列ピッチはセル幅(cell_w)
    固定で全文字同じ幅に並ぶ(従来動作)。True(モード2、TTFらしい可変幅)では、
    各文字のフォント送り幅(advance_px、rasterize_glyphが全角ボディの中央揃えに
    既に使っている値)を列ピッチとして使い、半角文字や約物などで文字ごとに
    幅が変わるプロポーショナル配置になる。ラスタライズ自体は従来通りcell_px
    固定サイズのキャンバス中央に文字を配置するため、そのキャンバス原点
    (cell_origin_px)は「送り幅を詰めた結果のペン位置」から中央揃え分の
    パディング((cell_w-advance)/2)を引いた位置になる。
    """
    lines = text.split("\n")
    if columns is None:
        columns = max((len(line) for line in lines), default=1)

    rasters: list[GlyphRaster] = []
    cell_w, cell_h = cell_px
    col_pitch = cell_w * letter_spacing_factor
    row_pitch = cell_h * line_spacing_factor
    for row_idx, line in enumerate(lines):
        pen_x = 0.0
        for col_idx, ch in enumerate(line):
            raster = rasterize_glyph(
                font_path, ch, font_size_pt, cell_px, font_index=font_index, threshold=threshold
            )
            if proportional_spacing:
                col_origin = pen_x - (cell_w - raster.advance_px) / 2
                pen_x += raster.advance_px * letter_spacing_factor
            else:
                col_origin = col_idx * col_pitch
            raster.cell_origin_px = (row_idx * row_pitch, col_origin)
            rasters.append(raster)
    return rasters
