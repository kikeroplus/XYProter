"""ステージ2: 細線化（skeletonize）と微小ノイズ除去。"""
from __future__ import annotations

import numpy as np
from skimage.morphology import remove_small_objects, skeletonize


def clean_binary_noise(binary: np.ndarray, min_size: int = 4) -> np.ndarray:
    """アンチエイリアス由来の微小な孤立画素を除去する。

    濁点・半濁点のような正当な小片（ラスタライズ後でも数十px程度はある）を
    誤って消さないよう、閾値は保守的（デフォルト4px）に設定する。
    """
    return remove_small_objects(binary, max_size=max(min_size - 1, 0), connectivity=2)


def skeletonize_glyph(binary: np.ndarray, clean_noise: bool = True, min_object_size: int = 4) -> np.ndarray:
    """二値グリフ画像からセンターライン（スケルトン）を抽出する。"""
    work = clean_binary_noise(binary, min_object_size) if clean_noise else binary
    return skeletonize(work)
