# Phase 5: Mixin 型補助モジュール作成

## 目的

フェーズ6 で `MainWindow` を 6つの Mixin に分割するための **型補助** を先に整備する。これを先にやることで、Mixin 内のメソッドで `self._engine` などの属性に型補完が効くようになる。

## 作業内容

`gui/_mixins/__init__.py` と `gui/_mixins/_types.py` の2ファイルを作る。**この時点では Mixin 本体は作らない**(フェーズ6でやる)。

---

## Step 5-1: gui/_mixins/__init__.py

空ファイル:

```python
"""gui._mixins — MainWindow を構成する Mixin 群と型補助"""
```

```bash
mkdir -p gui/_mixins
touch gui/_mixins/__init__.py
git add gui/_mixins/__init__.py
```

---

## Step 5-2: 属性の完全洗い出し

`MainWindow` クラスで使われる属性を **完全に** リストアップする。

```bash
cd gui
python3 << 'EOF'
import re
from collections import defaultdict

with open("main_window.py") as f:
    lines = f.readlines()

current_class = None
attrs = defaultdict(set)  # method -> set of attrs

for i, line in enumerate(lines, 1):
    m = re.match(r"^class (\w+)", line)
    if m:
        current_class = m.group(1)
        continue
    if current_class != "MainWindow":
        continue
    # self.xxx 参照を全部拾う
    for m in re.finditer(r"self\.(_?\w+)", line):
        attrs[i].add(m.group(1))

# 全属性をユニーク化
all_attrs = set()
for s in attrs.values():
    all_attrs.update(s)

# メソッド呼出らしきもの(後に () が続く)はメソッドの可能性が高いが、
# 属性経由のオブジェクトメソッド呼出(例: self._engine.start_pondering)は属性扱い
# ここでは「self.xxx の xxx」全部をまず出す
print(f"全シンボル: {len(all_attrs)}")
for a in sorted(all_attrs):
    print(f"  {a}")
EOF
```

このリストから「メソッド(別 Mixin で定義する)」と「属性(MainWindow.__init__ で初期化する)」を分離する。

メソッドの判別は簡単: フェーズ3-4 で抽出したクラス名と、`grep "def 名前(" gui/main_window.py` でヒットするかで判定。

---

## Step 5-3: gui/_mixins/_types.py

完成形のテンプレート:

```python
"""
gui/_mixins/_types.py — Mixin で利用する型ヒント補助。

このモジュールは TYPE_CHECKING 時のみ評価される Protocol を提供する。
実行時には何もしない。

各 Mixin は self の型を MainWindowProto として宣言することで、
self._engine などの属性に対して IDE 補完と mypy/pyright の型チェックが効く。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol, Optional, Any

if TYPE_CHECKING:
    # ── PyQt6 ────────────────────────────────────────
    from PyQt6.QtCore import QSettings, QPropertyAnimation, QTimer
    from PyQt6.QtWidgets import (
        QWidget, QTextEdit, QMenu, QSlider, QFrame,
        QGraphicsOpacityEffect,
    )
    from PyQt6.QtGui import QAction, QPixmap

    # ── core ─────────────────────────────────────────
    from core.game_state import GameState
    from core.katago_engine import KataGoEngine
    from core.sgf_parser import SGFGame, SGFNode
    from core.analyzer import MoveAnalysis

    # ── gui パッケージ ───────────────────────────────
    from gui.widgets.board import BoardWidget, BoardContainer
    from gui.widgets.panels import InfoPanel, MoveInfoCard
    from gui.widgets.branchtree import BranchTreeWidget
    from gui.widgets.graph import WinRateGraph
    from gui.widgets.titlebar import _CustomTitleBar
    from gui.widgets.navbar import NavBar
    from gui.widgets.welcome import WelcomePane
    from gui.infra import SoundPlayer


    class MainWindowProto(Protocol):
        """
        MainWindow が持つ全属性の型宣言。
        Mixin のメソッドは `def foo(self: "MainWindowProto", ...)` のように
        self を MainWindowProto として注釈する。

        この Protocol を実装する具象クラスは gui.main_window.MainWindow のみ。
        実行時には Protocol チェックは行われない(TYPE_CHECKING 内なので)。
        """

        # ── 棋譜とエンジン ──────────────────────────────────
        _game: Optional[SGFGame]
        _current_idx: int
        _engine: Optional[KataGoEngine]
        _current_model_file: str
        _current_rules: str
        _current_komi: float
        _ai_enabled: bool
        _ownership_enabled: bool

        # ── pondering 重複防止 ─────────────────────────────
        _last_ponder_node_id: Optional[int]
        _last_ponder_sig_board: tuple
        _last_ponder_sig_analysis: tuple
        _last_ponder_sig_heavy: tuple

        # ── UI コンポーネント ──────────────────────────────
        _board: BoardWidget
        _board_container: BoardContainer
        _info: InfoPanel
        _move_info: MoveInfoCard
        _branch_tree: BranchTreeWidget
        _titlebar: _CustomTitleBar
        _navbar: NavBar
        _welcome: WelcomePane
        _comment: QTextEdit
        _sound: SoundPlayer

        # ── パネル状態 ────────────────────────────────────
        _panel_anim_running: bool
        _right_panel_collapsed: bool
        _last_panel_width: int

        # ── ウィンドウ状態 ────────────────────────────────
        _startup_board_size: int
        _is_maximizing: bool  # 必要に応じて
        # ... 他のウィンドウ状態フラグ

        # ── コメントオーバーレイ ───────────────────────────
        _comment_overlay: Optional[QWidget]
        _comment_overlay_anim: Optional[QPropertyAnimation]
        # ... 他のコメント関連属性

        # ── テーマ関連 ────────────────────────────────────
        _theme_fade_anim: Optional[QPropertyAnimation]
        # ... 他のテーマアニメ関連

        # ── ウェルカム遷移 ────────────────────────────────
        _is_welcome_mode: bool
        # ...

        # ── その他 ────────────────────────────────────────
        # (フェーズ5の実作業でリストを完全化する)

        # ── 定数(class 属性) ───────────────────────────
        _MIN_WIN_OPEN_W: int
        _MIN_WIN_OPEN_H: int
        _FP_MIN_W: int
        _FP_MARGIN_X: int

        # ── QMainWindow 由来のメソッド ────────────────────
        # Protocol では実装不要だが、Mixin から呼ぶものは宣言する
        def update(self) -> None: ...
        def show(self) -> None: ...
        def hide(self) -> None: ...
        def close(self) -> None: ...
        def setWindowOpacity(self, opacity: float) -> None: ...
        def windowOpacity(self) -> float: ...
        def isMaximized(self) -> bool: ...
        def isMinimized(self) -> bool: ...
        def setGeometry(self, *args: Any) -> None: ...
        def geometry(self) -> Any: ...
        # ... 他に Mixin から self.xxx() で呼ばれる Qt メソッド


# 実行時には何もしない
__all__ = ["MainWindowProto"] if TYPE_CHECKING else []
```

### 作業ポイント

1. **完全な属性リストを作る前に Mixin 本体を書き始めない**。途中で「あ、この属性も必要」となると手戻りが大きい
2. Step 5-2 のスクリプトで全シンボルを出し、メソッドと属性を仕分けする
3. 仕分けの判断に迷う場合は `MainWindow.__init__` 内で `self.xxx =` で代入されているか確認(代入があれば属性、なければメソッド)

### Mixin から呼ぶ Qt メソッドの宣言

`self.update()`, `self.setWindowOpacity()` などは `QMainWindow` 由来のメソッド。これらは Protocol では宣言しないと型エラーになる。**実用上 IDE 補完が効けば十分なので、よく使うものだけ宣言する**(全部は不要)。

---

## 検証

```bash
# 構文チェック
python -m py_compile gui/_mixins/_types.py

# import は TYPE_CHECKING 内なので実行時には何もしない
python -c "from gui._mixins._types import MainWindowProto; print('OK')"
# → TYPE_CHECKING 内なら NameError ではなく、Protocol が定義されていないため
#    実行時には __all__ が空なので、上の import は実は失敗する
# → 確認のための実行時 import は不要。型チェック時のみ意味がある
```

実行時に `MainWindowProto` を import しても意味がないので、検証は構文チェックのみで OK。

## コミット

```bash
git add gui/_mixins/__init__.py gui/_mixins/_types.py
git commit -m "refactor: add _mixins/_types.py for MainWindow type-checking support"
```

## 次フェーズへ

完了したら `06_phase6.md` へ(MainWindow の Mixin 分割本番)。

**重要**: フェーズ6 を始める前に、フェーズ5 で属性リストを完成させていることを確認する。属性宣言の追加は後でできるが、Mixin 6つを書きながら同時に Protocol を埋めるのは認知負荷が高い。
