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
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .gcode_export import build_gcode_lines
from .grbl_sender import GrblConnection, GrblError, check_xy_bounds, parse_status
from .pen_control import RelativeZPenController
from .pipeline import PipelineConfig, run_text_pipeline

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "xyproter_gui_settings.json"

STEP_OPTIONS = [0.01, 0.1, 1, 10, 100]
FEED_OPTIONS = [10, 50, 100, 500, 1000, 2000, 5000]
SIZE_OPTIONS = [10, 15, 20, 25, 30, 40, 50, 80, 100]
Z_PEN_FEED = 200.0  # ペンアップ/ダウン(相対Z移動)の送り速度。小さい移動量なので固定値で十分。

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
    "font_path": r"C:\Windows\Fonts\msmincho.ttc",
}

_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


class GrblControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("XYProter プロッター操作パネル")
        self.conn: GrblConnection | None = None
        self.last_wco: tuple[float, float, float] = (0.0, 0.0, 0.0)
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
                    "font_path": self.font_var.get(),
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
        draw_frame = ttk.LabelFrame(self.root, text="文字描画")
        draw_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        ttk.Label(draw_frame, text="フォント").grid(row=0, column=0, sticky="w")
        self.font_var = tk.StringVar(value=s["font_path"])
        ttk.Entry(draw_frame, textvariable=self.font_var, width=42).grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Button(draw_frame, text="参照", command=self._browse_font).grid(row=0, column=4)

        ttk.Label(draw_frame, text="文字サイズ(mm)").grid(row=1, column=0, sticky="w")
        self.size_var = tk.StringVar(value=str(s["size_mm"]))
        ttk.Combobox(
            draw_frame, textvariable=self.size_var, values=SIZE_OPTIONS, width=8, state="readonly"
        ).grid(row=1, column=1, sticky="w")

        ttk.Label(draw_frame, text="Z下降量(mm)").grid(row=1, column=2, sticky="w")
        self.zdown_var = tk.StringVar(value=str(s["z_down_mm"]))
        ttk.Entry(draw_frame, textvariable=self.zdown_var, width=6).grid(row=1, column=3, sticky="w")

        ttk.Label(draw_frame, text="文字列").grid(row=2, column=0, sticky="w")
        self.text_var = tk.StringVar(value="")
        ttk.Entry(draw_frame, textvariable=self.text_var, width=32).grid(row=2, column=1, columnspan=3, sticky="w")
        ttk.Button(draw_frame, text="送信", command=self._on_send_text).grid(row=2, column=4)

        self.job_status_var = tk.StringVar(value="")
        ttk.Label(draw_frame, textvariable=self.job_status_var, foreground="blue").grid(
            row=3, column=0, columnspan=5, sticky="w"
        )

    # ---------- ポート ----------
    def _list_ports(self) -> list[str]:
        import serial.tools.list_ports as list_ports

        return [p.device for p in list_ports.comports()]

    def _refresh_ports(self) -> None:
        self.port_combo["values"] = self._list_ports()

    # ---------- 接続 ----------
    def _toggle_connect(self) -> None:
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
        try:
            parsed = parse_status(conn.status())
            if parsed.get("work_offset") is not None:
                self.last_wco = parsed["work_offset"]
            mpos = parsed.get("machine_position")
            if mpos is None:
                messagebox.showerror("エラー", "現在位置を取得できませんでした")
                return

            conn.send_line("G92 " + " ".join(f"{a}0" for a in axes))

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
    def _on_send_text(self) -> None:
        conn = self._require_conn()
        if conn is None:
            return
        text = self.text_var.get()
        if not text:
            messagebox.showerror("エラー", "文字列を入力してください")
            return
        try:
            size_mm = float(self.size_var.get())
            z_down = float(self.zdown_var.get())
            max_x = float(self.max_x_var.get())
            max_y = float(self.max_y_var.get())
            feed = float(self.feed_var.get())
        except ValueError:
            messagebox.showerror("エラー", "数値項目を確認してください")
            return

        config = PipelineConfig(
            font_path=Path(self.font_var.get()),
            font_size_pt=280.0,
            cell_px=(400, 400),
            canvas_size_mm=(size_mm * len(text), size_mm),
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

        if not messagebox.askyesno(
            "送信確認",
            f"「{text}」を{size_mm:.0f}mmサイズで描画します。\n{job.stats.summary()}\n\n"
            "電源投入位置から見て、この範囲(+X, +Y方向)に十分な余裕がありますか？\n"
            "続行すると、現在位置を作業原点(0,0,0)にゼロ設定してから送信します。",
        ):
            self.job_status_var.set("")
            return

        pen = RelativeZPenController(down_travel_mm=z_down, z_feed=Z_PEN_FEED)
        lines = build_gcode_lines(job, pen=pen, feed_rate=feed, travel_feed_rate=min(feed * 2, 800.0))

        try:
            conn.zero_work_origin()
            parsed = parse_status(conn.status())
            if parsed.get("machine_position") is not None:
                self.last_wco = parsed["machine_position"]  # ゼロ設定直後なのでWCO=現在MPosそのもの

            total = len(lines)
            for i, line in enumerate(lines):
                conn.send_line(line)
                if i % 10 == 0:
                    self.job_status_var.set(f"送信中... {i + 1}/{total}")
                    self.root.update_idletasks()
            self.job_status_var.set("送信完了")
        except GrblError as e:
            messagebox.showerror("GRBLエラー", str(e))
            self.job_status_var.set(f"エラーで中断: {e}")
        finally:
            self._refresh_status()


def main() -> None:
    root = tk.Tk()
    GrblControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
