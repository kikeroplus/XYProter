# XYプロッター用 日本語文字センターライン抽出パイプライン 実装依頼

## 背景・目的

XYプロッターで日本語（ひらがな・カタカナ・漢字）を書くためのツールを作りたい。
通常のTrueType/OpenTypeフォントはアウトライン（塗りつぶし用の輪郭）データなので、そのままプロッターでなぞると線が二重線になってしまう。そこで、フォントのグリフをラスタライズ→細線化（skeletonize）してセンターライン（一筆書き用のパス）を抽出する方式を採用する。

事前に手作業でプロトタイプ検証済みで、以下が確認できている：

- `skimage.morphology.skeletonize` によるセンターライン抽出は、シンプルな字（ひらがな、画数の少ない漢字）では実用レベル
- ゴシック体（Noto Sans CJK JP等、装飾の少ない書体）の方が明朝体よりノイズが少なく、細線化に向く
- 明朝体は「トメ」「ハネ」「ウロコ」由来の短いヒゲ状の余計な枝が出やすい → **グラフベースのスパー（ヒゲ）除去処理で解消できることを確認済み**（下記に検証済みコードを添付。これをベースに拡張してほしい）
- 太いペンでごまかす案も試したが、ペンを太くしすぎると画数の多い字は線同士が癒着して潰れる。ペン幅だけに頼らず、ちゃんとヒゲ除去する方針が良い
- 未解決の課題：**線が交差する箇所でスケルトンがブロブ状（塊）になる**問題。ここを1点に集約する処理がまだない
- パスをプロッターの実際の移動順（ポリライン列、ペンアップダウンの回数）に変換する処理も未着手

このプロトタイプ検証を土台に、本格的なパイプラインとして実装してほしい。

## 検証済み参考コード（このロジックをベースに拡張すること）

```python
import numpy as np
from scipy.ndimage import convolve
from skimage.morphology import skeletonize

KERNEL = np.array([[1,1,1],[1,0,1],[1,1,1]])

def neighbor_count(skel):
    return convolve(skel.astype(int), KERNEL, mode="constant", cval=0)

def get_neighbors(skel, pt):
    y, x = pt
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ny, nx = y + dy, x + dx
            if 0 <= ny < skel.shape[0] and 0 <= nx < skel.shape[1] and skel[ny, nx]:
                out.append((ny, nx))
    return out

def prune_spurs(skel, min_length):
    """スケルトンの端点から分岐点までの経路長が min_length 以下なら
    トメ・ハネ由来のノイズとみなして除去する。
    (512px canvasに420ptフォントを描画した場合、閾値28px程度が良好だった)
    """
    skel = skel.copy()
    nb = neighbor_count(skel)
    endpoints = list(zip(*np.where(skel & (nb == 1))))
    to_remove = set()
    for ep in endpoints:
        if ep in to_remove:
            continue
        path = [ep]
        prev, cur = None, ep
        branch_reached = False
        while True:
            nbrs = [n for n in get_neighbors(skel, cur) if n != prev]
            if len(nbrs) == 0:
                break
            if len(nbrs) > 1:
                branch_reached = True
                break
            nxt = nbrs[0]
            if nb[nxt] >= 3:
                branch_reached = True
                break
            path.append(nxt)
            prev, cur = cur, nxt
        if len(path) <= min_length:
            to_remove.update(path)
    for (y, x) in to_remove:
        skel[y, x] = False
    return skel
```

検証時の実測値（Noto Serif CJK、512x512px canvas、フォントサイズ420pt）：
「永」raw 1392px → pruned 1245px（連結成分数3のまま変化なし＝断線なし）
「語」raw 2554px → pruned 2167px（連結成分数7のまま変化なし＝断線なし）

## 実装してほしいパイプライン

以下の順に処理する、モジュール化されたPythonプログラムとして実装してほしい。各ステージは単体でテスト・可視化できるようにすること（このプロトタイプ検証と同じように、各段階の画像を出力して目視確認できると助かる）。

### 1. グリフラスタライズ
- 入力: フォントファイルパス（.ttf/.ttc/.otf、コレクション内インデックス指定可）、文字列、フォントサイズ、キャンバス解像度
- PIL(Pillow)でグリフを高解像度にラスタライズ、二値化

### 2. 細線化
- `skimage.morphology.skeletonize` でセンターライン抽出

### 3. スパー（ヒゲ）除去
- 上記の検証済みロジックを拡張。閾値はキャンバスサイズ/フォントサイズに対して相対的に自動計算されるようにする（文字ごとに閾値を手動調整しなくて済むように）
- 除去前後で連結成分数（`skimage.measure.label`）が変化していないか自動チェックし、変化していたら警告を出す（誤って本画を切断していないかの安全弁）

### 4. 交差点の集約（未解決課題・新規実装）
- スケルトン上で近接する分岐点（neighbor_count >= 3のピクセル）のクラスタを検出し、1つのノード（重心座標）に統合する
- 交差点まわりのブロブ化を解消し、綺麗な交差として扱えるようにする

### 5. パス（ポリライン）抽出
- スケルトンをグラフ構造として捉え（ノード＝端点・分岐点、エッジ＝ノード間の画素列）、プロッターが実際になぞる順序のポリライン列に変換する
- ペンアップ移動（線が繋がっていない箇所へのジャンプ）をなるべく減らす順序最適化を行う（厳密な巡回セールスマン問題は不要、最近傍法などの簡易ヒューリスティックでよい）
- 出力として以下の統計を表示：総パス数、総描画距離、総ペンアップ移動距離、ペンアップダウン回数

### 6. 出力
- SVG出力（プレビュー用、ポリラインとして）
- G-code出力（実プロッター用）。ペンアップ/ダウンの制御コマンドは設定可能にしてほしい（サーボ角度指定コマンドなのかGPIO制御なのか、まだ確定していないのでプレースホルダー的に差し替えやすい形にしておく）
- プレビュー画像出力（指定したペン幅で実際に描画した場合の見た目をシミュレート。太さは `skimage.morphology.dilation` 等で近似でよい）

### 7. CLI
- コマンドライン引数で以下を指定できるようにする：フォントパス、フォントコレクションインデックス、文字列（複数文字対応、グリッド配置）、キャンバスサイズ(mm)、DPI/スケール、スパー除去閾値（自動 or 手動上書き）、交差点集約半径、プレビュー用ペン幅、出力先ディレクトリ

## 動作環境・その他の注意

- 開発・実行はWindows上（Claude Code / VSCode）。フォントはWindows標準の日本語フォント（游ゴシック、MSゴシック等）や別途配置したNoto Sans CJK JPなどを使う想定
- 依存ライブラリはPillow, numpy, scipy, scikit-image, matplotlib（プレビュー用）を想定。他に良い方法があれば提案してほしい
- まずは複数文字を1枚のプレビュー画像に並べて出力し、目視で「これなら実用になりそう」と判断できる状態を目指す。G-code生成は次の段階でよいので、優先度としては 1〜5 のパイプライン品質（特に4の交差点処理と5のパス抽出）を重視してほしい
- コードは各ステージが独立関数/モジュールとして呼び出せるようにし、後から交差点集約アルゴリズムやパス最適化アルゴリズムだけ差し替えられるようにしてほしい
