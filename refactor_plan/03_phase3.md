# Phase 3: UIコンポーネント抽出

## 目的

`main_window.py` に残っている大型 UI クラスを `gui/widgets/` 配下の専用モジュールに切り出す。これでファイル本体は ~7,000 行になり、見通しが大きく改善する。

## 抽出対象

| 新ファイル | 含めるクラス | 元の行範囲 | 行数 |
|---|---|---|---|
| `gui/widgets/board.py` | BoardWidget, BoardContainer | L1395-3120, L8333-8350 | ~1,740 |
| `gui/widgets/panels.py` | ScoreBoard, InfoPanel, MetricLabel, BadgeWidget, _StoneIcon, _CrossFadeLabel, MoveInfoCard, `_make_card` | L3121-3621, L3622-3674, L3675-5003 | ~1,880 |
| `gui/widgets/branchtree.py` | _TreeEdgeFadeOverlay, BranchTreeWidget | L5004-6110 | ~1,110 |
| `gui/widgets/graph.py` | ScoreLeadAxis, IntegerBottomAxis, _GraphLabelOverlay, WinRateGraph | L7638-8332 | ~710 |
| `gui/widgets/titlebar.py` | _CustomTitleBar | L8351-9025 | ~670 |
| `gui/widgets/navbar.py` | _HelpPopover, ToggleSwitch, ToggleBar, NavBar | L6111-6351, L7412-7637 | ~700 |
| `gui/widgets/welcome.py` | WelcomePane, _WelcomeCard, _NewGameCard | L6352-6937 | ~580 |

## 作業順序

**依存の浅いものから**進める。同一フェーズ内でも commit を細かく分ける(各ファイル抽出ごとに1コミット)。

```
1. gui/widgets/board.py     (依存: theme, fonts, infra, icons)
2. gui/widgets/graph.py     (依存: theme, fonts. pyqtgraph)
3. gui/widgets/titlebar.py  (依存: theme, fonts, icons)
4. gui/widgets/navbar.py    (依存: theme, fonts, icons, widgets/common)
5. gui/widgets/welcome.py   (依存: theme, fonts, icons)
6. gui/widgets/panels.py    (依存: 上記全部 — 最後)
7. gui/widgets/branchtree.py (依存: theme, fonts, icons. core も少し)
```

`panels.py` を最後にする理由: `MoveInfoCard` が `BadgeWidget` を使い、`InfoPanel` が `ScoreBoard` と `WinRateGraph` を使うため、内部依存が一番多い。

---

## 共通ガイドライン

### 各ファイルの冒頭テンプレート

```python
"""
gui/widgets/XXX.py — ZZZ 関連のウィジェット。
"""
from __future__ import annotations
from typing import Optional  # 使うものだけ

# PyQt6 imports (使うものだけ)
from PyQt6.QtWidgets import ...
from PyQt6.QtCore import ...
from PyQt6.QtGui import ...

# gui パッケージ内 imports
from gui.theme import T, SP_XS, SP_SM, SP_MD, SP_LG, R_MD, R_LG  # 使うものだけ
from gui.fonts import Font_XS, Font_SM, Font_MD  # 使うものだけ
from gui.icons import make_icon  # 使うものだけ
# 他に必要なもの

# core パッケージ imports (必要なら)
# from core.xxx import ...
```

**import は実際に使うものだけ書く**。「念のため全部」は禁止。

### 各ファイル抽出の標準フロー

1. 新ファイルを作成し、上記テンプレートに従って必要なクラス定義を **コピー** で持ってくる
2. 新ファイルに必要な import を埋める
3. `python -m py_compile gui/widgets/XXX.py` で新ファイルを単体で構文チェック
4. `python -c "from gui.widgets.XXX import YYY"` で import チェック
5. `main_window.py` から該当クラスを削除
6. `main_window.py` の冒頭に `from gui.widgets.XXX import YYY` を追加
7. `python -m py_compile gui/main_window.py` チェック
8. `python -c "import gui.main_window"` チェック
9. アプリ起動 → 該当ウィジェットを使う操作を実行 → 問題なければコミット

---

## Step 3-1: gui/widgets/board.py

### 含めるクラス
- `class BoardWidget(QWidget):` (L1395-3120)
- `class BoardContainer(QWidget):` (L8333-8350)

### 必要な import (調査結果)

BoardWidget 内で使われるシンボル:
- `T()` (テーマ) → `from gui.theme import T`
- `SP_XS, SP_SM` 等 (使われていれば)
- `Font_*` 系
- `_profile` (プロファイラ) → `from gui.infra import _profile`
- `Theme` クラス自体は使わない(T() 経由)

### 注意点
- `BoardWidget` の class メソッド `_wood_cache_path` で `Path` を使う → `from pathlib import Path` 必要
- `QImage` `QPixmap` などの描画系 import が大量に必要
- `_paint_*` 系メソッドは内部のみで使う → そのまま移動

### 検証

起動して以下を確認:
- 盤面が正常に描画される
- 石を打てる
- 19路・13路・9路で正しい星点(過去の改修済み: 5個実装)
- 盤面反転トグル(ON/OFF 両方の回転方向)
- ownership 表示の ON/OFF
- 手番マーク(last move)が正しく表示
- ハイライト・候補手表示

問題なければ:
```bash
git commit -m "refactor: extract BoardWidget/BoardContainer to gui/widgets/board.py"
```

---

## Step 3-2: gui/widgets/graph.py

### 含めるクラス
- `class ScoreLeadAxis(pg.AxisItem if pg else object):` (L7638-7656)
- `class IntegerBottomAxis(pg.AxisItem if pg else object):` (L7657-7677)
- `class _GraphLabelOverlay(QWidget):` (L7678-7712)
- `class WinRateGraph(QWidget):` (L7713-8332)

### 必要な import
```python
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

try:
    import pyqtgraph as pg
except ImportError:
    pg = None

from gui.theme import T
from gui.fonts import Font_XS, FontMono_XS
```

### 注意点
- `pyqtgraph` が optional dependency なので、`pg is None` の場合のフォールバックがある。これをそのまま維持
- `ScoreLeadAxis` と `IntegerBottomAxis` は `tickValues` / `tickStrings` を持つ(同名重複は OK、各クラスのオーバーライド)

### 検証
- グラフ表示(勝率・目差両方)
- ノード位置のマーカーがクリックで動く
- ドラッグでスクラブできる
- テーマ切替でグラフ色が変わる

```bash
git commit -m "refactor: extract WinRateGraph and axes to gui/widgets/graph.py"
```

---

## Step 3-3: gui/widgets/titlebar.py

### 含めるクラス
- `class _CustomTitleBar(QWidget):` (L8351-9025)

### 必要な import
```python
from PyQt6.QtWidgets import QWidget, QPushButton, QHBoxLayout, QLabel, QMenu, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QSize, QPoint, QRect, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QIcon, QPixmap

from gui.theme import T, SP_XS, SP_SM, SP_MD
from gui.fonts import Font_SM
from gui.icons import make_icon, icon_button_qss
```

### 注意点
- `_CustomTitleBar.HEIGHT` と `NB_HEIGHT` のような定数参照が他のクラスから行われる(`MainWindow.__init__` の `H_OVERHEAD` 計算等)
- これらは class 属性なので、`_CustomTitleBar.HEIGHT` で引き続きアクセスできる

### 検証
- タイトルバーが正しく表示
- 最大化・最小化・閉じるボタンが動作
- ドラッグでウィンドウ移動できる
- ダブルクリックで最大化トグル
- メニューボタンが動作

```bash
git commit -m "refactor: extract _CustomTitleBar to gui/widgets/titlebar.py"
```

---

## Step 3-4: gui/widgets/navbar.py

### 含めるクラス
- `class _HelpPopover(QFrame):` (L6111-6161)
- `class ToggleSwitch(QWidget):` (L6162-6235)
- `class ToggleBar(QWidget):` (L6236-6351)
- `class NavBar(QWidget):` (L7412-7637)

### 必要な import
標準的なもの + `gui.widgets.common` から `_AlwaysAcceptWheelTextEdit` を使う場合あり(確認)

### 注意点
- `ToggleBar.apply_theme()` メソッドがある → 重複検出済み
- `NavBar.NB_HEIGHT` `NB_MARGIN` 定数も他から参照される(class 属性として残す)

### 検証
- ナビバーが正しく表示
- 戻る・進む・先頭・末尾ボタンが動作
- スライダ操作
- AI ON/OFF、ownership ON/OFF、手数表示 ON/OFF
- コメントボタンが状態に応じてスタイル変化(`_apply_comment_btn_style`)

```bash
git commit -m "refactor: extract NavBar and toggle widgets to gui/widgets/navbar.py"
```

---

## Step 3-5: gui/widgets/welcome.py

### 含めるクラス
- `class WelcomePane(QWidget):` (L6352-6531)
- `class _WelcomeCard(QWidget):` (L6532-6836)
- `class _NewGameCard(_WelcomeCard):` (L6837-6937)

### 必要な import
標準セット

### 注意点
- `_WelcomeCard._force_dark_palette` フラグはライトモード時の D&D オーバーレイ文字色固定のため(過去の改修)
- `WA_TranslucentBackground=True` でカード矩形を透過(過去の改修)

### 検証
- 起動時にウェルカム画面が表示される(まず QSettings の `player_rank` を消して初回起動状態にする)
- D&D オーバーレイが正しく表示
- ライトモード時のオーバーレイ文字色が正しい(白文字)
- 新規対局カードの動作

```bash
git commit -m "refactor: extract WelcomePane and welcome cards to gui/widgets/welcome.py"
```

---

## Step 3-6: gui/widgets/panels.py

### 含めるクラス・関数
- `class ScoreBoard(QWidget):` (L3121-3621)
- `def _make_card(title="", badge_widget=None) -> tuple:` (L3622-3674)
- `class InfoPanel(QWidget):` (L3675-3745)
- `class MetricLabel(QWidget):` (L3746-3915)
- `class BadgeWidget(QWidget):` (L3916-4179)
- `class _StoneIcon(QWidget):` (L4180-4277)
- `class _CrossFadeLabel(QLabel):` (L4278-4366)
- `class MoveInfoCard(QWidget):` (L4367-5003)

### 必要な import
たくさんある。実際にコピー後 grep で確認しながら埋める。
```python
from gui.theme import (
    T, SP_XS, SP_SM, SP_MD, SP_LG, R_MD, R_LG,
    PAD_CARD, PAD_TIGHT,
)
from gui.fonts import (
    F, Fmono, Font_XS, Font_SM, Font_MD, Font_LG,
    FontMono_XS, FontMono_SM, FontMono_MD,
)
from gui.icons import make_icon
from gui.infra import eval_badge_tuple, get_current_thresholds
from gui.widgets.graph import WinRateGraph  # InfoPanel が WinRateGraph を使う
from core.analyzer import MoveAnalysis
```

### 注意点
- `InfoPanel` は `ScoreBoard` と `WinRateGraph` を内包する
- フェーズ1 で `update_summary`, `set_katago_status`, `get_comment` を削除済み
- `MoveInfoCard` 内の `_comment_btn` 死分岐も削除済み

### 検証
- 右パネルが正しく表示
- ScoreBoard の対局情報・捕獲石数・勝率バー
- WinRateGraph がパネル内に表示
- MoveInfoCard の表示・テーマ切替
- BadgeWidget の評価バッジ表示

```bash
git commit -m "refactor: extract panels (ScoreBoard, InfoPanel, MoveInfoCard, etc.) to gui/widgets/panels.py"
```

---

## Step 3-7: gui/widgets/branchtree.py

### 含めるクラス
- `class _TreeEdgeFadeOverlay(QWidget):` (L5004-5058)
- `class BranchTreeWidget(QWidget):` (L5059-6110)

### 必要な import
```python
from gui.theme import T, SP_XS, SP_SM, SP_MD, R_MD, R_LG
from gui.fonts import Font_XS, Font_SM, FontMono_XS
from gui.infra import _profile
from core.sgf_parser import SGFNode  # 必要に応じて
```

### 注意点
- 分岐ツリーは負荷が高いので、`_profile` を使ったプロファイリング箇所がある。これを正しく移植
- マウスホイール・ドラッグスクロールの実装が複雑。慎重に

### 検証
- 分岐ツリーが正しく表示
- ノードクリックで移動
- 分岐ノード削除
- 手数アンカークリック
- スクロール動作(ホイール・ドラッグ・スクロールバー)
- フェードオーバーレイ(`_TreeEdgeFadeOverlay`)が両端で適切に出る

```bash
git commit -m "refactor: extract BranchTreeWidget to gui/widgets/branchtree.py"
```

---

## フェーズ全体の検証チェックリスト

- [ ] 7つのウィジェットファイルが全て作成されている
- [ ] `python -c "import gui.main_window"` がエラーなく通る
- [ ] アプリ起動 → 全シナリオ完走
  - [ ] 盤面操作(石を打つ・反転)
  - [ ] グラフ操作(クリック・ドラッグ)
  - [ ] タイトルバー(最大化・最小化・ドラッグ移動)
  - [ ] ナビバー(進む・戻る・スライダ・各種トグル)
  - [ ] ウェルカム画面(初回起動状態でテスト)
  - [ ] 右パネル(スコア・グラフ・MoveInfoCard)
  - [ ] 分岐ツリー(クリック・スクロール・削除)
- [ ] テーマ切替で全ウィジェットが正しく追従
- [ ] `main_window.py` の行数が 16,000+ から ~7,000 に減っていることを `wc -l` で確認

## 次フェーズへ

完了したら `04_phase4.md` へ。
