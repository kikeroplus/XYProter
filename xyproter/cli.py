"""CLI: `python -m xyproter "永語あアABC" --font ...`

依頼書ステージ7で要求された優先度（1〜5のパイプライン品質、プレビュー画像出力）を
カバーする。G-code出力は後回しのため未対応（ステージ6b, 7の一部）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .gcode_export import export_gcode
from .pen_control import GpioPenController, PenController, ServoAnglePenController, ZAxisPenController
from .pipeline import PipelineConfig, run_text_pipeline
from .preview import render_grid_preview
from .svg_export import export_svg


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xyproter", description="XYプロッター用 日本語文字センターライン抽出パイプライン"
    )
    parser.add_argument("text", help=r"描画する文字列（複数文字可。\nで行区切り＝グリッド配置）")
    parser.add_argument("--font", required=True, help="フォントファイルパス(.ttf/.ttc/.otf)")
    parser.add_argument("--font-index", type=int, default=0, help=".ttcコレクション内のフェイスindex")
    parser.add_argument("--font-size", type=float, default=380.0, help="フォントサイズ(セルpxに対する目安pt)")
    parser.add_argument("--cell-px", type=int, default=512, help="1文字あたりの正方形セルの一辺(px)")
    parser.add_argument("--columns", type=int, default=None, help="グリッドの列数（省略時は最長行の文字数）")
    parser.add_argument(
        "--canvas-mm",
        type=float,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=(100.0, 100.0),
        help="出力キャンバスの物理サイズ(mm)",
    )
    parser.add_argument("--threshold", type=int, default=128, help="ラスタライズ二値化閾値(0-255)")
    parser.add_argument(
        "--spur-threshold-px", type=float, default=None, help="スパー除去閾値(px)。省略時は自動計算"
    )
    parser.add_argument(
        "--merge-radius-px", type=float, default=None, help="交差点集約半径(px)。省略時は自動計算"
    )
    parser.add_argument("--pen-width-mm", type=float, default=0.5, help="プレビュー用ペン幅(mm)")
    parser.add_argument("--out-dir", type=Path, default=Path("output"), help="出力先ディレクトリ")
    parser.add_argument(
        "--show-travel", action="store_true", help="プレビューにペンアップ移動線(赤点線)を表示する"
    )
    parser.add_argument("--svg", action="store_true", help="SVGファイルも出力する")
    parser.add_argument("--gcode", action="store_true", help="G-codeファイルも出力する")
    parser.add_argument(
        "--pen-mode",
        choices=["z", "servo", "gpio"],
        default="z",
        help="G-code出力時のペンアップ/ダウン制御方式（未確定のため差し替え可能）",
    )
    parser.add_argument("--feed-rate", type=float, default=1500.0, help="G-code描画時の送り速度")
    parser.add_argument("--travel-feed-rate", type=float, default=3000.0, help="G-codeペンアップ移動時の送り速度")
    return parser


def _build_pen_controller(mode: str) -> PenController:
    if mode == "z":
        return ZAxisPenController()
    if mode == "servo":
        return ServoAnglePenController()
    return GpioPenController()


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    text = args.text.replace("\\n", "\n")
    config = PipelineConfig(
        font_path=Path(args.font),
        font_index=args.font_index,
        font_size_pt=args.font_size,
        cell_px=(args.cell_px, args.cell_px),
        canvas_size_mm=tuple(args.canvas_mm),
        threshold=args.threshold,
        spur_threshold_px=args.spur_threshold_px,
        merge_radius_px=args.merge_radius_px,
        columns=args.columns,
    )

    glyph_results, job = run_text_pipeline(text, config)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"文字数: {len(glyph_results)}")
    for gr in glyph_results:
        if gr.skeleton_result.warnings:
            print(f"[警告][{gr.raster.char}]", *gr.skeleton_result.warnings)
        if gr.merge_warnings:
            print(f"[警告][{gr.raster.char}]", *gr.merge_warnings)
    print(job.stats.summary())

    fig = render_grid_preview(job, pen_width_mm=args.pen_width_mm, show_travel=args.show_travel)
    preview_path = args.out_dir / "preview.png"
    fig.savefig(preview_path, dpi=150)
    print(f"プレビュー画像を保存しました: {preview_path}")

    if args.svg:
        svg_path = args.out_dir / "output.svg"
        export_svg(job, svg_path, stroke_width_mm=args.pen_width_mm)
        print(f"SVGを保存しました: {svg_path}")

    if args.gcode:
        gcode_path = args.out_dir / "output.gcode"
        pen = _build_pen_controller(args.pen_mode)
        export_gcode(job, gcode_path, pen=pen, feed_rate=args.feed_rate, travel_feed_rate=args.travel_feed_rate)
        print(f"G-codeを保存しました: {gcode_path} (pen-mode={args.pen_mode})")


if __name__ == "__main__":
    main()
