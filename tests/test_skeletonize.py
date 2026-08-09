import numpy as np

from xyproter.skeletonize_stage import clean_binary_noise, skeletonize_glyph


def test_skeletonize_thick_line_becomes_thin_centerline():
    binary = np.zeros((50, 100), dtype=bool)
    binary[20:30, 10:90] = True  # 太さ10pxの横棒
    skel = skeletonize_glyph(binary, clean_noise=False)
    # 各列で1画素幅になっているはず
    col_counts = skel[:, 10:90].sum(axis=0)
    assert col_counts.max() <= 2  # 端の丸め等で多少の揺らぎは許容
    assert skel.any()
    # センターラインはおおよそ行25(太さの中心)付近にあるはず
    rows_with_ink = np.where(skel[:, 50])[0]
    assert rows_with_ink.size > 0
    assert abs(int(rows_with_ink.mean()) - 25) <= 2


def test_clean_binary_noise_removes_small_specks():
    binary = np.zeros((50, 50), dtype=bool)
    binary[10:20, 10:20] = True  # 本体
    binary[0, 0] = True  # 孤立1px（アンチエイリアスノイズ想定）
    cleaned = clean_binary_noise(binary, min_size=4)
    assert not cleaned[0, 0]
    assert cleaned[10:20, 10:20].all()


def test_skeletonize_preserves_isolated_dot_when_large_enough():
    binary = np.zeros((60, 60), dtype=bool)
    binary[10:20, 10:20] = True  # 主画
    binary[40:46, 40:46] = True  # 濁点相当の小片（36px、min_size=4より十分大きい）
    skel = skeletonize_glyph(binary, clean_noise=True, min_object_size=4)
    assert skel[40:46, 40:46].any()
