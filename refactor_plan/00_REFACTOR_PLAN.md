# Kizuki リファクタリング計画書

このドキュメントは、`gui/main_window.py` (16,482行) を機能別モジュールに分割し、同時にデッドコード・潜在バグを除去するための完全な手順書です。Claude Code (CLI) で実行することを前提に書かれています。

---

## このリファクタリングの大原則

1. **動作を変えない**。UI 表示・操作感・KataGo 連携の挙動は完全に同一を保つ。リファクタは構造変更のみ。
2. **1フェーズずつ git commit する**。各フェーズ完了時にコミット → 動作確認 → 次フェーズ。
3. **検証を欠かさない**。各フェーズに「完了条件チェックリスト」がある。これを全て満たさない限り次へ進まない。
4. **不確実なときは止まる**。判断に迷う変更が出たら、その場で勝手に決めず、ユーザーに確認する。
5. **scope creep を避ける**。「ついでに直したい」が湧いてもこの計画書に書かれていない変更はしない。

---

## 全体像

### Before
```
files/
├── gui/
│   ├── main_window.py    (16,482行)
│   └── assets/
└── core/                  (健全・変更しない)
    ├── analyzer.py
    ├── game_state.py
    ├── katago_engine.py
    └── sgf_parser.py
```

### After
```
files/
├── gui/
│   ├── __init__.py
│   ├── main_window.py     (~1,000行 — MainWindow本体)
│   ├── theme.py           (~440行 — Theme/T()/配色/SP_*/PAD_*等の定数)
│   ├── fonts.py           (~70行  — Font_XS..XXL/FontMono_*)
│   ├── icons.py           (~250行 — make_icon/SVG/qss生成関数)
│   ├── infra.py           (~340行 — Profiler/SoundPlayer/BlunderInfo/TranslucentWidget)
│   ├── dialogs.py         (~1,620行 — 4種のダイアログ + _SplashScreen)
│   ├── menus.py           (~400行 — _KomiCustomWidget/_SubMenuPositioner/qmenu_qss)
│   ├── startup.py         (~450行 — main()/_check_models/_build_startup_engine)
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── common.py      (~620行 — FlatSlider/_RankItemDelegate/_AlwaysAcceptWheelTextEdit他)
│   │   ├── board.py       (~1,740行 — BoardWidget/BoardContainer)
│   │   ├── panels.py      (~1,880行 — MoveInfoCard/ScoreBoard/InfoPanel/Badge等)
│   │   ├── branchtree.py  (~1,110行 — BranchTreeWidget/_TreeEdgeFadeOverlay)
│   │   ├── graph.py       (~710行  — WinRateGraph/各種Axis/_GraphLabelOverlay)
│   │   ├── titlebar.py    (~670行  — _CustomTitleBar)
│   │   ├── navbar.py      (~470行  — NavBar/ToggleSwitch/ToggleBar/_HelpPopover)
│   │   └── welcome.py     (~580行  — WelcomePane/_WelcomeCard/_NewGameCard)
│   └── _mixins/
│       ├── __init__.py
│       ├── _types.py      (~150行  — TYPE_CHECKING用の共通Protocol)
│       ├── window_mgmt.py (最大化/最小化/パネル開閉/アニメ)
│       ├── navigation.py  (手戻り/分岐/スライダ/盤面更新)
│       ├── file_io.py     (SGF/D&D/コピペ/新規対局)
│       ├── engine_ctrl.py (KataGo/ponder/棋力/ルール/コミ/音量/グラフ更新)
│       ├── comments.py    (コメントオーバーレイ)
│       └── theme_ctrl.py  (テーマ切替/配色/ウェルカム⇔盤面遷移)
```

### 数字で見るゴール
- `main_window.py`: 16,482行 → **約1,000行**
- 最大ファイル: 5,806行(MainWindowクラス) → **約1,880行**(widgets/panels.py)
- 単一クラス最大メソッド数: 126個(MainWindow) → **約30個**(本体+UI構築のみ)

---

## フェーズ構成

リファクタリングは **8フェーズ** に分割します。各フェーズの所要時間目安と完了後の git commit メッセージ案も併記。

| フェーズ | 内容 | 目安 | コミット |
|---|---|---|---|
| 0 | 準備(バックアップ・ベースライン記録) | 短 | (コミット不要) |
| 1 | デッドコード削除 | 中 | `refactor: remove dead code in InfoPanel/MoveInfoCard/BoardWidget` |
| 2 | 基盤レイヤ抽出(theme/fonts/infra/icons/widgets/common) | 中 | `refactor: extract foundation layer (theme, fonts, icons, infra)` |
| 3 | UIコンポーネント抽出(widgets/*) | 大 | `refactor: extract UI components into gui/widgets/` |
| 4 | dialogs.py / menus.py / startup.py 抽出 | 中 | `refactor: extract dialogs, menus, and startup modules` |
| 5 | Mixin 型補助モジュール作成 | 短 | `refactor: add _mixins/_types.py for type-checking` |
| 6 | MainWindow → 6つの Mixin に分割 | 大 | `refactor: split MainWindow into 6 mixins` |
| 7 | hasattr → 属性初期化への置換 | 中 | `refactor: replace hasattr guards with __init__ initialization` |
| 8 | 最終整理(import整理・dead import削除・コメント整備) | 短 | `refactor: cleanup imports and update section comments` |

各フェーズの詳細は別ファイル `01_phase1.md` ... `08_phase8.md` を参照。

---

## 共通の作業ルール

### コミット前のチェック

各フェーズの作業後、以下を**必ず**実行する:

```bash
# 1. Python構文チェック (全 .py ファイル)
python -m py_compile gui/main_window.py
python -m py_compile gui/theme.py gui/fonts.py gui/infra.py gui/icons.py gui/dialogs.py gui/menus.py gui/startup.py
python -m py_compile gui/widgets/*.py
python -m py_compile gui/_mixins/*.py

# 2. import 確認 (循環import等が無いか)
python -c "from gui.main_window import MainWindow; print('OK')"

# 3. アプリ起動確認(ユーザー側で実施)
#   python -m gui.main_window
#   または既存の起動スクリプト
#   起動 → 数手進める → SGF読込 → 終了
```

### ファイル移動の手順(各クラス・関数共通)

1. 元の `main_window.py` 内でそのクラス/関数の **完全なコード範囲** を確認(class/def 開始行〜次の class/def or EOF まで)
2. 新ファイルにコピー(切り取りはNG。まずコピー)
3. 新ファイルに必要な import を追加(後述の「依存関係マップ」参照)
4. 元の `main_window.py` から削除
5. 元の `main_window.py` の上部に `from .新ファイル import 移したシンボル` を追加
6. **構文チェック → import チェック → 起動確認** が通ったらコミット

### コーディングコンベンション(厳守)

これらは Kizuki プロジェクト全体の規約。リファクタ後のコードも完全準拠すること。

- **間隔**: `SP_XS=4` / `SP_SM=8` / `SP_MD=12` / `SP_LG=16` / `SP_XL=24`
- **色**: `T()` singleton 経由(`BG`, `PANEL`, `TEXT`, `TEXT2`, `ACCENT`, `BORDER` 等)。ハードコード禁止。
- **フォント**: `Font_XS(12)` / `SM(14)` / `MD(16)` / `LG(18)` / `XL(24)` / `XXL(28)` を `setFont()` で適用
- **SVG**: `viewBox=draw size`、`stroke-width=1`、座標は整数+0.5
- **アニメ**: `QPropertyAnimation` で `windowOpacity`、180ms IN / 140ms OUT、`OutCubic`
- **ダーク/ライト両対応必須**

### 命名規則

- ファイル名: snake_case の新規命名(計画書通り)
- クラス名: **既存のまま変更しない**(例: `_CustomTitleBar` は `_CustomTitleBar` のまま)
- メソッド名・属性名: 既存のまま変更しない

---

## 重要な前提知識

### 既知のバグ・ハマりポイント(過去に踏んだ落とし穴)

以下は過去のデバッグで確定済みの注意事項です。リファクタ中もこれらに抵触しないこと。

1. `self.size` は QWidget 組み込みメソッド → 盤面サイズは `self.board_size`
2. `self.stones` は `dict {(col, row): "B"|"W"}` (リストではない)
3. `drawText` の Windows DirectWrite ヒンティングで字形ずれ → `QPainterPath + drawPath` で回避
4. QComboBox ポップアップ `setStyleSheet` で `setMaxVisibleItems` 無視 → `combobox-popup:0` 追加
5. combobox-popup:0 モードでスクロールバー既定で隠れる → `setVerticalScrollBarPolicy(ScrollBarAsNeeded)` 明示
6. WA_TranslucentBackground + Frameless ポップアップの白帯残存 → popup_window 透明化 + view 角丸+背景 + setMask 物理切り抜き
7. Qt はポップアップに OS ドロップシャドウを描画 → `NoDropShadowWindowHint` で無効化
8. QMenu の sizeHint キャッシュ問題 → `aboutToHide` で固定サイズ制約解除
9. 石とリングの中心ずれ → pixmap論理サイズ・pad・offset完全一致
10. popup_window のクラス名は `QComboBoxPrivateContainer`
11. `_WelcomeCard` でライトモード時にカード矩形が明るく塗られる → `WA_TranslucentBackground=True` で回避
12. ライトモード時に暗背景上のテキスト/アイコンが見えない → `_force_dark_palette` フラグで強制ダーク配色

### モジュールレベル状態(シングルトン)

以下はモジュールレベル変数として保持されている状態。リファクタで取り扱い注意:

| 変数 | 元の場所(行) | 移動先 | 備考 |
|---|---|---|---|
| `_profiler = _Profiler()` | L160 | `gui/infra.py` | プロファイラ singleton |
| `_theme = Theme("dark")` | L492 | `gui/theme.py` | テーマ singleton |
| `_submenu_positioner_instance = None` | L991 | `gui/menus.py` | サブメニュー位置補正 |

これらは「インポート時にインスタンス化される」性質なので、循環 import を起こすと初期化失敗します。**理論依存順**(下層→上層) を守ること:

```
theme.py ── fonts.py ── icons.py ── infra.py ── widgets/common.py
       ↘     ↓                     ↓
        widgets/{board, panels, branchtree, graph, titlebar, navbar, welcome}
                        ↓
                  dialogs.py / menus.py / startup.py
                        ↓
                  _mixins/*
                        ↓
                  main_window.py
```

下層は上層を import してはいけない。

---

## TYPE_CHECKING による Mixin 型補助 (フェーズ5・6の核心)

### なぜ必要か

Mixin 方式では各 `XxxMixin` クラスのメソッドが `self._engine`, `self._game`, `self._board` などを参照しますが、これらの属性は実際には合成クラス `MainWindow` 側で `__init__` 中に作られます。Mixin 単体を見るとこれらの属性の型が不明で、IDE 補完も mypy も効かなくなります。

### 解決策

`gui/_mixins/_types.py` に、Mixin が利用する全属性の型を宣言した `MainWindowProto` (Protocol) を定義し、各 Mixin はそれを継承させる形にします。実行時には Protocol は無視されるのでオーバーヘッドゼロ。

```python
# gui/_mixins/_types.py
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PyQt6.QtCore import QSettings, QPropertyAnimation
    from PyQt6.QtWidgets import QWidget, QTextEdit, QMenu, QAction, QSlider
    from core.game_state import GameState
    from core.katago_engine import KataGoEngine
    from gui.widgets.board import BoardWidget, BoardContainer
    from gui.widgets.panels import InfoPanel, MoveInfoCard
    from gui.widgets.branchtree import BranchTreeWidget
    from gui.widgets.titlebar import _CustomTitleBar
    from gui.widgets.navbar import NavBar
    from gui.infra import SoundPlayer

    class MainWindowProto(Protocol):
        # ── 棋譜・エンジン関連 ──────────────────────────────
        _game: GameState | None
        _current_idx: int
        _engine: KataGoEngine | None
        _current_model_file: str
        _current_rules: str
        _current_komi: float
        _ai_enabled: bool
        _ownership_enabled: bool
        _last_ponder_node_id: int | None
        _last_ponder_sig_board: tuple
        _last_ponder_sig_analysis: tuple
        _last_ponder_sig_heavy: tuple

        # ── UIコンポーネント参照 ────────────────────────────
        _board: BoardWidget
        _board_container: BoardContainer
        _info: InfoPanel
        _move_info: MoveInfoCard
        _branch_tree: BranchTreeWidget
        _titlebar: _CustomTitleBar
        _navbar: NavBar
        _comment: QTextEdit
        _sound: SoundPlayer

        # ── パネル状態 ────────────────────────────────────
        _panel_anim_running: bool
        _right_panel_collapsed: bool
        _last_panel_width: int

        # ... 残りの属性(計画書フェーズ5に完全リスト)
```

各 Mixin はこう書く:

```python
# gui/_mixins/navigation.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

    # Mixinに型をつけたいときは、_Self を Protocol にする
    _Self = MainWindowProto
else:
    _Self = object

class NavigationMixin:
    def _goto(self: "MainWindowProto", idx: int) -> None:
        # self._game / self._current_idx 等が型補完される
        ...
```

### 注意

- `_types.py` の属性リストは **フェーズ6 の前に完成させる**。Mixin を書き始めてから「あ、この属性も必要」となるとリファクタが遅くなる。フェーズ5で属性洗い出しを完了させる。
- 計画書の `attrs_inventory.md` に MainWindow 全113属性のリストがあるので、それを基に Protocol を作る。

---

## 検証戦略

### 各フェーズでの最低限の検証

1. `python -m py_compile` で全 .py ファイル構文OK
2. `python -c "from gui.main_window import MainWindow"` で import が通る
3. **ユーザーがアプリを起動して以下シナリオを実行**:
   - 起動 → 初期画面(またはデモ局面)表示
   - 数手進める/戻る
   - SGF 読み込み
   - テーマ切替(ダーク ⇔ ライト)
   - ウィンドウ最大化・最小化
   - 右パネル開閉
   - コメントオーバーレイ開閉
   - アプリ終了
   - **再起動して設定が保持されているか**

### フェーズごとの追加検証

- フェーズ1 (デッドコード削除): 各削除箇所が本当に呼ばれていないか、`grep -n` で再確認
- フェーズ2 (基盤抽出): `theme.T()` `Font_*` `make_icon()` などが他モジュールから呼べる
- フェーズ3 (UIコンポーネント抽出): 各 widget の単独 import が通る
- フェーズ4: ダイアログが開閉する
- フェーズ6 (Mixin): MRO 順序の確認 (後述)
- フェーズ7 (hasattr整理): 削除した hasattr 箇所が AttributeError を起こさない

### MainWindow の Mixin 合成順 (フェーズ6で重要)

```python
# gui/main_window.py
from gui._mixins.window_mgmt import WindowMgmtMixin
from gui._mixins.navigation import NavigationMixin
from gui._mixins.file_io   import FileIOMixin
from gui._mixins.engine_ctrl import EngineCtrlMixin
from gui._mixins.comments import CommentsMixin
from gui._mixins.theme_ctrl import ThemeCtrlMixin

class MainWindow(
    WindowMgmtMixin,
    NavigationMixin,
    FileIOMixin,
    EngineCtrlMixin,
    CommentsMixin,
    ThemeCtrlMixin,
    QMainWindow,  # 必ず最後に Qt基底クラスを置く
):
    ...
```

**重要**: `QMainWindow` を最後に置く。Python の MRO は左から右へ解決するため、Mixin が `super()` を使う場合の挙動を予測可能にするため。

---

## 進め方の指示(Claude Code 用)

このリポジトリで作業する Claude Code への指示は別ファイル `99_initial_prompt.md` を参照。

各フェーズの詳細手順は `01_phase1.md` 〜 `08_phase8.md` を順に開いて従うこと。

途中で迷ったら、計画書に書かれていない判断をする前に**必ず停止してユーザーに確認**。
