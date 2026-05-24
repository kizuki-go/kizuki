# Phase 2: 基盤レイヤ抽出

## 目的

他のあらゆるモジュールから参照される基盤を最初に切り出す。これにより以降のフェーズで「どの import を書けばいいか」が明確になる。

## 抽出対象

5つの新ファイルを作成し、`main_window.py` から該当コードを移動する。

| 新ファイル | 内容 | 元の場所 | 行数目安 |
|---|---|---|---|
| `gui/theme.py` | Theme, T(), 定数 (SP_*, R_*, PAD_*, COLS) | L323-758 + L583-603 + L501 | ~440 |
| `gui/fonts.py` | F/Fmono/Font_XS..XXL/FontMono_* | L510-578 | ~70 |
| `gui/icons.py` | SVG生成・make_icon・qss生成関数 | L609-1085, L1288-1338 | ~250 |
| `gui/infra.py` | Profiler, SoundPlayer, BlunderInfo, TranslucentWidget | L62-322, L1340-1394, L6938-6969 | ~340 |
| `gui/widgets/common.py` | FlatSlider, _RankItemDelegate, _ScrollFadeOverlay, _AlwaysAcceptWheelTextEdit | L760-1085, L1086-1338, L6970-7139 | ~620 |

## 作業順序

依存関係に従い、以下の順で進める:

```
1. gui/theme.py       (依存なし)
2. gui/fonts.py       (theme.py を import)
3. gui/icons.py       (theme.py を import)
4. gui/infra.py       (依存なし)
5. gui/widgets/__init__.py (空)
6. gui/widgets/common.py  (theme.py, fonts.py, icons.py を import)
```

各ステップごとに「コピー → import 追加 → 元から削除 → 構文OK → import OK → 起動OK → commit」を必ず実行。**5つを一気にやらない**。

---

## 詳細手順: 各ファイルの作成

### Step 2-1: gui/theme.py

**含めるもの**:

1. L323-491: `class Theme:` クラス定義 (436行)
2. L492: `_theme = Theme("dark")` シングルトン
3. L494-509: `def T() -> Theme:` 関数
4. L501: `COLS = ...` 定数
5. L583-603: `SP_*`, `R_*`, `PAD_*`, `SPACING_ROW` 定数

**新ファイルのスケルトン**:

```python
"""
gui/theme.py — Theme シングルトンと UI 定数。

このモジュールは他の全 gui モジュールから参照される基盤。
依存: PyQt6 のみ。他の gui モジュールを import してはならない。
"""
from __future__ import annotations
from PyQt6.QtGui import QColor

# ── 列ラベル ─────────────────────────────────────
COLS = "ABCDEFGHJKLMNOPQRST"

# ── 間隔定数 ─────────────────────────────────────
SP_XS = 4
SP_SM = 8
SP_MD = 12
SP_LG = 16
SP_XL = 24

# ── 角丸定数 ─────────────────────────────────────
R_XS = ...
R_SM = ...
R_MD = ...
R_LG = ...
R_PILL = ...

# ── パディング定数 ───────────────────────────────
PAD_CARD = ...
PAD_TIGHT = ...
PAD_NAV = ...
PAD_ICON = ...

SPACING_ROW = ...


class Theme:
    # ... (L323-491 のクラス内容をそのまま)


_theme = Theme("dark")


def T() -> Theme:
    return _theme


# Theme のセッターも忘れず移植
def set_mode(mode: str) -> None:
    """テーマモードを切り替える"""
    _theme.set_mode(mode)
```

**注意**: `Theme.set_mode()` の代わりに `_theme.set_mode("dark")` を呼ぶコードが既存にあるはず。`gui/theme.py` 側で `set_mode` をモジュールレベル関数として公開すると後で楽になる。既存コードで `from gui.theme import _theme; _theme.set_mode(...)` のようにする場合は、`_theme` を `default_theme` という公開名にリネームしてもよい(命名改善は controlled に)。

**移動元の `main_window.py` 側の変更**:

```python
# 旧: ファイル上部の Theme クラス定義を削除
# 新: 以下を追加
from gui.theme import (
    Theme, T, COLS,
    SP_XS, SP_SM, SP_MD, SP_LG, SP_XL,
    R_XS, R_SM, R_MD, R_LG, R_PILL,
    PAD_CARD, PAD_TIGHT, PAD_NAV, PAD_ICON,
    SPACING_ROW,
)
import gui.theme as _theme  # _theme.set_mode("dark") などのための別名
```

**検証**:
```bash
python -m py_compile gui/theme.py
python -c "from gui.theme import T, Theme; print(T().BG.name())"
python -m py_compile gui/main_window.py
python -c "import gui.main_window; print('OK')"
```

アプリ起動 → テーマ切替動作確認 → コミット。

```bash
git add gui/theme.py gui/main_window.py
git commit -m "refactor: extract Theme and UI constants to gui/theme.py"
```

---

### Step 2-2: gui/fonts.py

**含めるもの**:

- L510-514: `_font_name` 関数
- L515-521: `F` 関数
- L522-532: `Fmono` 関数
- L534-578: `Font_XS`〜`FontMono_XXL` 関数群

**新ファイル**:

```python
"""
gui/fonts.py — フォント生成ヘルパ。

依存: PyQt6 のみ。
"""
from __future__ import annotations
from PyQt6.QtGui import QFont, QFontDatabase


def _font_name(preferred: str, fallback: str) -> str:
    ...

def F(size, bold=False):
    ...

def Fmono(size, bold=False):
    ...

def Font_XS(bold=False):    return F(12, bold)
def Font_SM(bold=False):    return F(14, bold)
def Font_MD(bold=False):    return F(16, bold)
def Font_LG(bold=False):    return F(18, bold)
def Font_XL(bold=True):     return F(24, bold)
def Font_XXL(bold=True):    return F(28, bold)

def FontMono_XS(bold=False):   return Fmono(12, bold)
def FontMono_SM(bold=False):   return Fmono(14, bold)
def FontMono_MD(bold=False):   return Fmono(16, bold)
def FontMono_LG(bold=True):    return Fmono(18, bold)
def FontMono_XL(bold=True):    return Fmono(24, bold)
def FontMono_XXL(bold=True):   return Fmono(28, bold)
```

**main_window.py 側**:
```python
from gui.fonts import (
    F, Fmono,
    Font_XS, Font_SM, Font_MD, Font_LG, Font_XL, Font_XXL,
    FontMono_XS, FontMono_SM, FontMono_MD, FontMono_LG, FontMono_XL, FontMono_XXL,
)
```

検証(構文・import・起動)→ コミット:

```bash
git commit -m "refactor: extract font helpers to gui/fonts.py"
```

---

### Step 2-3: gui/icons.py

**含めるもの**:

- L609-628: `_rounded_check_svg`, `_get_check_mark_path`
- L658-700: `_rank_check_svg_bold`, `_get_rank_check_mark_path`
- L702-758: `_chevron_down_svg`, `_get_chevron_down_path`
- L863-957: `menu_qss`, `rank_list_qss`, `style_qmenu`
- L1003-1024: `statusbar_qss`
- L1025-1085: `icon_button_qss`, `install_icon_hover_color_swap`
- L1288-1338: `make_icon`

**注意**: `_install_submenu_positioner` (L993) と `_SubMenuPositioner` クラス (L959) は **menus.py に移す** (フェーズ4)。ここでは含めない。

**新ファイル**:

```python
"""
gui/icons.py — SVG アイコン生成と QSS 生成ヘルパ。

依存: gui.theme (T(), 色), PyQt6
"""
from __future__ import annotations
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import QByteArray
from gui.theme import T  # 必要に応じて他の定数も

# ── SVG パス文字列 ────────────────────────────────
def _rounded_check_svg(color: str) -> str: ...
def _get_check_mark_path(color: str) -> str: ...
# ... 他SVG関数

# ── QSS 生成 ─────────────────────────────────────
def menu_qss() -> str: ...
def rank_list_qss() -> str: ...
def statusbar_qss() -> str: ...
def icon_button_qss(...) -> str: ...

# ── スタイル適用 ──────────────────────────────────
def style_qmenu(qmenu, leaf: bool = False) -> None: ...
def install_icon_hover_color_swap(btn, normal_icon, hover_icon): ...

# ── アイコン生成 ──────────────────────────────────
def make_icon(svg: str, size: int = 16, color: str = "#ffffff", opacity: float = 1.0) -> QIcon:
    ...
```

`main_window.py` 側に対応する import を追加し、各シンボル定義を削除。

検証 → コミット:
```bash
git commit -m "refactor: extract icon and QSS helpers to gui/icons.py"
```

---

### Step 2-4: gui/infra.py

**含めるもの**:

- L67-163: `class _Profiler:`
- L160 (重複): `_profiler = _Profiler()` (`_Profiler` の直後で良い)
- L164-181: `def _profile(tag: str):`
- L182-228: `def _profile_method(tag: str):`
- L230-253: `_get_thresholds`, `get_current_thresholds`, `set_player_rank`
- L254-322: `class BlunderInfo` + `eval_badge_tuple`
- L1340-1394: `class SoundPlayer:`
- L6938-6969: `class TranslucentWidget(QWidget):`

**注意**: `_get_thresholds` 系は棋力評価の閾値を扱う。位置的にはここに置くが、より良い場所(例: `core/analyzer.py` の近く)があれば後で再整理する余地はある。今回は触らない。

```python
"""
gui/infra.py — プロファイラ、サウンド、Blunder評価、低レベルWidget基盤。
"""
from __future__ import annotations
import os as _os
import threading as _threading
import time
from contextlib import contextmanager as _contextmanager
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QWidget
# ... 他

class _Profiler:
    ...

_profiler = _Profiler()

@_contextmanager
def _profile(tag: str):
    ...

def _profile_method(tag: str):
    ...

# 棋力閾値
def _get_thresholds(rank: int) -> tuple: ...
def get_current_thresholds() -> tuple: ...
def set_player_rank(rank: int): ...

# Blunder情報
class BlunderInfo(_BlunderInfoBase):
    ...

def eval_badge_tuple(category) -> tuple: ...

# サウンド
class SoundPlayer:
    ...

# Widget基盤
class TranslucentWidget(QWidget):
    ...
```

**注意点**: `BlunderInfo` は `core/analyzer.py` の `_BlunderInfoBase` を継承している。`main_window.py` 冒頭の以下を `gui/infra.py` に移す:

```python
from core.analyzer import MoveAnalysis, BlunderInfo as _BlunderInfoBase
```

`MoveAnalysis` は別の場所(`gui.widgets.panels` か `gui._mixins.engine_ctrl`)で使うのでここでは import しない。

検証 → コミット。

```bash
git commit -m "refactor: extract Profiler/SoundPlayer/BlunderInfo to gui/infra.py"
```

---

### Step 2-5: gui/widgets/__init__.py

空ファイルを作成。

```python
"""gui.widgets — UIコンポーネント群"""
```

```bash
mkdir -p gui/widgets
touch gui/widgets/__init__.py
git add gui/widgets/__init__.py
```

---

### Step 2-6: gui/widgets/common.py

**含めるもの**:

- L760-957: `class _RankItemDelegate(QStyledItemDelegate):`
- L1082-1085: `SLIDER_HEIGHT`, `SLIDER_HANDLE` 定数
- L1086-1287: `class FlatSlider(QSlider):`
- L6970-7049: `class _ScrollFadeOverlay(QWidget):`
- L7050-7139: `class _AlwaysAcceptWheelTextEdit(QTextEdit):`

```python
"""
gui/widgets/common.py — 汎用ウィジェット。
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QSlider, QStyledItemDelegate, QStyle, QWidget, QTextEdit, QFrame
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QRect, QSize, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
from gui.theme import T, SP_XS, SP_SM, SP_MD, SP_LG, R_MD, R_LG
from gui.fonts import Font_XS, Font_SM, Font_MD
from gui.icons import make_icon

SLIDER_HEIGHT = ...
SLIDER_HANDLE = ...


class _RankItemDelegate(QStyledItemDelegate):
    ...

class FlatSlider(QSlider):
    ...

class _ScrollFadeOverlay(QWidget):
    ...

class _AlwaysAcceptWheelTextEdit(QTextEdit):
    ...
```

検証 → コミット。

```bash
git commit -m "refactor: extract common widgets (FlatSlider, RankItemDelegate, etc.)"
```

---

## フェーズ全体の検証チェックリスト

- [ ] 6つのファイルが全て作成され構文OK
- [ ] `python -c "from gui.theme import T; T().BG"` が動作
- [ ] `python -c "from gui.fonts import Font_MD"` が動作
- [ ] `python -c "from gui.icons import make_icon"` が動作
- [ ] `python -c "from gui.infra import SoundPlayer, _profile"` が動作
- [ ] `python -c "from gui.widgets.common import FlatSlider"` が動作
- [ ] `python -c "import gui.main_window"` が動作
- [ ] アプリ起動 → 通常操作シナリオ完了
- [ ] テーマ切替が問題なく動作(theme.py が正しく機能している)
- [ ] スライダ動作が問題なし(FlatSlider が正しく機能している)
- [ ] 棋力選択メニューが正常(_RankItemDelegate が正しく機能している)
- [ ] サウンド再生が問題なし(SoundPlayer が正しく機能している)

## トラブル時の対応

- 循環 import エラー: `gui.infra` 内で `gui.widgets.*` を import していないか確認。基盤レイヤは widgets を import しない。
- `T()` の解決が遅延しているなど挙動が変: モジュールレベル `_theme = Theme("dark")` の初期化タイミングが原因かも。`main_window.MainWindow.__init__` 内の `_theme.set_mode(...)` 呼び出しが残っているか確認。

## 次フェーズへ

完了したら `03_phase3.md` へ。
