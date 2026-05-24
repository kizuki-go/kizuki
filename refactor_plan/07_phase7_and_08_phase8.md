# Phase 7: hasattr → 属性初期化への置換

## 目的

`hasattr(self, "_xxx")` の防衛的ガードを、`__init__` での **明示的な初期化** に置き換える。これによりコードの依存関係が明確になり、IDE 補完も効くようになる。

## 背景

調査で判明したこと:
- `hasattr(self, ...)` は `MainWindow` 内に **114回**、他クラス含めて 148回
- 多くは「属性が後から作られる(遅延生成)」設計で、初期化忘れによる AttributeError を防ぐためのガード
- フェーズ6 で Mixin 分割が完了している今、`MainWindowProto` に属性が宣言されているなら、`__init__` で `None` 初期化すれば `hasattr` ガードは不要になる

## 作業方針

**全 148箇所を一気にやらない**。クラスごとに進める。

### 進め方

各クラスについて:
1. `__init__` で必ず最初に `self._xxx = None` (または適切な初期値)を書く
2. 各メソッド内の `if hasattr(self, "_xxx"):` を `if self._xxx is not None:` に置換
3. 動作確認
4. コミット

### 優先順位

1. `MainWindow` (114箇所 — 最大の効果)
2. `MoveInfoCard` (17箇所)
3. その他のクラス(`_CustomTitleBar` 7箇所、他は少数)

---

## Step 7-1: MainWindow

### 準備

フェーズ5 で作成した `gui/_mixins/_types.py` の `MainWindowProto` にある属性リストを参照する。全 113 属性が `MainWindow.__init__` の冒頭で初期化される必要がある。

### __init__ の整理

`MainWindow.__init__` の先頭に **属性初期化ブロック** を作る:

```python
class MainWindow(WindowMgmtMixin, NavigationMixin, FileIOMixin,
                 EngineCtrlMixin, CommentsMixin, ThemeCtrlMixin,
                 QMainWindow):
    # ── class 定数 ──────────────────────────────────
    _MIN_WIN_OPEN_W = 1280
    _MIN_WIN_OPEN_H = 800
    _FP_MIN_W = 380
    _FP_MARGIN_X = 8

    def __init__(self, engine=None):
        super().__init__()

        # ══════════════════════════════════════════════
        # 属性の事前初期化
        # 全 Mixin から self.xxx で参照される属性をここで定義する。
        # これにより hasattr() ガードが不要になり、IDE 補完も効く。
        # ══════════════════════════════════════════════

        # ── 棋譜・エンジン ──────────────────────────────
        self._game = None
        self._current_idx = 0
        self._engine = engine
        self._current_model_file = ""
        self._current_rules = "japanese"
        self._current_komi = 6.5
        self._ai_enabled = True
        self._ownership_enabled = False

        # ── ponder 重複防止 ────────────────────────────
        self._last_ponder_node_id = None
        self._last_ponder_sig_board = ()
        self._last_ponder_sig_analysis = ()
        self._last_ponder_sig_heavy = ()

        # ── UI コンポーネント参照(後で _build_ui で生成) ──
        self._board = None              # type: ignore[assignment]
        self._board_container = None    # type: ignore[assignment]
        self._info = None               # type: ignore[assignment]
        self._move_info = None          # type: ignore[assignment]
        self._branch_tree = None        # type: ignore[assignment]
        self._titlebar = None           # type: ignore[assignment]
        self._navbar = None             # type: ignore[assignment]
        self._welcome = None            # type: ignore[assignment]
        self._comment = None            # type: ignore[assignment]
        self._sound = None              # type: ignore[assignment]

        # ── パネル状態 ──────────────────────────────────
        self._panel_anim_running = False
        self._right_panel_collapsed = False
        self._last_panel_width = 0

        # ── コメントオーバーレイ ────────────────────────
        self._comment_overlay = None
        self._comment_overlay_anim = None

        # ── テーマ・ウェルカム ──────────────────────────
        self._theme_fade_anim = None
        self._is_welcome_mode = False

        # ── その他のアニメ・状態 ───────────────────────
        # (フェーズ7 の実作業でリスト完全化)

        # ══════════════════════════════════════════════
        # 既存の __init__ 処理が続く
        # ══════════════════════════════════════════════
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # ... 以下既存
```

### hasattr の置換

例:
```python
# 旧
if hasattr(self, "_comment_overlay") and self._comment_overlay:
    self._comment_overlay.show()

# 新
if self._comment_overlay is not None:
    self._comment_overlay.show()
```

```python
# 旧
if hasattr(self, "_engine"):
    self._engine.stop()

# 新
if self._engine is not None:
    self._engine.stop()
```

### 単純な hasattr 削除のパターン

```python
# 旧: 「属性が存在すること」のみ確認、None でも True 扱い
if hasattr(self, "_xxx"):
    do_something(self._xxx)

# 新: __init__ で必ず存在することが保証されたので、ガード自体不要
do_something(self._xxx)
```

ただし `None` チェックが必要な場合もあるので、ケースバイケース。

### 作業手順

1. `MainWindow.__init__` 冒頭に属性初期化ブロックを追加
2. `grep -n "hasattr(self" gui/main_window.py | head -20` で先頭20箇所を確認
3. 1箇所ずつ `hasattr → is not None` に置換 or 削除
4. 動作確認(少なくとも10箇所変更ごと)
5. 進められる範囲で進めてコミット

`grep -c "hasattr(self" gui/main_window.py` の数字が大幅に減ること。

### 検証

```bash
# 数値で減少を確認
grep -c "hasattr(self" gui/main_window.py
# フェーズ7 開始時: 114
# 完了目標: 20以下(完全に消せないものは残す)
```

完全削除を目指す必要はない。「明らかに __init__ で初期化できるもの」を片付けるだけで十分。

### コミット

```bash
git add gui/main_window.py
git commit -m "refactor: replace hasattr guards with explicit __init__ initialization in MainWindow"
```

---

## Step 7-2: MoveInfoCard

`gui/widgets/panels.py` 内の `MoveInfoCard` クラス。17箇所の hasattr 整理。

同じ手順で:
1. `__init__` 冒頭で属性を全部初期化(`_badge`, `_metric_div`, `_missing_msg` など)
2. `hasattr(self, "_xxx")` を置換
3. 動作確認
4. コミット

```bash
git commit -m "refactor: replace hasattr guards in MoveInfoCard"
```

---

## Step 7-3: その他のクラス

`_CustomTitleBar` (7箇所), `WinRateGraph` (4箇所) も同様に。

「少数なので影響範囲も小さい」「触らないと一貫性に欠ける」の判断。1コミットでまとめて OK。

```bash
git commit -m "refactor: replace remaining hasattr guards in misc widgets"
```

---

## フェーズ7 検証チェックリスト

- [ ] `grep -c "hasattr(self" gui/main_window.py` が 114 → 20以下に減っている
- [ ] `grep -c "hasattr(self" gui/widgets/panels.py` が大幅減
- [ ] アプリ起動 → 全シナリオ完走
- [ ] 特に「遅延生成されていた属性」が今も正しく機能する(コメントオーバーレイの初回開閉など)

---

# Phase 8: 最終整理(import整理・コメント整備)

## 目的

リファクタの最終仕上げ。コードベース全体の見栄えを整える。

## 作業内容

### Step 8-1: 不要 import の削除

各ファイルを開いて、使われていない import を削除する。

```bash
# pyflakes や ruff があれば
ruff check --select F401 gui/
# 無ければ grep で目視
```

特に `main_window.py` は大量の import が冒頭にあり、フェーズ2〜6 でクラスが移動した結果、使われていない import が残っているはず。

### Step 8-2: import 順序の統一

各ファイル冒頭の import を以下の順に整理:

```python
# 1. 標準ライブラリ
import sys, logging
from pathlib import Path
from typing import Optional

# 2. サードパーティ
from PyQt6.QtWidgets import ...
from PyQt6.QtCore import ...
from PyQt6.QtGui import ...

# 3. プロジェクト内 (core)
from core.katago_engine import KataGoEngine
from core.game_state import GameState
from core.sgf_parser import ...

# 4. プロジェクト内 (gui)
from gui.theme import T, SP_SM, SP_MD
from gui.fonts import Font_SM, Font_MD
from gui.icons import make_icon
from gui.infra import _profile
from gui.widgets.board import BoardWidget
# ... etc
```

### Step 8-3: __init__.py の整理

`gui/__init__.py` で公開する API を整理(必要なら):

```python
"""gui — Kizuki の GUI レイヤ"""
# 必要に応じて公開シンボルを集約
# 強制ではない。何も書かなくても良い
```

### Step 8-4: セクションコメント追加

各モジュール内で、論理的な区切りに以下のような区切り線を追加:

```python
# ══════════════════════════════════════════════════════════════════════════════
# 〇〇関連
# ══════════════════════════════════════════════════════════════════════════════
```

これは既存コードの `_Profiler` の上に既に使われているスタイル(L50)。

### Step 8-5: モジュールごとの docstring 整備

各ファイル冒頭の `"""..."""` を最新化:

```python
"""
gui/widgets/board.py — 碁盤描画ウィジェット。

主要クラス:
- BoardWidget: 碁盤の描画と入力処理。19路/13路/9路に対応。
- BoardContainer: BoardWidget を中央寄せ・サイズ管理するコンテナ。

依存:
- gui.theme: 色・間隔
- gui.fonts: 座標ラベル等のフォント
- gui.infra: プロファイラ
"""
```

### Step 8-6: README.md の更新(任意)

リファクタによりプロジェクト構造が変わったので、`README.md` の「ファイル構成」セクションを更新する(あれば)。

---

## 検証

```bash
# 構文チェック・全ファイル
find gui -name "*.py" -exec python -m py_compile {} \;

# 起動確認
python -m gui.startup
```

最終動作確認シナリオ:
- [ ] 起動 → スプラッシュ → メイン画面
- [ ] デモ局面の読み込み
- [ ] 操作: 進む・戻る・スライダ
- [ ] AI on/off, ownership on/off, 手数表示 on/off
- [ ] SGF 開く・保存・スクリーンショット保存
- [ ] D&D で SGF 読込
- [ ] テーマ切替
- [ ] ルール・コミ変更
- [ ] 棋力変更
- [ ] コメント編集
- [ ] 分岐ツリー操作
- [ ] パネル開閉
- [ ] 最大化・最小化・通常サイズ復帰
- [ ] アプリ終了

---

## 最終コミット

```bash
git add -A
git commit -m "refactor: cleanup imports, add section comments and module docstrings"

# 全コミットを確認
git log --oneline refactor/phase1-dead-code..HEAD
```

リファクタ完了!

---

## 完了後の状態

| 指標 | Before | After |
|---|---|---|
| `main_window.py` 行数 | 16,482 | ~1,000 |
| 最大ファイル | 16,482 | ~1,900 (widgets/panels.py) |
| `MainWindow` クラス行数 | 5,806 | ~600 |
| `MainWindow` メソッド数 | 126 | ~30 |
| `hasattr(self, ...)` 総数 | 148 | <30 |
| デッドコード | 多数 | 削除済 |

これで Kizuki のコードベースは今後の機能追加に耐えうる構造になります。

リファクタご苦労様でした。
