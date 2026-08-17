"""パイプライン全ステージで共有するデータ構造。

データフロー:
    GlyphRaster -> SkeletonResult -> SkeletonGraph(統合前) -> SkeletonGraph(統合後)
        -> List[Polyline] (px空間) -> PlotJob (mm空間)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

NodeRole = Literal["endpoint", "junction", "synthetic"]


@dataclass
class GlyphRaster:
    """1文字分のラスタライズ結果。"""

    char: str
    binary: np.ndarray  # bool, shape (H, W). True = インク（描画対象画素）
    canvas_px: tuple[int, int]  # (width, height)
    font_path: Path
    font_index: int
    font_size_pt: float
    cell_origin_px: tuple[float, float] = (0.0, 0.0)  # グリッド配置時の左上オフセット (row_offset, col_offset)


@dataclass
class SkeletonResult:
    """細線化＋スパー除去の結果。"""

    raw_skeleton: np.ndarray  # bool
    pruned_skeleton: np.ndarray  # bool
    stroke_width_px: float
    spur_threshold_px: float
    n_components_before: int
    n_components_after: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class SkeletonNode:
    id: int
    pixels: list[tuple[int, int]]  # (row, col) 元画素群
    position: tuple[float, float]  # (row, col) float、統合後は重心
    role: NodeRole


@dataclass
class SkeletonEdge:
    id: int
    node_a: int
    node_b: int
    pixels: list[tuple[int, int]]  # node_a -> node_b の順に並んだ (row, col) 画素列（両端ノード込み）
    length_px: float


@dataclass
class SkeletonGraph:
    nodes: dict[int, SkeletonNode]
    edges: dict[int, SkeletonEdge]
    image_shape: tuple[int, int]

    def degree(self, node_id: int) -> int:
        return sum(1 for e in self.edges.values() if e.node_a == node_id or e.node_b == node_id)

    def incident_edges(self, node_id: int) -> list[SkeletonEdge]:
        return [e for e in self.edges.values() if e.node_a == node_id or e.node_b == node_id]


@dataclass
class Polyline:
    points: np.ndarray  # shape (N, 2)、列は (row, col) または (x_mm, y_mm)。文脈で判断。
    closed: bool = False
    source_edge_ids: list[int] = field(default_factory=list)

    def reversed(self) -> "Polyline":
        return Polyline(
            points=self.points[::-1].copy(),
            closed=self.closed,
            source_edge_ids=list(reversed(self.source_edge_ids)),
        )

    def length(self) -> float:
        if len(self.points) < 2:
            return 0.0
        diffs = np.diff(self.points, axis=0)
        return float(np.hypot(diffs[:, 0], diffs[:, 1]).sum())


@dataclass
class PlotStats:
    n_paths: int
    total_draw_distance: float
    total_travel_distance: float
    n_pen_lifts: int

    def summary(self) -> str:
        return (
            f"総パス数: {self.n_paths}, "
            f"総描画距離: {self.total_draw_distance:.2f}, "
            f"総ペンアップ移動距離: {self.total_travel_distance:.2f}, "
            f"ペンアップダウン回数: {self.n_pen_lifts}"
        )


@dataclass
class PlotJob:
    polylines: list[Polyline]  # mm座標、描画順に確定済み
    canvas_size_mm: tuple[float, float]  # (width, height)
    stats: PlotStats
    # 描画完了後にペンを移動させる先(mm座標、原点=先頭文字の左上)。
    # 既定は「書き終わりの次の行の先頭」で、build_gcode_linesの終端移動先として使う。
    next_line_start_mm: tuple[float, float] = (0.0, 0.0)
