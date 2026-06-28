"""
gui/_mixins/_types.py — Mixin で利用する型ヒント補助。

このモジュールは TYPE_CHECKING 時のみ評価される Protocol を提供する。
実行時には何もしない (TYPE_CHECKING ブロック内の import / クラス定義は
評価されないため、循環 import の心配もない)。

Phase 6 で書く各 Mixin は self の型を MainWindowProto として注釈する:

    class NavigationMixin:
        def _goto(self: "MainWindowProto", idx: int) -> None:
            ...

これにより self._engine / self._board などへの参照に IDE 補完と
mypy/pyright の型チェックが効く。MainWindowProto を実装する具象クラスは
gui.main_window.MainWindow のみ。

属性リストは Phase 5 で main_window.py を AST スキャンして洗い出した
117 インスタンス属性 + 22 クラスレベル定数 + 1 pyqtSignal をすべて
網羅している (見落としを防ぐため、用途未確定の属性は Any にしている)。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol, Optional, Any

if TYPE_CHECKING:
    # ── PyQt6 ────────────────────────────────────────
    from PyQt6.QtCore import (
        QTimer, QRect, QPoint, QSize, QObject, pyqtBoundSignal,
    )
    from PyQt6.QtWidgets import (
        QWidget, QStackedWidget, QScrollArea, QStatusBar, QLabel,
        QPushButton, QMenu, QListWidget, QActionGroup, QGraphicsOpacityEffect,
    )
    from PyQt6.QtGui import QAction
    # アニメ系: 単一/グループを抽象化して扱うため共通基底 QAbstractAnimation を使う
    from PyQt6.QtCore import QAbstractAnimation, QPropertyAnimation

    # ── core ─────────────────────────────────────────
    from core.game_state import GameState
    from core.katago_engine import KataGoEngine
    from core.sgf_parser import SGFGame, SGFNode

    # ── gui パッケージ ───────────────────────────────
    from gui.widgets.board import BoardWidget, BoardContainer
    from gui.widgets.panels import InfoPanel, MoveInfoCard
    from gui.widgets.branchtree import BranchTreeWidget
    from gui.widgets.titlebar import _CustomTitleBar
    from gui.widgets.navbar import NavBar, ToggleBar
    from gui.widgets.welcome import WelcomePane, _WelcomeCard
    from gui.widgets.common import (
        _RankItemDelegate, FlatSlider, _ScrollFadeOverlay,
        _AlwaysAcceptWheelTextEdit,
    )
    from gui.infra import SoundPlayer, TranslucentWidget
    from gui.menus import _KomiCustomWidget
    from gui.dialogs import ColorAdjustmentDialog


    class MainWindowProto(Protocol):
        """MainWindow が持つ全属性の型宣言。

        Mixin のメソッドは `self: "MainWindowProto"` のように self の型を
        この Protocol で注釈することで、属性アクセス時の補完と型チェックが効く。

        本 Protocol を実装する具象クラスは gui.main_window.MainWindow のみで、
        実行時には Protocol チェックは行われない (TYPE_CHECKING 内なので)。
        """

        # ═════════════════════════════════════════════════════════════
        # クラスレベル定数 (class attributes)
        # ═════════════════════════════════════════════════════════════
        DEFAULT_KOMI: float
        DEFAULT_RANK: int
        DEFAULT_RULES: str
        KATAGO_DIR: str
        KOMI_PRESETS: list
        RANK_OPTIONS: list
        RULES: list
        RULE_DEFAULT_KOMI: dict
        _COMMENT_ANIM_DURATION: int
        _COMMENT_ANIM_SLIDE_PX: int
        _FP_MARGIN: int
        _FP_MARGIN_X: int
        _FP_MAX_W: int
        _FP_MIN_W: int
        _FP_RADIUS: int
        _FP_RATIO: float
        _MIN_WIN_OPEN_H: int
        _MIN_WIN_OPEN_W: int
        _RESIZE_BORDER: int
        _SCROLL_ANIM_DURATION_MS: int
        _SUPPORTED_KIFU_EXTS: tuple
        _WHEEL_THROTTLE_MS: int

        # ═════════════════════════════════════════════════════════════
        # シグナル
        # ═════════════════════════════════════════════════════════════
        _ponder_result_signal: pyqtBoundSignal

        # ═════════════════════════════════════════════════════════════
        # 棋譜とエンジン
        # ═════════════════════════════════════════════════════════════
        _engine: Optional[KataGoEngine]
        _game: Optional[SGFGame]
        _game_state: Optional[GameState]
        _current_idx: int
        _current_komi: float
        _current_model_file: str
        _current_rules: str
        _current_sgf_path: Optional[str]
        _is_dirty: bool

        # AI / ownership トグル
        _ai_enabled: bool
        _ownership_enabled: bool
        _move_numbers_enabled: bool
        _move_number_anchor: Optional[SGFNode]

        # ─ 解析キャッシュ ─
        _node_analyses: dict
        _graph_struct_cache: Any
        _graph_last_cur_node: Optional[SGFNode]
        _pondering_node: Optional[SGFNode]

        # ─ ponder 重複防止 / スロットル ─
        _last_ponder_node_id: Optional[int]
        _last_ponder_sig_analysis: Optional[tuple]
        _last_ponder_sig_board: Optional[tuple]
        _last_ponder_sig_heavy: Optional[tuple]
        _ponder_full_last_t: float
        _ponder_full_throttle_ms: int
        _ponder_heavy_last_t: float
        _ponder_heavy_throttle_ms: int
        _first_intermediate_received: bool

        # ═════════════════════════════════════════════════════════════
        # UI コンポーネント (Phase 3 で抽出した widget 群)
        # ═════════════════════════════════════════════════════════════
        _board: BoardWidget
        _board_container: BoardContainer
        _branch_tree: BranchTreeWidget
        _branch_scroll: QScrollArea
        _info: InfoPanel
        _move_card: MoveInfoCard
        _navbar: NavBar
        _titlebar: _CustomTitleBar
        _welcome_pane: WelcomePane
        _drop_card: _WelcomeCard
        _drop_overlay: QWidget
        _sound: SoundPlayer
        _toggle_bar: ToggleBar
        _slider: FlatSlider
        _status_bar: QStatusBar

        # ─ レイアウトコンテナ ─
        _container: QWidget
        _root_widget: QWidget
        _left_col: QWidget
        _right_col: QWidget
        _left_stack: QStackedWidget
        _cards_scroll: QScrollArea

        # ─ コメントオーバーレイ ─
        _comment: _AlwaysAcceptWheelTextEdit
        _comment_textedit: _AlwaysAcceptWheelTextEdit
        _comment_overlay: TranslucentWidget
        _comment_fade_overlay: _ScrollFadeOverlay
        _comment_close_btn: QPushButton

        # ─ 音量パネル ─
        _volume_container: QWidget
        _volume_label: QLabel
        _volume_slider: FlatSlider
        _volume_menu: QMenu
        _volume_close_state: dict
        _vol_container_filter: QObject

        # ─ メニュー ─
        _komi_menu: QMenu
        _komi_group: QActionGroup
        _komi_custom_action: Any   # QWidgetAction
        _komi_custom_widget: _KomiCustomWidget
        _komi_via_other: bool
        _rank_menu: QMenu
        _rank_list_widget: QListWidget
        _rank_item_delegate: _RankItemDelegate

        # ─ アクション (QAction) ─
        _action_dark: QAction
        _action_light: QAction
        _action_komi: dict
        _action_rules: dict
        _copy_act: QAction
        _save_act: QAction
        _ss_act: QAction
        _ss_win_act: QAction

        # ─ ダイアログ ─

        # ═════════════════════════════════════════════════════════════
        # ウィンドウ状態 / アニメーション
        # ═════════════════════════════════════════════════════════════
        _anim_in_progress: bool
        _on_resize_edge: bool
        _overlay_prewarmed: bool
        _win11_corners_applied: bool
        _startup_board_size: int

        # ─ 最大化 / 最小化 / 復元 ─
        _pre_max_geometry: QRect
        _pre_minimize_geometry: QRect
        _pre_minimize_opacity: float
        _pseudo_max_active: bool
        _max_anim: QAbstractAnimation
        _max_overlay: QWidget
        _max_overlay_fade_step: int
        _max_overlay_fade_timer: QTimer
        _min_anim: QAbstractAnimation
        _min_overlay: QWidget
        _restore_anim: QAbstractAnimation

        # ─ 閉じる ─
        _close_anim: QAbstractAnimation
        _close_anim_started: bool
        _close_confirmed: bool

        # ─ パネル (右ペイン折りたたみ) ─
        _panel_anim: QAbstractAnimation
        _panel_anim_running: bool
        _panel_anim_target_rw: int
        _panel_anim_target_visible: bool
        _panel_opacity_effect: QGraphicsOpacityEffect
        _right_panel_collapsed: bool
        _last_panel_width: int

        # ─ コメントオーバーレイのアニメ ─
        _comment_anim: QAbstractAnimation
        _comment_anim_effect: QGraphicsOpacityEffect
        _comment_anim_kind: str   # "open" | "close"
        _comment_anim_running: bool

        # ─ テーマ切替フェード ─
        _theme_fade_anim: QPropertyAnimation
        _theme_fade_effect: QGraphicsOpacityEffect
        _theme_fade_overlay: QWidget
        _theme_fade_running: bool

        # ─ 起動アニメ ─
        _startup_anim: QPropertyAnimation
        _startup_anim_done: bool
        _startup_anim_running: bool
        _startup_scale_anim: QPropertyAnimation
        _root_opacity_effect: Optional[QGraphicsOpacityEffect]

        # ─ ウェルカム↔盤面遷移 ─
        _welcome_fade_effects: list
        _welcome_to_board_anim: QAbstractAnimation

        # ─ ホイール / スクロール ─
        _wheel_last_refresh_t: float
        _wheel_refresh_timer: QTimer
        _scroll_anims: dict

        # ═════════════════════════════════════════════════════════════
        # QMainWindow / QWidget 由来のメソッド (Mixin から呼ぶもの)
        # ═════════════════════════════════════════════════════════════
        def update(self) -> None: ...
        def show(self) -> None: ...
        def hide(self) -> None: ...
        def close(self) -> None: ...
        def setWindowOpacity(self, opacity: float) -> None: ...
        def windowOpacity(self) -> float: ...
        def isMaximized(self) -> bool: ...
        def isMinimized(self) -> bool: ...
        def isVisible(self) -> bool: ...
        def setGeometry(self, *args: Any) -> None: ...
        def geometry(self) -> QRect: ...
        def width(self) -> int: ...
        def height(self) -> int: ...
        def x(self) -> int: ...
        def y(self) -> int: ...
        def pos(self) -> QPoint: ...
        def size(self) -> QSize: ...
        def setWindowTitle(self, title: str) -> None: ...
        def windowTitle(self) -> str: ...
        def setFocus(self) -> None: ...
        def repaint(self) -> None: ...
        def setCursor(self, cursor: Any) -> None: ...
        def unsetCursor(self) -> None: ...
        def grabMouse(self) -> None: ...
        def releaseMouse(self) -> None: ...
        def mapToGlobal(self, p: QPoint) -> QPoint: ...
        def mapFromGlobal(self, p: QPoint) -> QPoint: ...
        # QMainWindow 固有
        def menuBar(self) -> Any: ...
        def statusBar(self) -> QStatusBar: ...
        def setCentralWidget(self, w: QWidget) -> None: ...
        def centralWidget(self) -> QWidget: ...
        def setMenuBar(self, mb: Any) -> None: ...
        # QObject
        def setProperty(self, name: str, value: Any) -> bool: ...
        def property(self, name: str) -> Any: ...


# 実行時には Protocol を公開しない (TYPE_CHECKING のみで有効)。
__all__: list = ["MainWindowProto"] if TYPE_CHECKING else []
