import numpy as np

from xyproter.metrics import count_components
from xyproter.rasterize import rasterize_glyph
from xyproter.skeletonize_stage import skeletonize_glyph
from xyproter.spur_pruning import prune_spurs, remove_spurs


def _t_shape_skeleton():
    """縦棒(長さ51, 中間に分岐点) + 分岐点から斜めに伸びる短いヒゲ(長さ5)を持つ合成スケルトン。

    分岐点付近では対角隣接により数画素がneighbor_count>=3の「ブロブ」になる
    （交差点がブロブ化する現象そのもの）。主線の両側のセグメントを
    十分長くとることで、ヒゲだけが閾値以下として除去されるようにする。
    """
    skel = np.zeros((80, 80), dtype=bool)
    skel[5:56, 30] = True  # 縦の主線 (51px, row5..55)
    branch_row, branch_col = 30, 30  # 主線の中間点を分岐点にする
    for i in range(1, 6):
        skel[branch_row - i, branch_col + i] = True  # 分岐点から斜めに伸びる短いヒゲ (5px)
    return skel


def test_prune_spurs_removes_short_spur_keeps_main_line():
    skel = _t_shape_skeleton()
    main_line_len = int(skel[5:56, 30].sum())
    pruned = prune_spurs(skel, min_length=8)
    # 主線(縦棒)はそのまま残っているはず
    assert pruned[5:56, 30].sum() == main_line_len
    # ヒゲは除去されているはず
    assert pruned.sum() < skel.sum()


def test_prune_spurs_does_not_break_connectivity():
    skel = _t_shape_skeleton()
    n_before = count_components(skel)
    pruned = prune_spurs(skel, min_length=8)
    n_after = count_components(pruned)
    assert n_before == n_after


def test_remove_spurs_regression_real_font(jp_font_path):
    """依頼書記載の実測傾向（ヒゲ除去でpx数が減り、連結成分数は不変）を実フォントで確認する。"""
    for char in ["永", "語"]:
        raster = rasterize_glyph(jp_font_path, char, 420.0, (512, 512))
        raw_skel = skeletonize_glyph(raster.binary)
        result = remove_spurs(raster.binary, raw_skel)
        assert result.n_components_before == result.n_components_after
        assert not result.warnings
        assert result.pruned_skeleton.sum() <= result.raw_skeleton.sum()
        assert result.spur_threshold_px > 0
