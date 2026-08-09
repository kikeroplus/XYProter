"""1文字を選び、各パイプラインステージの画像を並べて目視確認する開発用ツール。

使用例:
    python scripts/visualize_stage.py --char 永 --font "C:/Windows/Fonts/msmincho.ttc" --out out.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyproter.path_extraction import extract_trails, order_trails_nearest_neighbor  # noqa: E402
from xyproter.pipeline import PipelineConfig, process_glyph  # noqa: E402
from xyproter.preview import render_stage_panels  # noqa: E402
from xyproter.rasterize import rasterize_glyph  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="1文字の各パイプラインステージを可視化する")
    parser.add_argument("--char", required=True, help="描画する1文字")
    parser.add_argument("--font", required=True, help="フォントファイルパス(.ttf/.ttc/.otf)")
    parser.add_argument("--font-index", type=int, default=0, help=".ttcコレクション内のフェイスindex")
    parser.add_argument("--font-size", type=float, default=420.0)
    parser.add_argument("--canvas", type=int, default=512, help="正方形キャンバスの一辺(px)")
    parser.add_argument("--pen-width-px", type=float, default=6.0)
    parser.add_argument("--merge-radius-px", type=float, default=None)
    parser.add_argument("--spur-threshold-px", type=float, default=None)
    parser.add_argument("--out", type=Path, default=None, help="指定時はウィンドウ表示せずファイル保存")
    args = parser.parse_args()

    config = PipelineConfig(
        font_path=Path(args.font),
        font_index=args.font_index,
        font_size_pt=args.font_size,
        cell_px=(args.canvas, args.canvas),
        spur_threshold_px=args.spur_threshold_px,
        merge_radius_px=args.merge_radius_px,
    )
    raster = rasterize_glyph(
        args.font, args.char, args.font_size, (args.canvas, args.canvas), font_index=args.font_index
    )
    result = process_glyph(raster, config)
    trails = order_trails_nearest_neighbor(extract_trails(result.graph_merged))

    sr = result.skeleton_result
    print(f"文字: {args.char}")
    print(f"ストローク幅推定: {sr.stroke_width_px:.2f}px")
    print(f"スパー除去閾値: {sr.spur_threshold_px:.2f}px")
    print(f"連結成分数(除去前->後): {sr.n_components_before} -> {sr.n_components_after}")
    if sr.warnings:
        print("[警告]", *sr.warnings)
    if result.merge_warnings:
        print("[警告]", *result.merge_warnings)
    print(f"ノード数(統合前->後): {len(result.graph_raw.nodes)} -> {len(result.graph_merged.nodes)}")
    print(f"パス数: {len(trails)}")

    fig = render_stage_panels(result, trails, pen_width_px=args.pen_width_px)
    if args.out:
        fig.savefig(args.out, dpi=150)
        print(f"保存しました: {args.out}")
    else:
        import matplotlib.pyplot as plt

        plt.show()


if __name__ == "__main__":
    main()
