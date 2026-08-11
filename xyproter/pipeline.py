"""全ステージのオーケストレーション。

GlyphRaster -> SkeletonResult -> SkeletonGraph(統合前) -> SkeletonGraph(統合後)
    -> List[Polyline](px) -> PlotJob(mm)

px -> mm 変換は build_plot_job の1箇所に閉じ込める。座標系は「文章の先頭文字の
左上」を原点(0,0)とし、X+が右、Y-(マイナス)方向が下——つまり人が紙に文字を
書く向きに合わせている(numpy座標のrow増加=下方向をそのままY減少に対応させる)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .graph_build import build_skeleton_graph
from .intersection_merge import check_merge_safety, merge_close_junctions
from .metrics import estimate_merge_radius
from .path_extraction import compute_stats, extract_trails, order_trails_nearest_neighbor
from .path_simplify import simplify_polylines
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
    simplify_tolerance_mm: float | None = None  # モード1(既定)=None=間引きなし。値を指定すると高速モード
    letter_spacing_factor: float = 1.0  # セル幅に対する列方向配置ピッチの倍率。既定1.0、<1で文字間を詰める


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
    """複数文字のグラフからページ単位で順序最適化したポリライン列(mm座標)を構築する。

    文字と文字の間の順序は glyph_results の並び順(= rasterize_grid が生成した
    元テキストの行順・文字順)をそのまま保つ。1文字の中の複数ストローク間の
    順序のみ、ペンアップ移動を最小化する最近傍法で最適化する。
    """
    if not glyph_results:
        return PlotJob(polylines=[], canvas_size_mm=config.canvas_size_mm, stats=compute_stats([]))

    ordered_px: list[Polyline] = []
    cur_pos: tuple[float, float] = (0.0, 0.0)
    for gr in glyph_results:
        trails = extract_trails(gr.graph_merged)
        trails = [_offset_polyline(t, gr.raster.cell_origin_px) for t in trails]
        char_ordered = order_trails_nearest_neighbor(trails, start_pos=cur_pos)
        ordered_px.extend(char_ordered)
        if char_ordered:
            cur_pos = (float(char_ordered[-1].points[-1][0]), float(char_ordered[-1].points[-1][1]))

    grid_rows = max(gr.raster.cell_origin_px[0] + gr.raster.canvas_px[1] for gr in glyph_results)
    grid_cols = max(gr.raster.cell_origin_px[1] + gr.raster.canvas_px[0] for gr in glyph_results)
    canvas_w_mm, _ = config.canvas_size_mm
    px_to_mm = canvas_w_mm / grid_cols if grid_cols > 0 else 1.0
    # 高さはconfig指定値を使わず実際に使用した行数から逆算する。呼び出し側が
    # 文章全体の行数を事前に計算してcanvas高さを合わせる必要をなくすため。
    canvas_h_mm = grid_rows * px_to_mm

    ordered_mm: list[Polyline] = []
    for poly in ordered_px:
        rows = poly.points[:, 0]
        cols = poly.points[:, 1]
        x_mm = cols * px_to_mm
        y_mm = -rows * px_to_mm  # 原点=先頭文字の左上。下方向がマイナスY(人が書く向き)
        pts_mm = np.stack([x_mm, y_mm], axis=1)
        ordered_mm.append(Polyline(points=pts_mm, closed=poly.closed, source_edge_ids=poly.source_edge_ids))

    if config.simplify_tolerance_mm is not None:
        ordered_mm = simplify_polylines(ordered_mm, config.simplify_tolerance_mm)

    stats = compute_stats(ordered_mm, start_pos=(0.0, 0.0))
    return PlotJob(polylines=ordered_mm, canvas_size_mm=(canvas_w_mm, canvas_h_mm), stats=stats)


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
        letter_spacing_factor=config.letter_spacing_factor,
    )
    glyph_results = [process_glyph(r, config) for r in rasters]
    job = build_plot_job(glyph_results, config)
    return glyph_results, job
