"""全ステージのオーケストレーション。

GlyphRaster -> SkeletonResult -> SkeletonGraph(統合前) -> SkeletonGraph(統合後)
    -> List[Polyline](px) -> PlotJob(mm)

px -> mm 変換とnumpy座標(row下向き)からプロッターY軸(上向き想定)への反転は
build_plot_job の1箇所に閉じ込める。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .graph_build import build_skeleton_graph
from .intersection_merge import check_merge_safety, merge_close_junctions
from .metrics import estimate_merge_radius
from .path_extraction import compute_stats, extract_trails, order_trails_nearest_neighbor
from .rasterize import rasterize_grid
from .skeletonize_stage import skeletonize_glyph
from .spur_pruning import remove_spurs
from .types import GlyphRaster, PlotJob, Polyline, SkeletonGraph, SkeletonResult


@dataclass
class PipelineConfig:
    font_path: Path
    font_index: int = 0
    font_size_pt: float = 200.0
    cell_px: tuple[int, int] = (512, 512)  # (width, height)
    canvas_size_mm: tuple[float, float] = (100.0, 100.0)  # (width, height)
    threshold: int = 128
    clean_noise: bool = True
    min_object_size: int = 4
    spur_threshold_px: float | None = None
    spur_factor: float = 1.4
    merge_radius_px: float | None = None
    merge_factor: float = 0.85
    columns: int | None = None


@dataclass
class GlyphPipelineResult:
    raster: GlyphRaster
    skeleton_result: SkeletonResult
    graph_raw: SkeletonGraph
    graph_merged: SkeletonGraph
    merge_warnings: list[str]


def process_glyph(raster: GlyphRaster, config: PipelineConfig) -> GlyphPipelineResult:
    """1文字分について ステージ2〜4（細線化・ヒゲ除去・交差点集約）を実行する。"""
    skel_raw = skeletonize_glyph(
        raster.binary, clean_noise=config.clean_noise, min_object_size=config.min_object_size
    )
    skel_result = remove_spurs(
        raster.binary, skel_raw, threshold_px=config.spur_threshold_px, factor=config.spur_factor
    )
    graph_raw = build_skeleton_graph(skel_result.pruned_skeleton)

    merge_radius = (
        config.merge_radius_px
        if config.merge_radius_px is not None
        else estimate_merge_radius(skel_result.stroke_width_px, config.merge_factor)
    )
    graph_merged = merge_close_junctions(graph_raw, merge_radius)
    warnings = check_merge_safety(graph_raw, graph_merged)

    return GlyphPipelineResult(
        raster=raster,
        skeleton_result=skel_result,
        graph_raw=graph_raw,
        graph_merged=graph_merged,
        merge_warnings=warnings,
    )


def _offset_polyline(poly: Polyline, offset_rc: tuple[float, float]) -> Polyline:
    return Polyline(
        points=poly.points + np.array(offset_rc, dtype=float),
        closed=poly.closed,
        source_edge_ids=poly.source_edge_ids,
    )


def build_plot_job(glyph_results: list[GlyphPipelineResult], config: PipelineConfig) -> PlotJob:
    """複数文字のグラフからページ単位で順序最適化したポリライン列(mm座標)を構築する。"""
    if not glyph_results:
        return PlotJob(polylines=[], canvas_size_mm=config.canvas_size_mm, stats=compute_stats([]))

    all_trails_px: list[Polyline] = []
    for gr in glyph_results:
        trails = extract_trails(gr.graph_merged)
        all_trails_px.extend(_offset_polyline(t, gr.raster.cell_origin_px) for t in trails)

    ordered_px = order_trails_nearest_neighbor(all_trails_px, start_pos=(0.0, 0.0))

    grid_rows = max(gr.raster.cell_origin_px[0] + gr.raster.canvas_px[1] for gr in glyph_results)
    grid_cols = max(gr.raster.cell_origin_px[1] + gr.raster.canvas_px[0] for gr in glyph_results)
    canvas_w_mm, canvas_h_mm = config.canvas_size_mm
    px_to_mm = canvas_w_mm / grid_cols if grid_cols > 0 else 1.0

    ordered_mm: list[Polyline] = []
    for poly in ordered_px:
        rows = poly.points[:, 0]
        cols = poly.points[:, 1]
        x_mm = cols * px_to_mm
        y_mm = canvas_h_mm - rows * px_to_mm  # numpy行(下向き) -> プロッターY軸(上向き)の反転
        pts_mm = np.stack([x_mm, y_mm], axis=1)
        ordered_mm.append(Polyline(points=pts_mm, closed=poly.closed, source_edge_ids=poly.source_edge_ids))

    stats = compute_stats(ordered_mm, start_pos=(0.0, 0.0))
    return PlotJob(polylines=ordered_mm, canvas_size_mm=config.canvas_size_mm, stats=stats)


def run_text_pipeline(text: str, config: PipelineConfig) -> tuple[list[GlyphPipelineResult], PlotJob]:
    """文字列（複数行対応、グリッド配置）を丸ごと処理する。"""
    rasters = rasterize_grid(
        config.font_path,
        text,
        config.font_size_pt,
        config.cell_px,
        columns=config.columns,
        font_index=config.font_index,
        threshold=config.threshold,
    )
    glyph_results = [process_glyph(r, config) for r in rasters]
    job = build_plot_job(glyph_results, config)
    return glyph_results, job
