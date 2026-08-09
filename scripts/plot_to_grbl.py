"""文字をラスタライズ〜パス抽出し、G-codeを生成してGRBLプロッターに送信する。

安全のための使い方（この順番を必ず守ること。原点復帰・ソフト/ハードリミットが
すべて無効な機体のため、電源投入時のキャリッジ位置がそのまま機械原点(0,0,0)として
扱われ、可動範囲を超えても機体側では検知できない）:

    1. まず --dry-run で実行し、output/grbl_preview.png と output/grbl_output.gcode
       の内容を必ず目視確認する。
    2. 実機の電源を入れた直後の状態で、キャリッジが可動範囲の隅（+X, +Yへの移動代が
       描画範囲以上残っている位置）にあり、ペンが紙の上・安全な待機高さにあることを
       確認する。
    3. --outline-check を付けて実行し、ペンアップのまま描画範囲の外周だけを低速で
       なぞらせ、実際に可動範囲内に収まるか目視確認する（インクは出ない）。
       異常があればすぐ電源を切って止めること。
    4. 問題なければ --outline-check を外し、--port を指定して本描画を実行する
       （確認プロンプトに y と答えると送信開始）。

例:
    python scripts/plot_to_grbl.py "永" --font "C:/Windows/Fonts/msmincho.ttc" --size-mm 30 --dry-run
    python scripts/plot_to_grbl.py "永" --font "C:/Windows/Fonts/msmincho.ttc" --size-mm 30 --port COM17 --outline-check
    python scripts/plot_to_grbl.py "永" --font "C:/Windows/Fonts/msmincho.ttc" --size-mm 30 --port COM17
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyproter.gcode_export import build_gcode_lines, build_outline_check_gcode  # noqa: E402
from xyproter.grbl_sender import GrblConnection, GrblError, check_xy_bounds  # noqa: E402
from xyproter.pen_control import RelativeZPenController  # noqa: E402
from xyproter.pipeline import PipelineConfig, run_text_pipeline  # noqa: E402
from xyproter.preview import render_grid_preview  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GRBLプロッターへ文字を描画する")
    parser.add_argument("text", help=r"描画する文字列（複数文字可。\nで行区切り＝グリッド配置）")
    parser.add_argument("--font", required=True, help="フォントファイルパス(.ttf/.ttc/.otf)")
    parser.add_argument("--font-index", type=int, default=0)
    parser.add_argument("--font-size", type=float, default=280.0, help="セルpxに対するフォントサイズ目安")
    parser.add_argument("--cell-px", type=int, default=400)
    parser.add_argument("--size-mm", type=float, default=30.0, help="1文字あたりのセルの一辺(mm)")
    parser.add_argument("--max-x", type=float, default=200.0, help="機体のX最大可動範囲(mm) $130相当")
    parser.add_argument("--max-y", type=float, default=200.0, help="機体のY最大可動範囲(mm) $131相当")
    parser.add_argument("--draw-feed", type=float, default=300.0, help="描画時送り速度(mm/min)")
    parser.add_argument("--travel-feed", type=float, default=600.0, help="ペンアップ移動速度(mm/min)")
    parser.add_argument("--z-down-mm", type=float, default=3.0, help="待機位置から紙までのZ下降量(mm)")
    parser.add_argument("--z-feed", type=float, default=200.0, help="Z軸送り速度(mm/min)")
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    parser.add_argument("--port", default=None, help="例: COM17（指定時のみ実機へ送信する）")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--dry-run", action="store_true", help="G-code生成とプレビューのみ行い、実機へは送らない"
    )
    parser.add_argument("--yes", action="store_true", help="送信前の確認プロンプトを省略する")
    parser.add_argument(
        "--outline-check",
        action="store_true",
        help="実際の描画の代わりに、ペンアップのまま描画範囲の外周だけを低速でなぞる安全確認を行う",
    )
    parser.add_argument("--outline-feed", type=float, default=100.0, help="外周確認時の送り速度(mm/min)")
    return parser


def _run_outline_check(args: argparse.Namespace, canvas_w_mm: float, canvas_h_mm: float) -> None:
    """ペンアップのまま描画範囲の外周だけを低速でなぞり、物理的な可動範囲を安全確認する。

    インクは一切出ない（Zは動かさない）。実際の文字描画を行う前に、
    電源投入位置から見てこの範囲が本当に安全かどうかを目視確認するためのモード。
    """
    print(f"[外周確認モード] 範囲: X 0-{canvas_w_mm:.1f}mm, Y 0-{canvas_h_mm:.1f}mm")
    print("ペンは動かさず(Zコマンドなし)、XYだけを外周に沿って低速で動かします。")

    lines = build_outline_check_gcode(canvas_w_mm, canvas_h_mm, feed_rate=args.outline_feed)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    gcode_path = args.out_dir / "grbl_outline_check.gcode"
    gcode_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"G-code: {gcode_path} ({len(lines)}行)")

    if args.dry_run or not args.port:
        print("ドライランのみ実行しました（実機へは送信していません）。")
        return

    print()
    print("=" * 60)
    print("外周確認を実機に送信します。以下を必ず確認してください:")
    print("  - 電源投入直後のキャリッジ位置がこの矩形の(0,0)角に対応していること")
    print(f"  - 送り速度は{args.outline_feed:.0f}mm/minと低速なので、異常があればすぐ電源を切れる態勢でいること")
    print("  - Zは動かさないため、ペンが紙に触れることはありません")
    print("=" * 60)
    if not args.yes:
        answer = input("続行しますか？ [y/N]: ").strip().lower()
        if answer != "y":
            print("中止しました。")
            return

    conn = GrblConnection(args.port, baudrate=args.baud)
    print(f"{args.port} に接続中...")
    startup = conn.connect()
    print(startup or "(起動メッセージなし)")
    print(f"現在位置: {conn.status()}")
    print("現在の物理位置を作業原点(0,0,0)としてゼロ設定します(G92)...")
    conn.zero_work_origin()
    print(f"ゼロ設定後: {conn.status()}")

    def on_progress(i: int, total: int, line: str, resp: str) -> None:
        print(f"[{i + 1}/{total}] {line} -> {resp}")

    try:
        conn.stream(lines, on_progress=on_progress)
        print("外周確認 完了。問題がなければ --outline-check なしで本描画を実行してください。")
    except GrblError as e:
        print(f"[エラー] GRBLがエラーを返しました: {e}")
        print("安全のため送信を中断しました。ペンや機体の状態を確認してください。")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    text = args.text.replace("\\n", "\n")
    n_cols = max((len(line) for line in text.split("\n")), default=1)
    n_rows = len(text.split("\n"))
    canvas_w_mm = args.size_mm * n_cols
    canvas_h_mm = args.size_mm * n_rows

    if args.outline_check:
        _run_outline_check(args, canvas_w_mm, canvas_h_mm)
        return

    config = PipelineConfig(
        font_path=Path(args.font),
        font_index=args.font_index,
        font_size_pt=args.font_size,
        cell_px=(args.cell_px, args.cell_px),
        canvas_size_mm=(canvas_w_mm, canvas_h_mm),
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
    print(f"描画範囲: X 0-{canvas_w_mm:.1f}mm, Y 0-{canvas_h_mm:.1f}mm")

    violations = check_xy_bounds(job, max_x=args.max_x, max_y=args.max_y)
    if violations:
        print("[エラー] 機体の可動範囲を超える座標があります。--size-mm を小さくしてください:")
        for v in violations:
            print(" -", v)
        return

    fig = render_grid_preview(job, pen_width_mm=0.5)
    preview_path = args.out_dir / "grbl_preview.png"
    fig.savefig(preview_path, dpi=150)
    print(f"プレビュー画像: {preview_path}")

    pen = RelativeZPenController(down_travel_mm=args.z_down_mm, z_feed=args.z_feed)
    lines = build_gcode_lines(job, pen=pen, feed_rate=args.draw_feed, travel_feed_rate=args.travel_feed)
    gcode_path = args.out_dir / "grbl_output.gcode"
    gcode_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"G-code: {gcode_path} ({len(lines)}行)")

    if args.dry_run or not args.port:
        print("ドライランのみ実行しました（実機へは送信していません）。")
        return

    print()
    print("=" * 60)
    print("これから実機へ送信します。以下を必ず確認してください:")
    print(f"  - 電源投入直後のペンの位置が、紙の上の安全な待機高さにあること")
    print(f"    （この位置がZ=0として扱われ、ここから{args.z_down_mm:.1f}mm下降してペンダウンします）")
    print(f"  - 可動範囲({args.max_x:.0f}x{args.max_y:.0f}mm)内に指や障害物がないこと")
    print(f"  - 送り速度: 描画{args.draw_feed:.0f}mm/min, 移動{args.travel_feed:.0f}mm/min, Z{args.z_feed:.0f}mm/min")
    print("=" * 60)
    if not args.yes:
        answer = input("続行しますか？ [y/N]: ").strip().lower()
        if answer != "y":
            print("中止しました。")
            return

    conn = GrblConnection(args.port, baudrate=args.baud)
    print(f"{args.port} に接続中...")
    startup = conn.connect()
    print(startup or "(起動メッセージなし)")
    print(f"現在位置: {conn.status()}")
    print("現在の物理位置を作業原点(0,0,0)としてゼロ設定します(G92)...")
    conn.zero_work_origin()
    print(f"ゼロ設定後: {conn.status()}")

    def on_progress(i: int, total: int, line: str, resp: str) -> None:
        if i % 20 == 0 or i == total - 1:
            print(f"[{i + 1}/{total}] {line} -> {resp}")

    try:
        conn.stream(lines, on_progress=on_progress)
        print("送信完了")
    except GrblError as e:
        print(f"[エラー] GRBLがエラーを返しました: {e}")
        print("安全のため送信を中断しました。ペンや機体の状態を確認してください。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
