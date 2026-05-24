# Phase 6: MainWindow を Mixin に分割

## 目的

`MainWindow` クラス(5,806行・126メソッド)を、責務ごとに 6つの Mixin に分割する。リファクタリングの本丸。

## 全体方針

### Mixin の MRO 設計

```python
# gui/main_window.py
from PyQt6.QtWidgets import QMainWindow

from gui._mixins.window_mgmt import WindowMgmtMixin
from gui._mixins.navigation  import NavigationMixin
from gui._mixins.file_io     import FileIOMixin
from gui._mixins.engine_ctrl import EngineCtrlMixin
from gui._mixins.comments    import CommentsMixin
from gui._mixins.theme_ctrl  import ThemeCtrlMixin


class MainWindow(
    WindowMgmtMixin,
    NavigationMixin,
    FileIOMixin,
    EngineCtrlMixin,
    CommentsMixin,
    ThemeCtrlMixin,
    QMainWindow,   # ★ 必ず最後に Qt 基底クラス
):
    def __init__(self, engine=None):
        super().__init__()
        # ... ここは本体だけが持つ初期化
```

`QMainWindow` を最後に置く理由: MRO 解決順は Python の C3 線形化に従い「左→右」。Mixin 間で `super()` を使わない限り問題ないが、Qt 基底を最後にしておけば eventFilter などの super 呼び出しが安全。

### 各 Mixin に移すメソッド一覧

#### WindowMgmtMixin (gui/_mixins/window_mgmt.py)

ウィンドウ管理・サイズ変更・最大化/最小化・パネル開閉・アニメーション汎用。

```
closeEvent
_apply_min_window_size
_place_panels
showEvent
_enable_win11_rounded_corners
_do_prewarm_overlay_once
_toggle_maximized
_update_overlay_geometry
_on_maximize_anim_done
_detect_taskbar_direction
_toggle_right_panel
_animate_max_panel_toggle
_on_panel_anim_done
_animated_minimize
_update_min_overlay_geometry
_on_minimize_anim_done
_animated_close
_animated_show_on_startup
_on_startup_anim_done
_cleanup_root_opacity_effect
_is_pseudo_maximized
_set_win11_corner_round
changeEvent
_animated_show_from_minimized
_on_restore_anim_done
_edge_at
resizeEvent
_animate_unified
_smooth_scroll_to
```

#### NavigationMixin (gui/_mixins/navigation.py)

棋譜の手戻り・分岐操作・盤面状態の更新。

```
_goto
_goto_no_ponder
_resolve_goto_target
_goto_first
_goto_last
_prev
_next
_on_slider_value_changed
_on_slider_pressed
_on_slider_drag
_on_slider_released
_on_graph_dragged
_on_graph_released
_wheel_step
_wheel_trailing_refresh
wheelEvent
_on_branch_node_clicked
_on_delete_branch_node
_on_move_number_anchor_requested
_build_moves_to_node
_on_delete_node
_on_board_click
_build_states
_refresh_board
_refresh_board_minimal
```

#### FileIOMixin (gui/_mixins/file_io.py)

SGF・ドラッグドロップ・コピペ・新規対局。

```
_open_sgf_path
_open_sgf
_save_sgf
_confirm_discard_or_save
_copy_sgf
_paste_sgf
_save_board_screenshot
_load_demo
_new_game
_has_supported_kifu
_show_drop_overlay
_hide_drop_overlay
dragEnterEvent
dragMoveEvent
dragLeaveEvent
dropEvent
```

#### EngineCtrlMixin (gui/_mixins/engine_ctrl.py)

KataGo・pondering・棋力・ルール・コミ・音量・グラフ更新。

```
_scan_models
_create_engine
_on_ai_toggle
_on_ownership_toggle
_on_move_numbers_toggled
_start_pondering_current
_on_ponder_result
_show_first_launch_rank_dialog
_on_rank_action
_on_rank_menu_about_to_show
_on_rank_menu_about_to_hide
_apply_rules_komi_to_engine
_on_rules_changed
_sync_komi_menu_check
_on_komi_changed
_on_komi_custom_confirmed
_on_komi_realtime_change
_on_komi_other_value_adjusted
_on_volume_changed
_on_volume_icon_clicked
_volume_slider_mouse_press
_invalidate_graph_struct_cache
_update_graph
```

#### CommentsMixin (gui/_mixins/comments.py)

コメントオーバーレイの開閉・編集・保存。

```
_write_comment_to_node
_save_comment_if_editing
_on_node_comment_requested
_toggle_comment_overlay
_open_comment_overlay
_on_comment_overlay_open_done
_cancel_comment_overlay_anim
_prewarm_comment_overlay
_load_comment_to_overlay
_close_comment_overlay
_on_comment_overlay_close_done
_on_comment_overlay_changed
_is_inside_comment_overlay
```

#### ThemeCtrlMixin (gui/_mixins/theme_ctrl.py)

テーマ切替・配色適用・ウェルカム⇔盤面遷移・コメントスタイル更新。

```
apply_theme
_on_theme_fade_done
_cancel_theme_fade
_apply_theme_immediate
_open_color_adjustment
_apply_comment_close_btn_qss
_set_welcome_mode
_prepare_welcome_to_board_fade
_animate_welcome_to_board
```

#### MainWindow 本体に残すもの

```
__init__              (初期化全部 + 各 Mixin の参照する属性を全て None で先に置く)
_build_ui             (UI構築)
_build_menu           (メニュー構築)
_reset_to_first_launch_and_quit  (デバッグ用・残す)
_app_event_filter_active
eventFilter
mousePressEvent
mouseDoubleClickEvent
mouseMoveEvent
keyPressEvent
```

`MainWindow` クラス自体は 約 1,000 行(`__init__` + `_build_ui` + `_build_menu` + 数個のイベントハンドラ)。

---

## 作業手順

**1 Mixin ずつ抽出してコミット**。6 Mixin を一気にやらない。順序は依存度の低いものから:

1. `theme_ctrl.py` (依存少なめ)
2. `comments.py` (コメント関連で閉じている)
3. `file_io.py` (SGF とドラッグドロップ)
4. `window_mgmt.py` (アニメ系で副作用多いので慎重に)
5. `engine_ctrl.py` (一番複雑、KataGo連携)
6. `navigation.py` (盤面更新を含むので最後)

各 Mixin の作業手順:

### 共通テンプレート

```python
# gui/_mixins/XXX.py
"""
gui/_mixins/XXX.py — YYY 関連のメソッド群を MainWindow に提供する Mixin。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto
    _Self = MainWindowProto
else:
    _Self = "Any"

# 実行時に必要な import (PyQt6 など)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import ...


class XxxMixin:
    """YYY 関連メソッドを提供する Mixin。"""

    def method_name(self: "MainWindowProto", arg1: int) -> None:
        # self._engine, self._board などが型補完される
        ...
```

### Step ごとの作業

1. `gui/_mixins/XXX.py` を作成し、テンプレートを書く
2. `main_window.py` から対象メソッドを **コピー** で Mixin に移す
   - メソッドのインデント(`    def` から `    def`)を維持
   - 最初の引数 `self` に `: "MainWindowProto"` の型注釈を追加
3. `main_window.py` から対象メソッドを削除
4. `MainWindow` クラスの定義行を更新して Mixin を継承:
   ```python
   class MainWindow(XxxMixin, ..., QMainWindow):
   ```
5. 構文・import・起動確認
6. 動作確認(Mixin 担当領域の機能を全部触る)
7. コミット

---

## 詳細手順 (theme_ctrl.py を例に)

### Step 6-1: theme_ctrl.py

1. `gui/_mixins/theme_ctrl.py` を新規作成

```python
"""
gui/_mixins/theme_ctrl.py — テーマ切替・ウェルカム遷移を MainWindow に提供する。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtWidgets import QWidget, QGraphicsOpacityEffect

import gui.theme as _theme_module
from gui.theme import T


class ThemeCtrlMixin:
    def apply_theme(self: "MainWindowProto", mode: str) -> None:
        # ... (元の MainWindow.apply_theme 内容)

    def _on_theme_fade_done(self: "MainWindowProto") -> None:
        ...

    # ... 他のメソッド
```

2. `main_window.py` の `MainWindow` クラスから該当メソッドを削除

3. クラス定義行を更新:
```python
from gui._mixins.theme_ctrl import ThemeCtrlMixin

class MainWindow(ThemeCtrlMixin, QMainWindow):  # 段階的に追加
    ...
```

4. 検証:
```bash
python -m py_compile gui/_mixins/theme_ctrl.py
python -c "import gui.main_window"
# アプリ起動 → テーマ切替動作確認
```

5. コミット:
```bash
git commit -m "refactor: extract ThemeCtrlMixin from MainWindow"
```

### Step 6-2 〜 Step 6-6: 残りの5つの Mixin

同じ手順で順に。各ステップで:
- `class MainWindow(XxxMixin, YyyMixin, ..., QMainWindow):` のように Mixin を追加していく
- 該当領域の動作を確認(例: file_io 後は SGF 読み込み・D&D を試す)
- コミットメッセージは `refactor: extract XxxMixin from MainWindow`

---

## 重要な落とし穴と回避策

### 落とし穴1: Qt の event handler が Mixin にあると効かない可能性

Qt は `dragEnterEvent`, `dropEvent`, `wheelEvent`, `resizeEvent`, `closeEvent`, `changeEvent`, `showEvent` などを **メソッド名で検出** してオーバーライド扱いする。Mixin 経由でも基本的には動くが、MRO で `QMainWindow` より前に Mixin があれば OK。

**対策**: 上の MRO 定義で `QMainWindow` を最後にしているので問題なし。ただし、念のため移動後に該当イベントが正しく発火するか確認する。

### 落とし穴2: pyqtSignal の Mixin での扱い

`MainWindow` に `pyqtSignal` が定義されていた場合(本コードベースでは未確認)、Mixin への移動は注意が必要。Signal は class 属性として `QObject` 派生クラス上に定義されている必要がある。

**対策**: フェーズ6 開始前に grep:
```bash
grep -n "pyqtSignal\|Signal()" gui/main_window.py
```
これで `MainWindow` 内に Signal が定義されているか確認。あれば本体側に残す。

### 落とし穴3: super() の挙動

Mixin 内で `super().eventFilter(obj, ev)` を呼ぶ場合、MRO に従って次のクラスに渡される。`QMainWindow` を最後にしているので、Mixin の `super().eventFilter` は `QMainWindow.eventFilter` に到達する。

**対策**: `eventFilter` などのオーバーライドは `MainWindow` 本体に残す方が安全(下記参照)。

### 落とし穴4: 同じメソッドを複数 Mixin に分散できない

例えば `eventFilter` を `WindowMgmtMixin` と `CommentsMixin` の両方に書くと、後者で完全上書きされる(MRO 順)。

**対策**: `eventFilter`, `mousePressEvent` などイベントハンドラは **MainWindow 本体** に残し、内部で各 Mixin のヘルパーを呼ぶ。

例:
```python
# MainWindow 本体
def eventFilter(self, obj, ev):
    # WindowMgmt 関連の処理
    if self._handle_window_event(obj, ev):
        return True
    # Comments 関連の処理
    if self._is_inside_comment_overlay(obj):
        return self._handle_comment_event(obj, ev)
    return super().eventFilter(obj, ev)
```

ただし、現状の `eventFilter` は L14177-14431 で 255行ある。これを綺麗に分解するのはこのフェーズの範囲外。**今回は eventFilter は MainWindow 本体に残す** ことを推奨。

---

## フェーズ全体の検証チェックリスト

- [ ] `gui/_mixins/` に 6つの Mixin ファイル + `_types.py` + `__init__.py` が存在
- [ ] `MainWindow` クラスが 6つの Mixin + `QMainWindow` を継承
- [ ] `python -c "import gui.main_window; print('OK')"` が通る
- [ ] アプリ起動 → 全シナリオ完走
  - [ ] ウィンドウ管理(最大化・最小化・パネル開閉・タイトルバードラッグ)
  - [ ] ナビゲーション(進む・戻る・スライダ・分岐ジャンプ)
  - [ ] SGF読込・保存・D&D
  - [ ] KataGo連携(AI ON/OFF, ownership ON/OFF, ponder結果反映, 棋力切替, ルール・コミ変更)
  - [ ] コメント編集(開閉・編集・保存)
  - [ ] テーマ切替・ウェルカム遷移
- [ ] eventFilter が正しく動作(キーボード操作・マウス追跡)
- [ ] `main_window.py` の行数が ~5,800 → ~1,000 に減っていることを `wc -l` で確認

## 次フェーズへ

完了したら `07_phase7.md` へ。
