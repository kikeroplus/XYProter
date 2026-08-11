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
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .gcode_export import build_gcode_lines, build_outline_check_gcode
from .grbl_sender import GrblConnection, GrblError, check_xy_bounds, parse_status
from .pen_control import RelativeZPenController
from .pipeline import PipelineConfig, run_text_pipeline

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "xyproter_gui_settings.json"

STEP_OPTIONS = [0.01, 0.1, 1, 10, 100]
FEED_OPTIONS = [10, 50, 100, 500, 1000, 2000, 5000]
SIZE_OPTIONS = [5, 6, 7, 8, 9, 10, 15, 20, 25, 30, 40, 50, 80, 100]
Z_FEED_OPTIONS = [200, 400, 600, 800, 1000]
# $110/$111(X/Y最大送り速度)=800mm/minが機体の上限のため、それ以下の現実的な値のみを選択肢にする
XY_FEED_OPTIONS = [100, 200, 300, 400, 600, 800]
LETTER_SPACING_OPTIONS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

DEFAULT_SETTINGS = {
    "port": "",
    "baud": 115200,
    "max_x": 200.0,
    "max_y": 200.0,
    "min_z": -50.0,
    "max_z": 50.0,
    "step_mm": 1.0,
    "feed": 500.0,
    "size_mm": 30.0,
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
                    "feed": float(self.feed_var.get()),
                    "size_mm": float(self.size_var.get()),
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

        self.connect_btn = ttk.Button(conn_frame, text="接続", command=self._toggle_connect)
        self.connect_btn.grid(row=0, column=4, padx=4)

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
        ttk.Button(zero_frame, text="X=0", command=lambda: self._on_zero("X")).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(zero_frame, text="Y=0", command=lambda: self._on_zero("Y")).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(zero_frame, text="Z=0", command=lambda: self._on_zero("Z")).grid(row=0, column=2, padx=4, pady=4)
        ttk.Button(zero_frame, text="XYZ=0", command=lambda: self._on_zero("XYZ")).grid(row=0, column=3, padx=4, pady=4)

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

        # ---- 移動速度 ----
        speed_frame = ttk.LabelFrame(self.root, text="移動速度")
        speed_frame.grid(row=3, column=0, sticky="nsew", padx=6, pady=4)
        ttk.Label(speed_frame, text="ステップ(mm)").grid(row=0, column=0)
        self.step_var = tk.StringVar(value=str(s["step_mm"]))
        ttk.Combobox(
            speed_frame, textvariable=self.step_var, values=STEP_OPTIONS, width=8, state="readonly"
        ).grid(row=0, column=1)
        ttk.Label(speed_frame, text="フィード(mm/min)").grid(row=1, column=0)
        self.feed_var = tk.StringVar(value=str(s["feed"]))
        ttk.Combobox(
            speed_frame, textvariable=self.feed_var, values=FEED_OPTIONS, width=8, state="readonly"
        ).grid(row=1, column=1)

        # ---- ナビゲーション ----
        nav_frame = ttk.LabelFrame(self.root, text="ナビゲーション")
        nav_frame.grid(row=3, column=1, sticky="nsew", padx=6, pady=4)
        ttk.Button(nav_frame, text="Y+", command=lambda: self._on_jog("Y", 1)).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(nav_frame, text="X-", command=lambda: self._on_jog("X", -1)).grid(row=1, column=0, padx=4, pady=4)
        ttk.Button(nav_frame, text="X+", command=lambda: self._on_jog("X", 1)).grid(row=1, column=2, padx=4, pady=4)
        ttk.Button(nav_frame, text="Y-", command=lambda: self._on_jog("Y", -1)).grid(row=2, column=1, padx=4, pady=4)
        ttk.Button(nav_frame, text="Z+", command=lambda: self._on_jog("Z", 1)).grid(row=0, column=3, padx=4, pady=4)
        ttk.Button(nav_frame, text="Z-", command=lambda: self._on_jog("Z", -1)).grid(row=2, column=3, padx=4, pady=4)

        # ---- 安全操作(依頼仕様にはないが、事故歴を踏まえて追加) ----
        safety_frame = ttk.LabelFrame(self.root, text="安全操作")
        safety_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        ttk.Button(safety_frame, text="フィードホールド(!)", command=self._on_feed_hold).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(safety_frame, text="再開(~)", command=self._on_resume).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(safety_frame, text="ソフトリセット", command=self._on_soft_reset).grid(row=0, column=2, padx=4, pady=4)
        ttk.Button(safety_frame, text="アラーム解除($X)", command=self._on_unlock).grid(row=0, column=3, padx=4, pady=4)

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

        # -- 文字列入力(主役なので横長・大きめのフォントで目立たせる) --
        ttk.Label(draw_frame, text="文字列 (Enterで改行)").grid(
            row=1, column=0, sticky="w", padx=6, pady=(6, 0)
        )
        self.text_widget = tk.Text(draw_frame, width=64, height=6, wrap="none", font=("", 13))
        self.text_widget.grid(row=2, column=0, sticky="ew", padx=6, pady=(2, 6))

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
        ttk.Label(char_frame, text="文字間隔").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.letter_spacing_var = tk.StringVar(value=str(s["letter_spacing_factor"]))
        ttk.Combobox(
            char_frame,
            textvariable=self.letter_spacing_var,
            values=LETTER_SPACING_OPTIONS,
            width=6,
            state="readonly",
        ).grid(row=1, column=1, padx=4, pady=2)
        self.fast_mode_var = tk.BooleanVar(value=bool(s["fast_mode"]))
        ttk.Checkbutton(char_frame, text="高速モード", variable=self.fast_mode_var).grid(
            row=2, column=0, sticky="w", padx=4, pady=2
        )
        self.simplify_tolerance_var = tk.StringVar(value=str(s["simplify_tolerance_mm"]))
        ttk.Entry(char_frame, textvariable=self.simplify_tolerance_var, width=6).grid(
            row=2, column=1, padx=4, pady=2
        )

        speed_frame2 = ttk.LabelFrame(settings_row, text="送り速度(mm/min)")
        speed_frame2.pack(side="left", fill="y", padx=4)
        ttk.Label(speed_frame2, text="描画").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.draw_feed_var = tk.StringVar(value=str(s["draw_feed"]))
        ttk.Combobox(
            speed_frame2, textvariable=self.draw_feed_var, values=XY_FEED_OPTIONS, width=6, state="readonly"
        ).grid(row=0, column=1, padx=4, pady=2)
        ttk.Label(speed_frame2, text="移動").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.travel_feed_var = tk.StringVar(value=str(s["travel_feed"]))
        ttk.Combobox(
            speed_frame2, textvariable=self.travel_feed_var, values=XY_FEED_OPTIONS, width=6, state="readonly"
        ).grid(row=1, column=1, padx=4, pady=2)
        ttk.Label(speed_frame2, text="Z").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.z_feed_var = tk.StringVar(value=str(s["z_feed"]))
        ttk.Combobox(
            speed_frame2, textvariable=self.z_feed_var, values=Z_FEED_OPTIONS, width=6, state="readonly"
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
        ttk.Button(action_row, text="外周確認", command=self._on_outline_check).pack(side="left", padx=(0, 4))
        self.send_btn = ttk.Button(action_row, text="送信", command=self._on_send_text)
        self.send_btn.pack(side="left", padx=4)
        self.cancel_btn = ttk.Button(
            action_row, text="キャンセル", command=self._on_cancel_send, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=4)

        self.job_status_var = tk.StringVar(value="")
        ttk.Label(draw_frame, textvariable=self.job_status_var, foreground="blue", wraplength=560).grid(
            row=5, column=0, sticky="w", padx=6, pady=(0, 6)
        )

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

    # ---------- フォント選択 ----------
    def _browse_font(self) -> None:
        path = filedialog.askopenfilename(
            title="フォントファイルを選択",
            filetypes=[("フォント", "*.ttf *.ttc *.otf"), ("すべて", "*.*")],
        )
        if path:
            self.font_var.set(path)

    # ---------- 文字送信 ----------
    def _get_text_and_canvas(self, size_mm: float, letter_spacing_factor: float = 1.0) -> tuple[str, float, float]:
        """Textウィジェットから文字列を取得し、行数・列数から外周確認用のキャンバスサイズ(mm)を概算する。

        実際の描画時のY方向サイズは`pipeline.build_plot_job`が実際の行数から
        逆算するため、ここで計算するcanvas_h_mmは描画結果そのものには影響しない
        (外周確認や送信前確認ダイアログなど、パイプライン実行前の概算表示にのみ使う)。
        canvas_w_mmは列方向の配置ピッチ(letter_spacing_factor倍)で(n_cols-1)個の
        間隔＋最後の1文字分として計算し、`pipeline.rasterize_grid`の配置と一致させる。
        """
        text = self.text_widget.get("1.0", "end-1c")
        lines = text.split("\n")
        n_cols = max((len(line) for line in lines), default=1)
        n_rows = len(lines)
        canvas_w_mm = size_mm * (max(n_cols - 1, 0) * letter_spacing_factor + 1)
        canvas_h_mm = size_mm * n_rows
        return text, canvas_w_mm, canvas_h_mm

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
        except ValueError:
            messagebox.showerror("エラー", "文字サイズ/文字間隔を確認してください")
            return

        _, canvas_w_mm, canvas_h_mm = self._get_text_and_canvas(size_mm, letter_spacing_factor)

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
        except ValueError:
            messagebox.showerror("エラー", "数値項目を確認してください")
            return

        text, canvas_w_mm, canvas_h_mm = self._get_text_and_canvas(size_mm, letter_spacing_factor)
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
        mode_desc = (
            f"高速モード(許容誤差{simplify_tolerance_mm:.2f}mm)" if self.fast_mode_var.get() else "通常モード"
        )
        if not messagebox.askyesno(
            "送信確認",
            f"「{text}」を{size_mm:.0f}mmサイズで描画します。[{mode_desc}]\n{job.stats.summary()}\n\n"
            f"描画範囲: X 0〜{actual_w_mm:.1f}mm, Y 0〜-{actual_h_mm:.1f}mm\n"
            f"送り速度: 描画{draw_feed:.0f}mm/min, 移動{travel_feed:.0f}mm/min, Z{z_feed:.0f}mm/min\n"
            f"終了時はペンを追加で{final_lift_mm:.1f}mm退避させます\n"
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
            except GrblError as e:
                self.root.after(0, lambda: messagebox.showerror("GRBLエラー", str(e)))
                self.root.after(0, lambda: self.job_status_var.set(f"エラーで中断: {e}"))
            finally:
                # 他のボタンの送信中ガード(_warn_if_sending)が内部呼び出しの
                # _refresh_statusまで誤ってブロックしないよう、after登録より
                # 先にワーカースレッド内で同期的にリセットしておく。
                self._cancel_event = None
                self._send_thread = None
                self.root.after(0, self._refresh_status)
                self.root.after(0, lambda: self.send_btn.config(state="normal"))
                self.root.after(0, lambda: self.cancel_btn.config(state="disabled"))

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


def main() -> None:
    root = tk.Tk()
    GrblControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
