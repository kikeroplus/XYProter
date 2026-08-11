"""matplotlibによる各ステージの可視化。計算コア（他モジュール）はここに依存しない。"""
from __future__ import annotations

import numpy as np
from skimage.morphology import dilation, disk

from .pipeline import GlyphPipelineResult
from .types import PlotJob, Polyline

_ROLE_COLOR = {"junction": "red", "endpoint": "blue", "synthetic": "green"}

_JP_FONT_CANDIDATES = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP"]


def _configure_japanese_font() -> None:
    import matplotlib

    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = _JP_FONT_CANDIDATES + list(
        matplotlib.rcParams["font.sans-serif"]
    )
    matplotlib.rcParams["axes.unicode_minus"] = False


def simulate_pen_preview(skeleton: np.ndarray, pen_width_px: float) -> np.ndarray:
    """指定したペン幅(px)で実際に描画した場合の見た目を dilation で近似する。"""
    radius = max(1, round(pen_width_px / 2))
    return dilation(skeleton, footprint=disk(radius))


def render_stage_panels(
    result: GlyphPipelineResult, trails_px: list[Polyline], pen_width_px: float = 6.0
):
    """1文字について、二値化〜ペン幅シミュレーションまで6段階を並べて可視化する。"""
    import matplotlib.pyplot as plt
    from matplotlib import cm

    _configure_japanese_font()
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    ax = axes.ravel()

    ax[0].imshow(result.raster.binary, cmap="gray_r")
    ax[0].set_title(f"1. 二値化「{result.raster.char}」")

    ax[1].imshow(result.skeleton_result.raw_skeleton, cmap="gray_r")
    ax[1].set_title("2. 生スケルトン")

    ax[2].imshow(result.skeleton_result.pruned_skeleton, cmap="gray_r")
    ax[2].set_title(f"3. ヒゲ除去後 (閾値={result.skeleton_result.spur_threshold_px:.1f}px)")

    ax[3].imshow(result.skeleton_result.pruned_skeleton, cmap="gray_r")
    for node in result.graph_merged.nodes.values():
        color = _ROLE_COLOR.get(node.role, "gray")
        ax[3].scatter([node.position[1]], [node.position[0]], c=color, s=30, zorder=3)
    ax[3].set_title("4. 交差点集約後 (赤=分岐 青=端点 緑=人工)")

    ax[4].imshow(np.zeros_like(result.skeleton_result.pruned_skeleton), cmap="gray")
    cmap = cm.get_cmap("viridis")
    n = len(trails_px)
    for i, poly in enumerate(trails_px):
        pts = poly.points
        ax[4].plot(pts[:, 1], pts[:, 0], color=cmap(i / max(1, n - 1)), linewidth=1.5)
    ax[4].set_title(f"5. パス抽出 (描画順, {n}本)")

    preview = simulate_pen_preview(result.skeleton_result.pruned_skeleton, pen_width_px)
    ax[5].imshow(preview, cmap="gray_r")
    ax[5].set_title(f"6. ペン幅シミュレーション ({pen_width_px}px)")

    for a in ax:
        a.axis("off")
        a.set_aspect("equal")
    fig.tight_layout()
    return fig


def render_grid_preview(job: PlotJob, pen_width_mm: float = 0.5, show_travel: bool = False):
    """複数文字のグリッド全体を、確定した描画順でプレビューする。"""
    import matplotlib.pyplot as plt

    _configure_japanese_font()
    width_mm, height_mm = job.canvas_size_mm
    fig_w = max(width_mm / 25.4, 4.0)
    fig_h = max(height_mm / 25.4, 3.0) + 0.4  # タイトル用の余白
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    linewidth_pt = pen_width_mm / 25.4 * 72
    prev_end: np.ndarray | None = None
    for poly in job.polylines:
        pts = poly.points
        ax.plot(pts[:, 0], pts[:, 1], color="black", linewidth=linewidth_pt, solid_capstyle="round")
        if show_travel and prev_end is not None:
            ax.plot(
                [prev_end[0], pts[0, 0]],
                [prev_end[1], pts[0, 1]],
                color="red",
                linestyle=":",
                linewidth=0.5,
            )
        prev_end = pts[-1]

    ax.set_xlim(0, width_mm)
    ax.set_ylim(-height_mm, 0)  # 原点=左上、Y-方向が下
    ax.set_aspect("equal")
    ax.set_title(job.stats.summary(), fontsize=9, wrap=True)
    fig.tight_layout()
    return fig
