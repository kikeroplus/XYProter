"""GRBLプロッター操作用のシンプルなGUIアプリ(Tkinter)。

起動:
    python -m xyproter.gui
    または scripts/gui_app.py

安全設計の要点([[feedback-grbl-safety-workflow]]の教訓を反映):
- ナビゲーション(ジョグ)は「範囲設定」で指定した可動範囲を超えないよう、
  移動前に現在位置を問い合わせてクランプする。
- 文字列送信は、実送信の直前に必ず現在位置を作業原点(0,0,0)にゼロ設定し
  (G92)、範囲チェック(check_xy_bounds)を通し、確認ダイアログを挟む。
- $20/$21/$22が無効な機体を前提に、フィードホールド/再開/ソフトリセット/
  アラーム解除ボタンを安全操作として追加している。
"""
from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .gcode_export import build_gcode_lines, build_outline_check_gcode
from .grbl_sender import GrblConnection, GrblError, check_xy_bounds, parse_status
from .pen_control import RelativeZPenController
from .pipeline import PipelineConfig, run_text_pipeline
from .types import PlotJob

# PyInstaller(onefile)実行時は__file__が実行のたびに変わる一時展開ディレクトリを
# 指すため、そこに設定を保存すると終了時に失われる。exe本体のディレクトリを使う。
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = _BASE_DIR / "xyproter_gui_settings.json"

STEP_OPTIONS = [0.01, 0.1, 1, 10, 100]
FEED_OPTIONS = [10, 50, 100, 500, 1000, 2000, 5000]
SIZE_OPTIONS = [5, 6, 7, 8, 9, 10, 15, 20, 25, 30, 40, 50, 80, 100]
# $112(Z最大送り速度)=1000mm/minが機体の上限。それを超える値を選んでもGRBLが$112で
# 自動的にクランプするため実害はないが、XY同様に将来の機体上限引き上げも見据えて
# GUI上は5000mm/minまで選択肢に含める。
Z_FEED_OPTIONS = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1500, 2000, 3000, 4000, 5000]
# $110/$111(X/Y最大送り速度)=3000mm/min(ベルトドライブ化後)が機体の上限。
# それを超える値を選んでもGRBLが$110/$111で自動的にクランプするため実害はないが、
# 将来的な機体上限の引き上げも見据えてGUI上は5000mm/minまで選択肢に含める。
XY_FEED_OPTIONS = [50, 100, 200, 300, 500, 800, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000]
LETTER_SPACING_OPTIONS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

# $1(Step Idle Delay)。この機体はENABLEピンがX/Y/Z共通配線のため、GRBL標準では
# 「Z軸のみ」励磁保持を切り替える手段がない([[project-grbl-plotter-hardware]]参照)。
# そのためトルク保持ON/OFFは全軸に対して$1を切り替える形で実装する。
# OFF値の25はこの機体でこれまで使われてきたGRBLデフォルト値。
GRBL_IDLE_DELAY_HOLD = 255  # ON: 常時励磁(モーター温度上昇と引き換えにペン位置を保持)
GRBL_IDLE_DELAY_DEFAULT = 25  # OFF: 25ms後にアイドル解放(通常運用)

DEFAULT_SETTINGS = {
    "port": "",
    "baud": 115200,
    "max_x": 200.0,
    "max_y": 200.0,
    "min_z": -50.0,
    "max_z": 50.0,
    "step_mm": 1.0,
    "jog_step_z": 1.0,
    "feed": 500.0,
    "jog_feed_z": 200.0,
    "size_mm": 30.0,
    "line_spacing_mm": 30.0,
    "z_down_mm": 3.0,
    "z_feed": 200.0,
    "final_lift_mm": 15.0,
    "letter_spacing_factor": 1.0,
    "draw_feed": 300.0,
    "travel_feed": 600.0,
    "font_path": r"C:\Windows\Fonts\msmincho.ttc",
    "fast_mode": False,
    "simplify_tolerance_mm": 0.15,
}

_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


class GrblControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("XYProter プロッター操作パネル")
        self.conn: GrblConnection | None = None
        self.last_wco: tuple[float, float, float] = (0.0, 0.0, 0.0)
        # 前回の送信でteardown時にZ軸をどれだけ退避させたか。次回送信の最初の
        # pen_downで、その分を追加で下げる補正に使う([[RelativeZPenController]]参照)。
        # 新規接続時は物理的な基準が不明になるためリセットする。
        self._last_final_lift_mm: float = 0.0
        # 送信中のバックグラウンドスレッドとキャンセル要求フラグ。
        # tkinterはスレッドセーフでないため、ワーカースレッドからのUI更新は
        # 必ず self.root.after(0, ...) 経由でメインスレッドにディスパッチする。
        self._send_thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self.settings = self._load_settings()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 設定の保存/読込 ----------
    def _load_settings(self) -> dict:
        settings = dict(DEFAULT_SETTINGS)
        if SETTINGS_PATH.exists():
            try:
                settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return settings

    def _save_settings(self) -> None:
        try:
            self.settings.update(
                {
                    "port": self.port_var.get(),
                    "baud": int(self.baud_var.get()),
                    "max_x": float(self.max_x_var.get()),
                    "max_y": float(self.max_y_var.get()),
                    "min_z": float(self.min_z_var.get()),
                    "max_z": float(self.max_z_var.get()),
                    "step_mm": float(self.step_var.get()),
                    "jog_step_z": float(self.jog_step_z_var.get()),
                    "feed": float(self.feed_var.get()),
                    "jog_feed_z": float(self.jog_feed_z_var.get()),
                    "size_mm": float(self.size_var.get()),
                    "line_spacing_mm": float(self.line_spacing_var.get()),
                    "z_down_mm": float(self.zdown_var.get()),
                    "z_feed": float(self.z_feed_var.get()),
                    "final_lift_mm": float(self.final_lift_var.get()),
                    "letter_spacing_factor": float(self.letter_spacing_var.get()),
                    "draw_feed": float(self.draw_feed_var.get()),
                    "travel_feed": float(self.travel_feed_var.get()),
                    "font_path": self.font_var.get(),
                    "fast_mode": bool(self.fast_mode_var.get()),
                    "simplify_tolerance_mm": float(self.simplify_tolerance_var.get()),
                }
            )
            SETTINGS_PATH.write_text(
                json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass  # 設定保存の失敗はアプリ終了を妨げない

    def _on_close(self) -> None:
        self._save_settings()
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.root.destroy()

    # ---------- UI構築 ----------
    def _build_ui(self) -> None:
        s = self.settings

        conn_frame = ttk.LabelFrame(self.root, text="接続")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        self.port_var = tk.StringVar(value=s["port"])
        self.port_combo = ttk.Combobox(conn_frame, textvariable=self.port_var, width=12, values=self._list_ports())
        self.port_combo.grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(conn_frame, text="ポート更新", command=self._refresh_ports).grid(row=0, column=1, padx=2)

        ttk.Label(conn_frame, text="baud").grid(row=0, column=2)
        self.baud_var = tk.StringVar(value=str(s["baud"]))
        ttk.Entry(conn_frame, textvariable=self.baud_var, width=8).grid(row=0, column=3, padx=4)

        # 接続状態がひと目でわかるよう縁取り色を切り替える。ttk.Buttonは
        # highlightbackground等のTk標準の縁取りオプションをWindowsの既定
        # テーマ(vista)では描画しないため、色付きのtk.Frameで包んで隙間
        # (padx/pady)を縁取りに見せる方式にしている。
        self.connect_border = tk.Frame(conn_frame, bg="red")
        self.connect_border.grid(row=0, column=4, padx=4)
        self.connect_btn = ttk.Button(self.connect_border, text="接続", command=self._toggle_connect)
        self.connect_btn.pack(padx=2, pady=2)

        self.conn_status_var = tk.StringVar(value="未接続")
        ttk.Label(conn_frame, textvariable=self.conn_status_var).grid(row=0, column=5, padx=8)

        # ---- 情報表示 ----
        info_frame = ttk.LabelFrame(self.root, text="情報表示")
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        self.state_var = tk.StringVar(value="状態: -")
        self.mpos_var = tk.StringVar(value="MPos(機械座標): -")
        self.wpos_var = tk.StringVar(value="WPos(作業座標): -")
        ttk.Label(info_frame, textvariable=self.state_var).grid(row=0, column=0, padx=6, sticky="w")
        ttk.Label(info_frame, textvariable=self.mpos_var).grid(row=1, column=0, padx=6, sticky="w")
        ttk.Label(info_frame, textvariable=self.wpos_var).grid(row=2, column=0, padx=6, sticky="w")
        ttk.Button(info_frame, text="更新", command=self._refresh_status).grid(row=0, column=1, rowspan=3, padx=8)

        # ---- ゼロ点設定 ----
        zero_frame = ttk.LabelFrame(self.root, text="ゼロ点設定")
        zero_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=4)

        def _zero_button(text: str, axes: str, column: int) -> None:
            border = tk.Frame(zero_frame, bg="#87CEFA")
            border.grid(row=0, column=column, padx=4, pady=4)
            ttk.Button(border, text=text, command=lambda: self._on_zero(axes)).pack(padx=2, pady=2)

        _zero_button("X=0", "X", 0)
        _zero_button("Y=0", "Y", 1)
        _zero_button("Z=0", "Z", 2)
        _zero_button("XYZ=0", "XYZ", 3)

        # ---- 範囲設定 ----
        range_frame = ttk.LabelFrame(self.root, text="範囲設定 (mm)")
        range_frame.grid(row=2, column=1, sticky="nsew", padx=6, pady=4)
        self.max_x_var = tk.StringVar(value=str(s["max_x"]))
        self.max_y_var = tk.StringVar(value=str(s["max_y"]))
        self.min_z_var = tk.StringVar(value=str(s["min_z"]))
        self.max_z_var = tk.StringVar(value=str(s["max_z"]))
        ttk.Label(range_frame, text="X最大").grid(row=0, column=0)
        ttk.Entry(range_frame, textvariable=self.max_x_var, width=8).grid(row=0, column=1)
        ttk.Label(range_frame, text="Y最大").grid(row=1, column=0)
        ttk.Entry(range_frame, textvariable=self.max_y_var, width=8).grid(row=1, column=1)
        ttk.Label(range_frame, text="Z最小").grid(row=0, column=2)
        ttk.Entry(range_frame, textvariable=self.min_z_var, width=8).grid(row=0, column=3)
        ttk.Label(range_frame, text="Z最大").grid(row=1, column=2)
        ttk.Entry(range_frame, textvariable=self.max_z_var, width=8).grid(row=1, column=3)

        # ---- 移動速度 + ナビゲーション ----
        # どちらもroot直下のgridに置くと、列幅が「ゼロ点設定」等の他の行に
        # 引っ張られてしまい隣同士に詰められなかったため、専用の行フレームに
        # まとめてpack(side="left")で隙間なく隣接させる(draw_frame内の
        # settings_rowと同じ「横並びフローレイアウト」パターン)。
        movement_row = ttk.Frame(self.root)
        movement_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=0, pady=0)

        # 設定値はXY/Zでくくって並べる(ステップ・フィードの種類別ではなく、
        # 軸のまとまり単位でグループ化)。
        speed_frame = ttk.LabelFrame(movement_row, text="移動速度 (mm / mm/min)")
        speed_frame.pack(side="left", padx=6, pady=4)
        ttk.Label(speed_frame, text="ステップXY").grid(row=0, column=0)
        self.step_var = tk.StringVar(value=str(s["step_mm"]))
        ttk.Combobox(
            speed_frame, textvariable=self.step_var, values=STEP_OPTIONS, width=5, state="readonly"
        ).grid(row=0, column=1)
        ttk.Label(speed_frame, text="フィードXY").grid(row=1, column=0)
        self.feed_var = tk.StringVar(value=str(s["feed"]))
        ttk.Combobox(
            speed_frame, textvariable=self.feed_var, values=FEED_OPTIONS, width=6, state="readonly"
        ).grid(row=1, column=1)
        ttk.Separator(speed_frame, orient="horizontal").grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(speed_frame, text="ステップZ").grid(row=3, column=0)
        self.jog_step_z_var = tk.StringVar(value=str(s["jog_step_z"]))
        ttk.Combobox(
            speed_frame, textvariable=self.jog_step_z_var, values=STEP_OPTIONS, width=5, state="readonly"
        ).grid(row=3, column=1)
        ttk.Label(speed_frame, text="フィードZ").grid(row=4, column=0)
        self.jog_feed_z_var = tk.StringVar(value=str(s["jog_feed_z"]))
        ttk.Combobox(
            speed_frame, textvariable=self.jog_feed_z_var, values=FEED_OPTIONS, width=6, state="readonly"
        ).grid(row=4, column=1)

        # ---- ナビゲーション ----
        # 十字のジョグパッド(一文字戻る/X-/原点/X+/一文字進むを横軸、
        # Y+/原点/Y-を縦軸)を中心に、縦軸の上下に一行戻る/一行進む、
        # さらに右端にZ+/Z-を独立した列で配置する。移動速度ペインのすぐ右に
        # packで隣接させる。
        nav_frame = ttk.LabelFrame(movement_row, text="ナビゲーション")
        nav_frame.pack(side="left", padx=6, pady=4)
        ttk.Button(nav_frame, text="一行戻る", command=lambda: self._on_line_jog(forward=False)).grid(
            row=0, column=2, padx=4, pady=4
        )
        ttk.Button(nav_frame, text="Y+", command=lambda: self._on_jog("Y", 1)).grid(row=1, column=2, padx=4, pady=4)
        ttk.Button(nav_frame, text="一文字戻る", command=lambda: self._on_char_jog(forward=False)).grid(
            row=2, column=0, padx=4, pady=4
        )
        ttk.Button(nav_frame, text="X-", command=lambda: self._on_jog("X", -1)).grid(row=2, column=1, padx=4, pady=4)
        ttk.Button(nav_frame, text="原点(0,0)", command=self._on_return_to_origin).grid(
            row=2, column=2, padx=4, pady=4
        )
        ttk.Button(nav_frame, text="X+", command=lambda: self._on_jog("X", 1)).grid(row=2, column=3, padx=4, pady=4)
        ttk.Button(nav_frame, text="一文字進む", command=lambda: self._on_char_jog(forward=True)).grid(
            row=2, column=4, padx=4, pady=4
        )
        ttk.Button(nav_frame, text="Y-", command=lambda: self._on_jog("Y", -1)).grid(row=3, column=2, padx=4, pady=4)
        ttk.Button(nav_frame, text="一行進む", command=lambda: self._on_line_jog(forward=True)).grid(
            row=4, column=2, padx=4, pady=4
        )
        ttk.Button(nav_frame, text="Z+", command=lambda: self._on_jog("Z", 1)).grid(row=1, column=5, padx=4, pady=4)
        ttk.Button(nav_frame, text="Z-", command=lambda: self._on_jog("Z", -1)).grid(row=3, column=5, padx=4, pady=4)

        # ---- 安全操作(依頼仕様にはないが、事故歴を踏まえて追加) ----
        safety_frame = ttk.LabelFrame(self.root, text="安全操作")
        safety_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        ttk.Button(safety_frame, text="フィードホールド(!)", command=self._on_feed_hold).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(safety_frame, text="再開(~)", command=self._on_resume).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(safety_frame, text="ソフトリセット", command=self._on_soft_reset).grid(row=0, column=2, padx=4, pady=4)
        ttk.Button(safety_frame, text="アラーム解除($X)", command=self._on_unlock).grid(row=0, column=3, padx=4, pady=4)

        # -- 生コマンド送信($$設定変更など、G-code/GRBLコマンドを1行そのまま送る) --
        ttk.Label(safety_frame, text="コマンド送信").grid(row=1, column=0, padx=4, pady=(0, 4), sticky="e")
        self.raw_cmd_var = tk.StringVar(value="")
        raw_cmd_entry = ttk.Entry(safety_frame, textvariable=self.raw_cmd_var, width=20)
        raw_cmd_entry.grid(row=1, column=1, columnspan=2, padx=4, pady=(0, 4), sticky="ew")
        raw_cmd_entry.bind("<Return>", lambda _e: self._on_send_raw_command())
        ttk.Button(safety_frame, text="送信", command=self._on_send_raw_command).grid(
            row=1, column=3, padx=4, pady=(0, 4)
        )

        # -- トルク保持モード(全軸$1=255⇔25の切り替え) --
        # ENABLEピンがX/Y/Z共通配線のため軸単体では切り替えられない
        # ([[project-grbl-plotter-hardware]]参照)。起動時は必ずOFFから始まり
        # 設定ファイルにも保存しない(モーター発熱に関わる設定を接続のたびに
        # 無自覚に引き継がせないため)。
        self.torque_hold_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            safety_frame,
            text="トルク保持(全軸, $1=255)",
            variable=self.torque_hold_var,
            command=self._on_toggle_torque_hold,
        ).grid(row=1, column=4, padx=(12, 4), pady=(0, 4), sticky="w")

        # ---- 文字描画 ----
        # 度重なる仕様追加でパラメータが1枚のフレームに平積みになっていたため、
        # 「フォント」「文字列入力(主役として横長・大きく)」「設定(カテゴリ別
        # サブフレーム)」「実行ボタン」の4ブロックに整理する。
        draw_frame = ttk.LabelFrame(self.root, text="文字描画")
        draw_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        # -- フォント --
        font_row = ttk.Frame(draw_frame)
        font_row.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        ttk.Label(font_row, text="フォント").pack(side="left")
        self.font_var = tk.StringVar(value=s["font_path"])
        ttk.Entry(font_row, textvariable=self.font_var).pack(
            side="left", padx=6, fill="x", expand=True
        )
        ttk.Button(font_row, text="参照", command=self._browse_font).pack(side="left")
        ttk.Button(font_row, text="Windowsフォント", command=self._browse_windows_font).pack(
            side="left", padx=(4, 0)
        )

        # -- 文字列入力(主役なので横長・大きめのフォントで目立たせる) --
        # 赤線は「文字数×文字入力欄の平均文字幅」で位置を近似しているため、
        # 列数・行数が多い設定(小さい文字÷広い範囲)では欄の表示幅を超えて
        # クランプされ、線だけでは正確な目安にならない。そのため正確な列数・
        # 行数を数値でも併記する(こちらは近似なしの厳密値)。
        self.guide_label_var = tk.StringVar(value="文字列 (Enterで改行)")
        ttk.Label(draw_frame, textvariable=self.guide_label_var).grid(
            row=1, column=0, sticky="w", padx=6, pady=(6, 0)
        )
        self.text_widget = tk.Text(
            draw_frame,
            width=64,
            height=6,
            wrap="none",
            font=("", 13),
            undo=True,
            autoseparators=True,
            maxundo=-1,
        )
        self.text_widget.grid(row=2, column=0, sticky="ew", padx=6, pady=(2, 6))
        self._text_font = tkfont.Font(font=self.text_widget.cget("font"))
        # 範囲設定(max_x/max_y)・文字サイズ・文字間隔・行間から、これ以上入力すると
        # 描画範囲をはみ出す境目を赤線でText上にオーバーレイ表示するガイド
        # ([[project-xyproter-pipeline]]の座標系: 原点=1文字目左上、X+右・Y-下)。
        # place(in_=text_widget)でテキストウィジェットの上に重ねて描画する。
        self._guide_v = tk.Frame(draw_frame, bg="red")
        self._guide_h = tk.Frame(draw_frame, bg="red")
        self.text_widget.bind("<Configure>", self._update_draw_guide)
        # ガイドの列位置は実際に入力されている文字のTk実測位置(bbox)を優先して
        # 使う([[_update_draw_guide]])ため、範囲設定などを一切変えずに文字を
        # 入力/削除しただけでもガイドの再計算が必要。<<Modified>>は入力・削除・
        # 貼り付け・元に戻す等どの経路でも発火するTk標準の変更通知イベント。
        # 発火後にedit_modified(False)でフラグをリセットしないと以降二度と
        # 発火しなくなる仕様のため、ここで毎回リセットしている。
        self.text_widget.bind("<<Modified>>", self._on_text_modified)

        # -- 設定(カテゴリ別サブフレームを横並びに) --
        settings_row = ttk.Frame(draw_frame)
        settings_row.grid(row=3, column=0, sticky="ew", padx=6, pady=2)

        char_frame = ttk.LabelFrame(settings_row, text="文字設定")
        char_frame.pack(side="left", fill="y", padx=(0, 4))
        ttk.Label(char_frame, text="サイズ(mm)").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.size_var = tk.StringVar(value=str(s["size_mm"]))
        ttk.Combobox(
            char_frame, textvariable=self.size_var, values=SIZE_OPTIONS, width=6, state="readonly"
        ).grid(row=0, column=1, padx=4, pady=2)
        ttk.Label(char_frame, text="行間(mm)").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        # 文字サイズ(セル寸法)とは独立に行方向のピッチをmmで直接指定する。既定は
        # サイズ(mm)と同値(=従来どおり行間と文字サイズが一致)だが、値を変えれば
        # 小さい文字を広い罫線幅に合わせて配置したり、逆に大きい文字を狭い行間で
        # 重ねて配置することもできる(重なりの禁止制御はあえて省略している)。
        self.line_spacing_var = tk.StringVar(value=str(s["line_spacing_mm"]))
        ttk.Entry(char_frame, textvariable=self.line_spacing_var, width=6).grid(
            row=1, column=1, padx=4, pady=2
        )
        ttk.Label(char_frame, text="文字間隔").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.letter_spacing_var = tk.StringVar(value=str(s["letter_spacing_factor"]))
        ttk.Combobox(
            char_frame,
            textvariable=self.letter_spacing_var,
            values=LETTER_SPACING_OPTIONS,
            width=6,
            state="readonly",
        ).grid(row=2, column=1, padx=4, pady=2)
        self.fast_mode_var = tk.BooleanVar(value=bool(s["fast_mode"]))
        ttk.Checkbutton(char_frame, text="高速モード", variable=self.fast_mode_var).grid(
            row=3, column=0, sticky="w", padx=4, pady=2
        )
        self.simplify_tolerance_var = tk.StringVar(value=str(s["simplify_tolerance_mm"]))
        ttk.Entry(char_frame, textvariable=self.simplify_tolerance_var, width=6).grid(
            row=3, column=1, padx=4, pady=2
        )

        speed_frame2 = ttk.LabelFrame(settings_row, text="送り速度(mm/min)")
        speed_frame2.pack(side="left", fill="y", padx=4)
        ttk.Label(speed_frame2, text="描画").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.draw_feed_var = tk.StringVar(value=str(s["draw_feed"]))
        ttk.Combobox(
            speed_frame2, textvariable=self.draw_feed_var, values=XY_FEED_OPTIONS, width=6
        ).grid(row=0, column=1, padx=4, pady=2)
        ttk.Label(speed_frame2, text="移動").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.travel_feed_var = tk.StringVar(value=str(s["travel_feed"]))
        ttk.Combobox(
            speed_frame2, textvariable=self.travel_feed_var, values=XY_FEED_OPTIONS, width=6
        ).grid(row=1, column=1, padx=4, pady=2)
        ttk.Label(speed_frame2, text="Z").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.z_feed_var = tk.StringVar(value=str(s["z_feed"]))
        ttk.Combobox(
            speed_frame2, textvariable=self.z_feed_var, values=Z_FEED_OPTIONS, width=6
        ).grid(row=2, column=1, padx=4, pady=2)

        pen_frame = ttk.LabelFrame(settings_row, text="ペン制御(mm)")
        pen_frame.pack(side="left", fill="y", padx=4)
        ttk.Label(pen_frame, text="Z下降量").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.zdown_var = tk.StringVar(value=str(s["z_down_mm"]))
        ttk.Entry(pen_frame, textvariable=self.zdown_var, width=6).grid(row=0, column=1, padx=4, pady=2)
        ttk.Label(pen_frame, text="終了時退避量").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.final_lift_var = tk.StringVar(value=str(s["final_lift_mm"]))
        ttk.Entry(pen_frame, textvariable=self.final_lift_var, width=6).grid(row=1, column=1, padx=4, pady=2)

        # -- 実行ボタン --
        action_row = ttk.Frame(draw_frame)
        action_row.grid(row=4, column=0, sticky="ew", padx=6, pady=6)
        auto_wrap_border = tk.Frame(action_row, bg="#87CEFA")
        auto_wrap_border.pack(side="left", padx=(0, 4))
        ttk.Button(auto_wrap_border, text="自動改行", command=self._on_auto_wrap).pack(padx=2, pady=2)
        ttk.Button(action_row, text="外周確認", command=self._on_outline_check).pack(side="left", padx=(0, 4))
        ttk.Button(action_row, text="シミュレーション", command=self._on_simulate).pack(side="left", padx=4)
        send_border = tk.Frame(action_row, bg="blue")
        send_border.pack(side="left", padx=4)
        self.send_btn = ttk.Button(send_border, text="送信", command=self._on_send_text)
        self.send_btn.pack(padx=2, pady=2)
        self.cancel_btn = ttk.Button(
            action_row, text="キャンセル", command=self._on_cancel_send, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=4)

        self.job_status_var = tk.StringVar(value="")
        ttk.Label(draw_frame, textvariable=self.job_status_var, foreground="blue", wraplength=560).grid(
            row=5, column=0, sticky="w", padx=6, pady=(0, 6)
        )

        # 描画範囲ガイド(赤線)は範囲設定・文字サイズ・文字間隔・行間の変更に
        # 追従して再計算する。全StringVar構築後にまとめてtraceを張る。
        for var in (self.max_x_var, self.max_y_var, self.size_var, self.letter_spacing_var, self.line_spacing_var):
            var.trace_add("write", self._update_draw_guide)
        self.root.after(100, self._update_draw_guide)

    # ---------- ポート ----------
    def _list_ports(self) -> list[str]:
        import serial.tools.list_ports as list_ports

        return [p.device for p in list_ports.comports()]

    def _refresh_ports(self) -> None:
        self.port_combo["values"] = self._list_ports()

    # ---------- 接続 ----------
    def _toggle_connect(self) -> None:
        if self._warn_if_sending():
            return
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
            self.conn_status_var.set("未接続")
            self.connect_btn.config(text="接続")
            self.connect_border.config(bg="red")
            return

        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("エラー", "COMポートを選択してください")
            return
        try:
            baud = int(self.baud_var.get())
            conn = GrblConnection(port, baudrate=baud)
            conn.connect()
            self.conn = conn
            self._last_final_lift_mm = 0.0  # 新規接続では物理的な基準が不明なのでリセット
            self.conn_status_var.set(f"接続済み: {port}")
            self.connect_btn.config(text="切断")
            self.connect_border.config(bg="green")
            self._refresh_status()
        except Exception as e:
            messagebox.showerror("接続エラー", str(e))

    def _require_conn(self) -> GrblConnection | None:
        if self.conn is None:
            messagebox.showerror("エラー", "先にプロッターへ接続してください")
            return None
        return self.conn

    def _warn_if_sending(self) -> bool:
        """送信中なら警告してTrueを返す(呼び出し元はこの場合処理を中断すること)。

        送信は別スレッドで同期プロトコル(1行送信->応答待ち)を使ってシリアル
        ポートを占有しているため、この間に別の操作(ジョグ、ゼロ点設定等)で
        同じポートに書き込むと応答を取り違える危険がある。フィードホールド/
        再開/ソフトリセット/キャンセルはリアルタイムコマンドで独立しているため、
        このガードの対象外にしている。
        """
        if self._cancel_event is not None:
            messagebox.showerror("エラー", "送信中は操作できません。先に「キャンセル」してください。")
            return True
        return False

    # ---------- 情報表示 ----------
    def _apply_status(self, parsed: dict) -> tuple[float, float, float] | None:
        if parsed.get("work_offset") is not None:
            self.last_wco = parsed["work_offset"]
        mpos = parsed.get("machine_position")
        if mpos is None:
            return None
        wpos = tuple(m - w for m, w in zip(mpos, self.last_wco))
        self.state_var.set(f"状態: {parsed.get('state') or '?'}")
        self.mpos_var.set("MPos(機械座標): X{:.3f} Y{:.3f} Z{:.3f}".format(*mpos))
        self.wpos_var.set("WPos(作業座標): X{:.3f} Y{:.3f} Z{:.3f}".format(*wpos))
        return wpos

    def _refresh_status(self) -> tuple[float, float, float] | None:
        conn = self._require_conn()
        if conn is None:
            return None
        if self._warn_if_sending():
            return None
        try:
            parsed = parse_status(conn.status())
        except Exception as e:
            messagebox.showerror("通信エラー", str(e))
            return None
        return self._apply_status(parsed)

    # ---------- ゼロ点設定 ----------
    def _on_zero(self, axes: str) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            parsed = parse_status(conn.status())
            if parsed.get("work_offset") is not None:
                self.last_wco = parsed["work_offset"]
            mpos = parsed.get("machine_position")
            if mpos is None:
                messagebox.showerror("エラー", "現在位置を取得できませんでした")
                return

            conn.send_line("G92 " + " ".join(f"{a}0" for a in axes))

            if "Z" in axes:
                # Zの物理的な基準が変わったため、前回送信の退避量に基づく
                # 補正(initial_extra_down_mm)はもう無効。残すと次回送信で
                # 余計にペンを押し込んでしまう(紙面やZ機構への衝突の危険)。
                self._last_final_lift_mm = 0.0

            new_wco = list(self.last_wco)
            for a in axes:
                new_wco[_AXIS_INDEX[a]] = mpos[_AXIS_INDEX[a]]
            self.last_wco = tuple(new_wco)
            self._apply_status({"state": parsed.get("state"), "machine_position": mpos, "work_offset": None})
            self.job_status_var.set(f"{axes} をゼロ設定しました")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))

    # ---------- ナビゲーション ----------
    def _on_jog(self, axis: str, sign: int) -> None:
        # 「範囲設定」はここでは適用しない(文字送信ジョブの範囲チェックのみに使う)。
        # ジョグは常にユーザーが目視しながら操作する前提のため、指定ステップ分そのまま動かす。
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            if axis == "Z":
                step = float(self.jog_step_z_var.get())
                feed = float(self.jog_feed_z_var.get())
            else:
                step = float(self.step_var.get())
                feed = float(self.feed_var.get())
        except ValueError:
            messagebox.showerror("エラー", "ステップ/フィードの値を確認してください")
            return

        delta = step * sign
        try:
            conn.send_line("G91")
            conn.send_line(f"G1 {axis}{delta:.4f} F{feed:.1f}")
            conn.send_line("G90")
            if axis == "Z":
                # ジョグでZの物理位置を手動変更したため、前回送信の退避量に
                # 基づく補正(initial_extra_down_mm)の前提が崩れる。安全側で無効化する。
                self._last_final_lift_mm = 0.0
            self.job_status_var.set(f"{axis}を{delta:+.3f}mm移動しました")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))
        finally:
            self._refresh_status()

    def _on_line_jog(self, forward: bool) -> None:
        """行間ピッチ(mm)分だけYをジョグする(一行進む/戻るボタン用)。

        「行間(mm)」欄の値は[[_get_text_and_canvas]]のcanvas_h_mm計算と同じ前提で
        「1行あたりのYピッチ」そのものなので、変換なしにそのままステップ量として使える。
        forward=True(一行進む)は文章が続く方向=Y-(下)、False(一行戻る)はY+(上)。
        """
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            line_spacing_mm = float(self.line_spacing_var.get())
            feed = float(self.feed_var.get())
        except ValueError:
            messagebox.showerror("エラー", "行間(mm)/フィードの値を確認してください")
            return

        delta = -line_spacing_mm if forward else line_spacing_mm
        try:
            conn.send_line("G91")
            conn.send_line(f"G1 Y{delta:.4f} F{feed:.1f}")
            conn.send_line("G90")
            self.job_status_var.set(f"Yを{delta:+.3f}mm移動しました({'一行進む' if forward else '一行戻る'})")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))
        finally:
            self._refresh_status()

    def _on_char_jog(self, forward: bool = True) -> None:
        """1文字分のXピッチ(mm)だけXをジョグする(一文字進む/戻るボタン用)。

        文字ピッチ=文字サイズ(mm)×文字間隔(letter_spacing_factor)。
        [[_get_text_and_canvas]]のcanvas_w_mm計算と同じ前提。
        forward=True(一文字進む)はX+(右)、False(一文字戻る)はX-(左)。
        """
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            size_mm = float(self.size_var.get())
            letter_spacing_factor = float(self.letter_spacing_var.get())
            feed = float(self.feed_var.get())
        except ValueError:
            messagebox.showerror("エラー", "文字サイズ/文字間隔/フィードの値を確認してください")
            return

        pitch = size_mm * letter_spacing_factor
        delta = pitch if forward else -pitch
        try:
            conn.send_line("G91")
            conn.send_line(f"G1 X{delta:.4f} F{feed:.1f}")
            conn.send_line("G90")
            self.job_status_var.set(f"Xを{delta:+.3f}mm移動しました({'一文字進む' if forward else '一文字戻る'})")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))
        finally:
            self._refresh_status()

    def _on_return_to_origin(self) -> None:
        """現在の作業原点(0,0)へXYだけを移動する(Zは動かさない)。

        文字列送信は既定で「書き終わりの次の行の先頭」に移動して終わる
        仕様のため、書き終わった位置から改めて1文字目の左上(原点)を
        確認・やり直したい場合に使う。zero_work_origin()は呼ばないため、
        直前の送信/ゼロ点設定が定義した作業座標系はそのまま、その(0,0)へ
        戻るだけ。
        """
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            travel_feed = float(self.travel_feed_var.get())
        except ValueError:
            messagebox.showerror("エラー", "送り速度(移動)を確認してください")
            return
        if not messagebox.askyesno(
            "原点に戻る",
            "現在の作業原点(0,0)へXYだけを移動します(Zは動かしません)。\n続行しますか?",
        ):
            return
        try:
            conn.send_line("G90")
            conn.send_line(f"G0 X0.000 Y0.000 F{travel_feed:.1f}")
            self.job_status_var.set("原点(0,0)へ移動しました")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))
        finally:
            self._refresh_status()

    # ---------- 安全操作 ----------
    def _on_feed_hold(self) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        conn.feed_hold()
        self.job_status_var.set("フィードホールド(!)を送信しました")

    def _on_resume(self) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        conn.cycle_resume()
        self.job_status_var.set("再開(~)を送信しました")

    def _on_soft_reset(self) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        conn.soft_reset()
        self.job_status_var.set("ソフトリセットを送信しました")

    def _on_unlock(self) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            conn.unlock()
            self.job_status_var.set("アラーム解除($X)を送信しました")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))

    def _on_send_raw_command(self) -> None:
        """任意のG-code/GRBLコマンド(\\$122=1500等)を1行そのまま送信する。

        \\$\\$設定の変更など、GUIの他の機能がカバーしていない操作向け。
        """
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        command = self.raw_cmd_var.get().strip()
        if not command:
            return
        try:
            resp = conn.send_line(command)
            self.job_status_var.set(f"'{command}' -> {resp}")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))

    def _on_toggle_torque_hold(self) -> None:
        """トルク保持チェックボックスの切り替えに応じて$1(Step Idle Delay)を送信する。

        ON: $1=255で常時励磁(モーター発熱と引き換えにアイドル中もペン位置を保持)。
        OFF: $1=25でGRBLデフォルトに戻す(アイドル後に励磁解放)。
        全軸共通設定のためX/Yも道連れで切り替わる([[project-grbl-plotter-hardware]]参照)。
        接続なし/送信中/GRBLエラー時はチェック状態を操作前に戻す。
        """
        want_hold = self.torque_hold_var.get()
        conn = self._require_conn()
        if conn is None:
            self.torque_hold_var.set(not want_hold)
            return
        if self._warn_if_sending():
            self.torque_hold_var.set(not want_hold)
            return
        value = GRBL_IDLE_DELAY_HOLD if want_hold else GRBL_IDLE_DELAY_DEFAULT
        command = f"$1={value}"
        try:
            resp = conn.send_line(command)
            state_label = "トルク保持ON(常時励磁)" if want_hold else "トルク保持OFF(通常)"
            self.job_status_var.set(f"{state_label}: '{command}' -> {resp}")
        except GrblError as e:
            self.torque_hold_var.set(not want_hold)
            messagebox.showerror("GRBLエラー", str(e))

    def _auto_disable_torque_hold(self) -> None:
        """文字列送信が正常完了した際、トルク保持をONのままにせず自動でOFFに戻す。

        「書き始め直前にON、書き終わりでOFF」という運用(ユーザー指示、モーター発熱を
        必要な間だけに抑えるため)。キャンセル・GRBLエラーで中断した場合はここを通らず
        ONのまま維持する(「再開」で送信を続ける可能性を優先する)。
        """
        if not self.torque_hold_var.get():
            return
        self.torque_hold_var.set(False)
        self._on_toggle_torque_hold()

    # ---------- フォント選択 ----------
    def _browse_font(self) -> None:
        """任意フォルダ(プロジェクト同梱フォント等)向けの通常のファイル選択ダイアログ。

        C:\\Windows\\Fontsは特殊なシェル名前空間(CLSIDによる「Fonts」ビュー)
        として開かれるため、標準のファイル選択ダイアログでこのフォルダに
        移動すると中身が一切表示されない(既知のWindowsの挙動)。initialdirへ
        `\\\\?\\`プレフィックスを付ける回避策も効かなかったため、Windows
        フォントを選ぶ場合は代わりに「Windowsフォント」ボタン
        (`_browse_windows_font`)を使う。
        """
        current = self.font_var.get().strip()
        initial_dir = Path(current).parent if current else None
        if initial_dir is not None and not initial_dir.is_dir():
            initial_dir = None

        kwargs = {}
        if initial_dir is not None:
            kwargs["initialdir"] = str(initial_dir)
        path = filedialog.askopenfilename(
            title="フォントファイルを選択",
            filetypes=[("フォント", "*.ttf *.ttc *.otf"), ("すべて", "*.*")],
            **kwargs,
        )
        if path:
            self.font_var.set(path)

    def _browse_windows_font(self) -> None:
        """C:\\Windows\\Fontsから選ぶ専用ボタン。

        標準のファイル選択ダイアログはこのフォルダを特殊なシェル名前空間
        として開くため中身が表示されない([[_browse_font]]参照)。この
        フォルダに限りPython側で直接ファイル一覧を取得し(シェルを経由
        しないので影響を受けない)、自前の簡易リスト選択ダイアログで選ばせる。
        """
        path = self._pick_font_from_fonts_dir()
        if path:
            self.font_var.set(path)

    def _pick_font_from_fonts_dir(self) -> str | None:
        fonts_dir = Path(r"C:\Windows\Fonts")
        try:
            files = sorted(
                (p for p in fonts_dir.iterdir() if p.suffix.lower() in (".ttf", ".ttc", ".otf")),
                key=lambda p: p.name.lower(),
            )
        except OSError as e:
            messagebox.showerror("エラー", f"{fonts_dir} を読み取れませんでした: {e}")
            return None
        if not files:
            messagebox.showerror("エラー", f"{fonts_dir} 内にフォントファイルが見つかりませんでした")
            return None

        dialog = tk.Toplevel(self.root)
        dialog.title("フォントファイルを選択 (C:\\Windows\\Fonts)")
        dialog.transient(self.root)
        dialog.grab_set()

        list_row = ttk.Frame(dialog)
        list_row.pack(side="top", fill="both", expand=True, padx=6, pady=6)
        listbox = tk.Listbox(list_row, width=50, height=20)
        for p in files:
            listbox.insert("end", p.name)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_row, orient="vertical", command=listbox.yview)
        scrollbar.pack(side="left", fill="y")
        listbox.config(yscrollcommand=scrollbar.set)
        listbox.selection_set(0)
        listbox.focus_set()

        result: dict[str, str | None] = {"path": None}

        def _on_ok() -> None:
            sel = listbox.curselection()
            if sel:
                result["path"] = str(files[sel[0]])
            dialog.destroy()

        def _on_cancel() -> None:
            dialog.destroy()

        listbox.bind("<Double-Button-1>", lambda e: _on_ok())
        dialog.bind("<Return>", lambda e: _on_ok())
        dialog.bind("<Escape>", lambda e: _on_cancel())

        btn_row = ttk.Frame(dialog)
        btn_row.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
        ttk.Button(btn_row, text="キャンセル", command=_on_cancel).pack(side="right")
        ttk.Button(btn_row, text="OK", command=_on_ok).pack(side="right", padx=(0, 4))

        dialog.wait_window()
        return result["path"]

    # ---------- 文字送信 ----------
    @staticmethod
    def _line_spacing_factor(size_mm: float, line_spacing_mm: float) -> float:
        """行間(mm)を文字サイズ(mm)に対する倍率(pipeline.line_spacing_factor)に変換する。

        行間を文字サイズと独立に指定できるようにするための変換。factor=1.0が
        従来どおり「行間=文字サイズ」の状態に対応する。
        """
        if size_mm <= 0:
            return 1.0
        return line_spacing_mm / size_mm

    @staticmethod
    def _max_count(limit_mm: float, size_mm: float, factor: float) -> float:
        """`canvas_mm = size_mm * ((n-1)*factor + 1) <= limit_mm` を満たす最大のnを返す。

        `_update_draw_guide`(赤線ガイド)と`_on_auto_wrap`(自動改行)の両方で、
        「範囲(mm)に収まる最大文字数」を同じ式から求めるために共通化している。
        """
        if size_mm <= 0:
            return 0.0
        if factor <= 0:
            return max(limit_mm / size_mm, 0.0)
        return max((limit_mm / size_mm - 1.0) / factor + 1.0, 0.0)

    def _get_text_and_canvas(
        self, size_mm: float, letter_spacing_factor: float = 1.0, line_spacing_factor: float = 1.0
    ) -> tuple[str, float, float]:
        """Textウィジェットから文字列を取得し、行数・列数から外周確認用のキャンバスサイズ(mm)を概算する。

        実際の描画時のY方向サイズは`pipeline.build_plot_job`が実際の行数から
        逆算するため、ここで計算するcanvas_h_mmは描画結果そのものには影響しない
        (外周確認や送信前確認ダイアログなど、パイプライン実行前の概算表示にのみ使う)。
        canvas_w_mm/canvas_h_mmは配置ピッチ(letter_spacing_factor/line_spacing_factor倍)で
        (n-1)個の間隔＋最後の1文字分として計算し、`pipeline.rasterize_grid`の配置と一致させる。
        """
        text = self.text_widget.get("1.0", "end-1c")
        lines = text.split("\n")
        n_cols = max((len(line) for line in lines), default=1)
        n_rows = len(lines)
        canvas_w_mm = size_mm * (max(n_cols - 1, 0) * letter_spacing_factor + 1)
        canvas_h_mm = size_mm * (max(n_rows - 1, 0) * line_spacing_factor + 1)
        return text, canvas_w_mm, canvas_h_mm

    # ---------- 自動改行 ----------
    def _on_auto_wrap(self) -> None:
        """現在の範囲設定(横方向・最大X)をもとに、入力文字列へ自動で改行を挿入する。

        ユーザーが手動で入力した改行(段落区切り)はそのまま尊重し、各行を
        独立に折り返す。1行に収まる最大文字数は赤線ガイドと同じ`_max_count`の式
        (canvas_w_mm <= max_x を満たす最大文字数)で求める。固定ピッチのグリッド
        配置(`rasterize_grid`)に合わせ、文字幅は字種によらず一律1文字=1列として扱う。
        """
        try:
            max_x = float(self.max_x_var.get())
            size_mm = float(self.size_var.get())
            letter_spacing_factor = float(self.letter_spacing_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("エラー", "範囲(最大X)/文字サイズ/文字間隔を確認してください")
            return
        if size_mm <= 0:
            messagebox.showerror("エラー", "文字サイズを確認してください")
            return

        max_cols = int(self._max_count(max_x, size_mm, letter_spacing_factor))
        if max_cols < 1:
            messagebox.showerror("エラー", "現在の範囲設定では1文字も描画範囲に収まりません")
            return

        text = self.text_widget.get("1.0", "end-1c")
        wrapped_lines: list[str] = []
        for line in text.split("\n"):
            if len(line) <= max_cols:
                wrapped_lines.append(line)
                continue
            for i in range(0, len(line), max_cols):
                wrapped_lines.append(line[i : i + max_cols])
        wrapped_text = "\n".join(wrapped_lines)

        if wrapped_text == text:
            self.job_status_var.set("自動改行: 変更はありませんでした(すでに範囲内です)")
            return

        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", wrapped_text)
        self.job_status_var.set(f"自動改行しました(横約{max_cols}文字ごと)")

    # ---------- 描画範囲ガイド(赤線) ----------
    def _on_text_modified(self, event: tk.Event) -> None:
        self.text_widget.edit_modified(False)
        self._update_draw_guide()

    def _update_draw_guide(self, *_args) -> None:
        """文字入力欄に、現在の範囲設定(max_x/max_y)・文字サイズ・文字間隔・行間から
        算出した「これ以上入力すると描画範囲をはみ出す」境目を赤線で重ね描きする。

        あくまで目安表示であり、実際の描画幅は文字の字形(グリフのアドバンス幅は
        セル幅と厳密には一致しない)によって多少前後する。入力を妨げる禁止制御は行わない。
        """
        try:
            max_x = float(self.max_x_var.get())
            max_y = float(self.max_y_var.get())
            size_mm = float(self.size_var.get())
            letter_spacing_factor = float(self.letter_spacing_var.get())
            line_spacing_mm = float(self.line_spacing_var.get())
        except (ValueError, tk.TclError):
            return
        if size_mm <= 0:
            return
        line_spacing_factor = self._line_spacing_factor(size_mm, line_spacing_mm)

        max_cols = self._max_count(max_x, size_mm, letter_spacing_factor)
        max_rows = self._max_count(max_y, size_mm, line_spacing_factor)

        # 赤線のピクセル位置は近似(下記コメント参照)なので、近似誤差のない
        # 厳密値(何文字目・何行目まで収まるか)をラベルに数値でも表示する。
        # 文字入力欄が狭い(=小さい文字÷広い範囲で列数・行数が多い)場合、
        # 赤線だけでは欄の右端/下端に張り付いてしまい目安にならないため。
        self.guide_label_var.set(
            f"文字列 (Enterで改行) ／ 目安: 横約{int(max_cols)}文字・縦約{int(max_rows)}行まで"
            "(赤線=欄内に収まる範囲のみの近似表示)"
        )

        widget_w = self.text_widget.winfo_width()
        widget_h = self.text_widget.winfo_height()
        if widget_w <= 1 or widget_h <= 1:
            # ウィジェットがまだレイアウト確定前の可能性があるので少し待って再試行する
            self.root.after(50, self._update_draw_guide)
            return

        # 列方向は文字ごとに実際の描画幅が違う(全角/半角混在、字種による差。
        # 実測例: meiryo 13ptで平仮名混じりの平均は約15.4px/文字だが「国」単体
        # は17px と字種だけでも1割以上ズレる)ため、固定の文字幅定数では
        # ズレが蓄積して境界文字数が数文字分ズレてしまう。実際に入力されている
        # 文字があれば、その位置をTkの実測(bbox)でそのまま使うことで誤差を
        # なくす。入力がその文字数に届いていない場合のみ、平均的な全角文字幅
        # (代表として「国」を計測)で近似する。行方向は同一フォントで行高が
        # 一定なため、この誤差は生じない。
        lines_text = self.text_widget.get("1.0", "end-1c").split("\n")
        char_w = max(self._text_font.measure("国"), 1)
        line_h = max(self._text_font.metrics("linespace"), 1)

        col_idx = int(max_cols)
        guide_x = max_cols * char_w
        for row_i, line in enumerate(lines_text, start=1):
            if len(line) >= col_idx:
                bbox = self.text_widget.bbox(f"{row_i}.{col_idx}")
                if bbox:
                    guide_x = float(bbox[0])
                break

        row_idx = int(max_rows) + 1
        guide_y = max_rows * line_h
        if len(lines_text) >= row_idx:
            bbox = self.text_widget.bbox(f"{row_idx}.0")
            if bbox:
                guide_y = float(bbox[1])

        guide_x = min(guide_x, widget_w)
        guide_y = min(guide_y, widget_h)

        self._guide_v.place(in_=self.text_widget, x=guide_x, y=0, width=2, height=widget_h)
        self._guide_h.place(in_=self.text_widget, x=0, y=guide_y, width=widget_w, height=2)
        self._guide_v.lift()
        self._guide_h.lift()

    def _on_outline_check(self) -> None:
        """描画範囲の外周だけをペンアップのまま低速でなぞり、実際にX+(右)・
        Y-(下)方向へ向かって動くか目視確認するための安全確認モード（インクは出ない）。
        """
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            size_mm = float(self.size_var.get())
            letter_spacing_factor = float(self.letter_spacing_var.get())
            line_spacing_mm = float(self.line_spacing_var.get())
        except ValueError:
            messagebox.showerror("エラー", "文字サイズ/文字間隔/行間を確認してください")
            return
        line_spacing_factor = self._line_spacing_factor(size_mm, line_spacing_mm)

        _, canvas_w_mm, canvas_h_mm = self._get_text_and_canvas(
            size_mm, letter_spacing_factor, line_spacing_factor
        )

        if not messagebox.askyesno(
            "外周確認",
            f"描画範囲: X 0〜{canvas_w_mm:.1f}mm, Y 0〜-{canvas_h_mm:.1f}mm\n"
            "ペンは動かさず(Zコマンドなし)、低速(100mm/min)でXYだけを外周に沿って動かします。\n"
            "電源投入直後のキャリッジ位置が、この矩形の(0,0)角＝書き始めたい文章の左上に"
            "対応している想定です。\n"
            "X+方向が右、Y-方向が下に動くか、必ず目視で確認してください。\n"
            "続行すると、現在位置を作業原点(0,0,0)にゼロ設定してから送信します。\n"
            "異常があればすぐ電源を切ってください。",
        ):
            return

        lines = build_outline_check_gcode(canvas_w_mm, canvas_h_mm, feed_rate=100.0)
        try:
            conn.zero_work_origin()
            parsed = parse_status(conn.status())
            if parsed.get("machine_position") is not None:
                self.last_wco = parsed["machine_position"]

            total = len(lines)
            for i, line in enumerate(lines):
                conn.send_line(line)
                self.job_status_var.set(f"外周確認送信中... {i + 1}/{total}")
                self.root.update_idletasks()
            self.job_status_var.set("外周確認 完了。X+が右、Y-が下に動いたか確認してください。")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))
            self.job_status_var.set(f"エラーで中断: {e}")
        finally:
            self._refresh_status()

    def _on_send_text(self) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        if self._warn_if_sending():
            return
        try:
            size_mm = float(self.size_var.get())
            z_down = float(self.zdown_var.get())
            z_feed = float(self.z_feed_var.get())
            final_lift_mm = float(self.final_lift_var.get())
            max_x = float(self.max_x_var.get())
            max_y = float(self.max_y_var.get())
            draw_feed = float(self.draw_feed_var.get())
            travel_feed = float(self.travel_feed_var.get())
            simplify_tolerance_mm = float(self.simplify_tolerance_var.get())
            letter_spacing_factor = float(self.letter_spacing_var.get())
            line_spacing_mm = float(self.line_spacing_var.get())
        except ValueError:
            messagebox.showerror("エラー", "数値項目を確認してください")
            return
        line_spacing_factor = self._line_spacing_factor(size_mm, line_spacing_mm)

        text, canvas_w_mm, canvas_h_mm = self._get_text_and_canvas(
            size_mm, letter_spacing_factor, line_spacing_factor
        )
        if not text:
            messagebox.showerror("エラー", "文字列を入力してください")
            return

        config = PipelineConfig(
            font_path=Path(self.font_var.get()),
            font_size_pt=280.0,
            cell_px=(400, 400),
            canvas_size_mm=(canvas_w_mm, canvas_h_mm),
            simplify_tolerance_mm=simplify_tolerance_mm if self.fast_mode_var.get() else None,
            letter_spacing_factor=letter_spacing_factor,
            line_spacing_factor=line_spacing_factor,
        )
        try:
            self.job_status_var.set("G-code生成中...")
            self.root.update_idletasks()
            _, job = run_text_pipeline(text, config)
        except Exception as e:
            messagebox.showerror("生成エラー", str(e))
            self.job_status_var.set("")
            return

        violations = check_xy_bounds(job, max_x=max_x, max_y=max_y)
        if violations:
            messagebox.showerror("範囲エラー", "\n".join(violations))
            self.job_status_var.set("")
            return

        actual_w_mm, actual_h_mm = job.canvas_size_mm
        next_x_mm, next_y_mm = job.next_line_start_mm
        mode_desc = (
            f"高速モード(許容誤差{simplify_tolerance_mm:.2f}mm)" if self.fast_mode_var.get() else "通常モード"
        )
        if not messagebox.askyesno(
            "送信確認",
            f"「{text}」を{size_mm:.0f}mmサイズで描画します。[{mode_desc}]\n{job.stats.summary()}\n\n"
            f"描画範囲: X 0〜{actual_w_mm:.1f}mm, Y 0〜-{actual_h_mm:.1f}mm\n"
            f"送り速度: 描画{draw_feed:.0f}mm/min, 移動{travel_feed:.0f}mm/min, Z{z_feed:.0f}mm/min\n"
            f"終了時はペンを追加で{final_lift_mm:.1f}mm退避させ、"
            f"書き終わりの次の行の先頭(X{next_x_mm:.1f}, Y{next_y_mm:.1f})へ移動して終わります\n"
            "原点(0,0)＝1文字目の左上を基準に、X+(右)・Y-(下)方向へ読み順に描画します。\n"
            "実際にX+が右・Y-が下に動くか未確認の場合は、先に「外周確認」ボタンで低速動作を目視確認してください。\n\n"
            "電源投入位置から見て、キャリッジが書き始めたい位置にあり、"
            "この範囲(+X, -Y方向)に十分な余裕がありますか？\n"
            "続行すると、現在位置を作業原点(0,0,0)にゼロ設定してから送信します。",
        ):
            self.job_status_var.set("")
            return

        pen = RelativeZPenController(
            down_travel_mm=z_down,
            z_feed=z_feed,
            final_lift_mm=final_lift_mm,
            initial_extra_down_mm=self._last_final_lift_mm,
        )
        lines = build_gcode_lines(job, pen=pen, feed_rate=draw_feed, travel_feed_rate=travel_feed)

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self.send_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")

        def worker() -> None:
            cancelled_at: int | None = None
            completed = False
            try:
                conn.zero_work_origin()
                parsed = parse_status(conn.status())
                if parsed.get("machine_position") is not None:
                    self.last_wco = parsed["machine_position"]  # ゼロ設定直後なのでWCO=現在MPosそのもの

                total = len(lines)
                for i, line in enumerate(lines):
                    if cancel_event.is_set():
                        cancelled_at = i
                        break
                    conn.send_line(line)
                    if i % 10 == 0:
                        self.root.after(0, lambda i=i: self.job_status_var.set(f"送信中... {i + 1}/{total}"))

                if cancelled_at is not None:
                    msg = (
                        f"キャンセルしました({cancelled_at}/{total}行送信済み、フィードホールド中)。"
                        "「再開」で続行するか「ソフトリセット」で完全停止してください。"
                    )
                    self.root.after(0, lambda: self.job_status_var.set(msg))
                else:
                    self._last_final_lift_mm = final_lift_mm  # 次回送信のZ基準ズレ補正に使う
                    self.root.after(0, lambda: self.job_status_var.set("送信完了"))
                    # 最終行の'ok'はプランナーバッファに積まれた時点で返るため、この時点では
                    # まだ物理的にRun状態(動作中)の可能性がある。$系コマンドはIdle状態でしか
                    # 受け付けられず(error:8)、Run中に送ると失敗する。バックグラウンドスレッド
                    # なのでUIをブロックせずにIdleへ落ち着くまでポーリングできる。
                    idle_wait = 0.0
                    while idle_wait < 10.0:
                        if parse_status(conn.status()).get("state") == "Idle":
                            break
                        idle_wait += 0.2
                    completed = True
            except GrblError as e:
                self.root.after(0, lambda: messagebox.showerror("GRBLエラー", str(e)))
                self.root.after(0, lambda: self.job_status_var.set(f"エラーで中断: {e}"))
            finally:
                # 他のボタンの送信中ガード(_warn_if_sending)が内部呼び出しの
                # _refresh_statusや_auto_disable_torque_holdまで誤ってブロックしないよう、
                # after登録より先にワーカースレッド内で同期的にリセットしておく。
                self._cancel_event = None
                self._send_thread = None
                self.root.after(0, self._refresh_status)
                self.root.after(0, lambda: self.send_btn.config(state="normal"))
                self.root.after(0, lambda: self.cancel_btn.config(state="disabled"))
                if completed:
                    self.root.after(0, self._auto_disable_torque_hold)

        self._send_thread = threading.Thread(target=worker, daemon=True)
        self._send_thread.start()

    def _on_cancel_send(self) -> None:
        """送信中のG-code転送をキャンセルする。

        キャンセル要求フラグを立てて次の行を送らせないようにするのと同時に、
        フィードホールド(!)を即座に送って現在実行中の移動を止める。バッファに
        残ったコマンドは破棄されないため、続行するなら「再開」、完全に止める
        なら「ソフトリセット」をユーザーに選んでもらう設計にしている。
        """
        if self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_btn.config(state="disabled")
        if self.conn is not None:
            try:
                self.conn.feed_hold()
            except Exception:
                pass
        self.job_status_var.set("キャンセル要求を送信しました(フィードホールド中)...")

    # ---------- シミュレーション ----------
    def _on_simulate(self) -> None:
        """実機に送信せず、実際のフォントで生成したストロークを画面上でプレビューする。

        「送信」と同じパイプライン(run_text_pipeline)を通すため、実際に送信した
        場合とほぼ同じ形・範囲が事前に確認できる(接続不要)。
        """
        try:
            size_mm = float(self.size_var.get())
            letter_spacing_factor = float(self.letter_spacing_var.get())
            line_spacing_mm = float(self.line_spacing_var.get())
            max_x = float(self.max_x_var.get())
            max_y = float(self.max_y_var.get())
            simplify_tolerance_mm = float(self.simplify_tolerance_var.get())
        except ValueError:
            messagebox.showerror("エラー", "数値項目を確認してください")
            return
        line_spacing_factor = self._line_spacing_factor(size_mm, line_spacing_mm)

        text, canvas_w_mm, canvas_h_mm = self._get_text_and_canvas(
            size_mm, letter_spacing_factor, line_spacing_factor
        )
        if not text:
            messagebox.showerror("エラー", "文字列を入力してください")
            return

        config = PipelineConfig(
            font_path=Path(self.font_var.get()),
            font_size_pt=280.0,
            cell_px=(400, 400),
            canvas_size_mm=(canvas_w_mm, canvas_h_mm),
            simplify_tolerance_mm=simplify_tolerance_mm if self.fast_mode_var.get() else None,
            letter_spacing_factor=letter_spacing_factor,
            line_spacing_factor=line_spacing_factor,
        )
        self.job_status_var.set("シミュレーション生成中...")
        self.root.update_idletasks()
        try:
            _, job = run_text_pipeline(text, config)
        except Exception as e:
            messagebox.showerror("生成エラー", str(e))
            self.job_status_var.set("")
            return
        self.job_status_var.set("")
        self._show_simulation_window(job, max_x, max_y)

    def _show_simulation_window(self, job: PlotJob, max_x: float, max_y: float) -> None:
        """描画範囲(赤枠)と実際の文字ストローク(黒線)を重ねて表示するプレビュー窓。

        mm座標系は本体の描画と同じ(原点=1文字目左上、X+右、Y-下)。赤枠は
        「範囲設定」で指定した可動範囲、黒線が実際に送信されるストローク。
        """
        top = tk.Toplevel(self.root)
        top.title("シミュレーション表示")

        canvas_w_px, canvas_h_px = 700, 700
        canvas = tk.Canvas(top, width=canvas_w_px, height=canvas_h_px, bg="white")
        canvas.pack(padx=8, pady=8)

        range_w_mm = max(max_x, job.canvas_size_mm[0], 1.0)
        range_h_mm = max(max_y, job.canvas_size_mm[1], 1.0)
        margin_px = 20
        scale = min(
            (canvas_w_px - 2 * margin_px) / range_w_mm,
            (canvas_h_px - 2 * margin_px) / range_h_mm,
        )

        def to_canvas(x_mm: float, y_mm: float) -> tuple[float, float]:
            return margin_px + x_mm * scale, margin_px + (-y_mm) * scale

        rx0, ry0 = to_canvas(0.0, 0.0)
        rx1, ry1 = to_canvas(max_x, -max_y)
        canvas.create_rectangle(rx0, ry0, rx1, ry1, outline="red", width=2)

        for poly in job.polylines:
            pts = poly.points
            if len(pts) < 2:
                continue
            coords: list[float] = []
            for px, py in pts:
                cx, cy = to_canvas(float(px), float(py))
                coords.extend([cx, cy])
            canvas.create_line(*coords, fill="black", width=1.2)

        ttk.Label(
            top,
            text=(
                f"赤枠 = 現在の範囲設定 X:0〜{max_x:.1f}mm Y:0〜-{max_y:.1f}mm\n"
                f"文字の実描画範囲: X:0〜{job.canvas_size_mm[0]:.1f}mm "
                f"Y:0〜-{job.canvas_size_mm[1]:.1f}mm\n{job.stats.summary()}"
            ),
            justify="left",
        ).pack(padx=8, pady=(0, 8), anchor="w")


def main() -> None:
    root = tk.Tk()
    GrblControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
