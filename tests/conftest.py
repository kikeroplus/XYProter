from pathlib import Path

import numpy as np
import pytest
from skimage.draw import disk
from skimage.morphology import skeletonize

_CANDIDATE_FONTS = [
    r"C:\Windows\Fonts\msmincho.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothR.ttc",
]


@pytest.fixture(scope="session")
def jp_font_path() -> str:
    for path in _CANDIDATE_FONTS:
        if Path(path).exists():
            return path
    pytest.skip("日本語フォントが見つかりません")


def ring_skeleton(shape: tuple[int, int] = (40, 40), outer_r: float = 15, inner_r: float = 9) -> np.ndarray:
    """分岐/端点を一切持たない、純粋な閉ループのスケルトン（円環）を生成する。

    正方形リングを手作業で組むと8連結の対角隣接によりコーナーが偽の分岐点になるため、
    円環(annulus)を細線化して生成する。
    """
    center = (shape[0] // 2, shape[1] // 2)
    outer = np.zeros(shape, dtype=bool)
    rr, cc = disk(center, outer_r, shape=shape)
    outer[rr, cc] = True
    inner = np.zeros(shape, dtype=bool)
    rr2, cc2 = disk(center, inner_r, shape=shape)
    inner[rr2, cc2] = True
    return skeletonize(outer & ~inner)


@pytest.fixture()
def pure_loop_skeleton() -> np.ndarray:
    return ring_skeleton()
