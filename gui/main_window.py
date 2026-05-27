"""
囲碁AI解析ソフト v2 — KataGo Reviewer スタイル
License: MIT
"""
from __future__ import annotations
import sys, logging, tempfile, time
from pathlib import Path
from typing import Optional

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QFileDialog, QSlider, QTextEdit,
        QStatusBar, QFrame, QSizePolicy, QScrollArea, QComboBox,
        QDialog, QStyledItemDelegate, QStyle,
    )
    from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF, QSize, QRect, QEvent, QObject, QStandardPaths, QThread, QByteArray
    from PyQt6.QtGui import (
        QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QPainterPathStroker,
        QAction, QKeySequence, QLinearGradient, QRadialGradient, QImage,
        QFontDatabase, QPixmap, QIcon,
    )
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:
    print("pip install PyQt6 pyqtgraph"); sys.exit(1)

try:
    import pyqtgraph as pg
except ImportError:
    pg = None

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.sgf_parser import (
    SGFGame, SGFNode, load_sgf, save_sgf, parse_sgf,
    sgf_coord_to_pos, pos_to_sgf_coord, sgf_coord_to_human,
)
from core.katago_engine import KataGoEngine, AnalysisResult
from core.game_state import GameState
from core.analyzer import MoveAnalysis

from gui.theme import (
    Theme, T, _theme,
    EVAL_COLORS, LIGHT_BLUNDER_COLORS, COLS,
    SP_XS, SP_SM, SP_MD, SP_LG, SP_XL,
    R_XS, R_SM, R_MD, R_LG, R_PILL,
    PAD_CARD, PAD_TIGHT, PAD_NAV, PAD_ICON,
    SPACING_ROW,
)
from gui.fonts import (
    F, Fmono,
    Font_XS, Font_SM, Font_MD, Font_LG, Font_XL, Font_XXL,
    FontMono_XS, FontMono_SM, FontMono_MD, FontMono_LG, FontMono_XL, FontMono_XXL,
)
from gui.icons import (
    _rounded_check_svg, _get_check_mark_path,
    _rank_check_svg_bold, _get_rank_check_mark_path,
    _chevron_down_svg, _get_chevron_down_path,
    menu_qss, rank_list_qss, statusbar_qss,
    icon_button_qss, install_icon_hover_color_swap,
    make_icon,
)
from gui.infra import (
    _profiler, _profile, _profile_method,
    set_player_rank, get_current_thresholds,
    BlunderInfo, eval_badge_tuple,
    SoundPlayer, TranslucentWidget,
)
from gui.widgets.common import (
    _RankItemDelegate,
    SLIDER_HEIGHT, SLIDER_HANDLE,
    FlatSlider,
    _ScrollFadeOverlay,
    _AlwaysAcceptWheelTextEdit,
)
from gui.widgets.board import BoardWidget, BoardContainer
from gui.widgets.graph import ScoreLeadAxis, IntegerBottomAxis, _GraphLabelOverlay, WinRateGraph
from gui.widgets.titlebar import _CustomTitleBar
from gui.widgets.navbar import _HelpPopover, ToggleSwitch, ToggleBar, NavBar
from gui.widgets.welcome import WelcomePane, _WelcomeCard, _NewGameCard
from gui.widgets.panels import (
    ScoreBoard, _make_card, InfoPanel, MetricLabel,
    BadgeWidget, _StoneIcon, _CrossFadeLabel, MoveInfoCard,
)
from gui.widgets.branchtree import _TreeEdgeFadeOverlay, BranchTreeWidget
from gui.dialogs import ColorAdjustmentDialog, _WarningIconWidget, _UnsavedChangesDialog, _FirstLaunchRankDialog
from gui.menus import style_qmenu, _SubMenuPositioner, _install_submenu_positioner, _KomiCustomWidget

from gui._mixins.theme_ctrl import ThemeCtrlMixin
from gui._mixins.comments import CommentsMixin
from gui._mixins.file_io import FileIOMixin
from gui._mixins.window_mgmt import WindowMgmtMixin
from gui._mixins.engine_ctrl import EngineCtrlMixin

logger = logging.getLogger(__name__)


# 音量ON: スピーカー + 音波2本
_SVG_VOLUME_ON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<polygon points="2,5.5 5.5,5.5 9,2.5 9,13.5 5.5,10.5 2,10.5" fill="{{color}}"/>'
    '<path d="M10.5 5.5 Q12.5 8 10.5 10.5" stroke="{{color}}" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
    '<path d="M12 3.5 Q15.5 8 12 12.5" stroke="{{color}}" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
    '</svg>'
)
# 音量OFF: スピーカー + ×
_SVG_VOLUME_OFF = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<polygon points="2,5.5 5.5,5.5 9,2.5 9,13.5 5.5,10.5 2,10.5" fill="{{color}}"/>'
    '<line x1="11" y1="5.5" x2="14.5" y2="10.5" stroke="{{color}}" stroke-width="1.5" stroke-linecap="round"/>'
    '<line x1="14.5" y1="5.5" x2="11" y2="10.5" stroke="{{color}}" stroke-width="1.5" stroke-linecap="round"/>'
    '</svg>'
)










# ── Analysis worker ─────────────────────────────────────────────────────────





class MainWindow(EngineCtrlMixin, WindowMgmtMixin, FileIOMixin, CommentsMixin, ThemeCtrlMixin, QMainWindow):
    # ポンダリング結果をメインスレッドで受け取るためのシグナル
    _ponder_result_signal   = pyqtSignal(object, object)  # (AnalysisResult, SGFNode)

    # ── KataGo モデル定義 ──────────────────────────────────────
    # モデルは katago/models/ 直下の .bin.gz ファイルとして管理される。
    # アプリ起動時に直下の .bin.gz をスキャンし、ちょうど1個ある場合のみ
    # 起動を許可する。0個または複数個ある場合は起動前にエラーで終了する
    # （main() 側で _check_models_or_exit() がチェックする）。
    KATAGO_DIR = str(Path(__file__).parent.parent / "katago")

    # ── 解析ルール / コミ設定 ────────────────────────────────────────
    # SGF の KM/RU は無視し、ソフト側で管理した値だけを KataGo に渡す。
    # 嘘 SGF（野狐の KM[0] 等）の影響を受けないようにするための設計。
    # メニュー「ルール」直下に並ぶルールセット定義 (key, label)
    RULES: list[tuple[str, str]] = [
        ("japanese",     "日本"),
        ("chinese",      "中国"),
        ("korean",       "韓国"),
        ("aga",          "AGA"),
        ("tromp-taylor", "Tromp-Taylor"),
        ("new-zealand",  "ニュージーランド"),
    ]
    DEFAULT_RULES = "japanese"
    # 各ルールの標準コミ値。ルール変更時にコミを自動リセットするのに使う。
    RULE_DEFAULT_KOMI: dict[str, float] = {
        "japanese":     6.5,
        "chinese":      7.5,
        "korean":       6.5,
        "aga":          7.5,
        "tromp-taylor": 7.5,
        "new-zealand":  7.0,
    }
    # コミプリセット（数値そのもの、メニュー表示は f"{v}" 形式）
    KOMI_PRESETS: list[float] = [0.5, 6.5, 7.5]
    DEFAULT_KOMI = 6.5

    # 棋力選択肢: (表示文字列, ランク値)。負=級、正=段。
    # メニュー「棋力」直下に並ぶ選択肢として使用。
    RANK_OPTIONS: list[tuple[str, int]] = [
        ("15級", -15), ("14級", -14), ("13級", -13), ("12級", -12), ("11級", -11),
        ("10級", -10), ("9級",   -9), ("8級",   -8), ("7級",   -7), ("6級",   -6),
        ("5級",   -5), ("4級",   -4), ("3級",   -3), ("2級",   -2), ("1級",   -1),
        ("初段",   1), ("二段",   2), ("三段",   3), ("四段",   4), ("五段",   5),
        ("六段",   6), ("七段",   7), ("八段",   8),
    ]
    DEFAULT_RANK = -5  # 5級

    def __init__(self, engine: Optional["KataGoEngine"] = None):
        """
        Args:
            engine: 既に start() 済みの KataGoEngine インスタンス。
                    main() でスプラッシュ表示中に並行起動されたものを受け取る。
                    None の場合は従来どおり __init__ 内で生成・起動する
                    (後方互換のため)。
        """
        super().__init__()
        # frameless化: OS標準のタイトルバー(枠+ボタン)を消し、自前の
        # _CustomTitleBar に置き換える。WindowSystemContextHelp は維持。
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # 起動時アニメ(フェードイン)のため、最初は完全透明に。
        # showEvent で _animated_show_on_startup() がフェードイン制御する。
        self.setWindowOpacity(0.0)
        # ── テーマ初期化（QSettings から復元） ──────────────────────────
        from PyQt6.QtCore import QSettings
        _saved_theme = QSettings("Kizuki", "Kizuki").value("theme", "dark", type=str)
        # 旧バージョンで "default" として保存されていた値や未知の値は
        # ダークに正規化する(_apply の else 句は LIGHT になるため、ここで明示)。
        if _saved_theme not in ("dark", "light"):
            _saved_theme = "dark"
        _theme.set_mode(_saved_theme)

        self.setWindowTitle("囲碁AI解析")
        # ウィンドウ最小サイズ: パネル開閉に応じて _apply_min_window_size で
        # 動的に切り替える。ここでは開いた状態のデフォルトを適用しておき、
        # 後段の起動時パネル状態判定で閉じている場合は再適用する。
        self.setMinimumSize(self._MIN_WIN_OPEN_W, self._MIN_WIN_OPEN_H)
        # ── 起動時ウィンドウサイズの決定 ─────────────────────────────────
        # 碁盤エリアの周りに余分な余白が生じないよう、画面サイズから逆算する。
        # ・碁盤を正方形にする(board_w == board_h)
        # ・画面の約85%以下に収める
        # 構成要素の高さ/幅:
        #   ウィンドウ高さ = タイトルバー(32) + 碁盤高さ + ナビバー領域(32+8=40)
        #   ウィンドウ幅   = 碁盤幅 + 右パネル幅(_FP_MIN_W) + 横マージン×2
        # → board = win_h - (32 + 40) = win_h - 72
        # → board = win_w - _FP_MIN_W - (_FP_MARGIN_X * 2)
        # 両者を等しくして board を最大化、win_w/win_h が画面の85%以下になる範囲で決定。
        screen = QApplication.primaryScreen().availableGeometry()
        scr_w, scr_h = screen.width(), screen.height()
        # 画面の85%を上限とする
        max_w = int(scr_w * 0.85)
        max_h = int(scr_h * 0.85)
        # 縦横方向の固定オーバーヘッド
        H_OVERHEAD = _CustomTitleBar.HEIGHT + NavBar.NB_HEIGHT + NavBar.NB_MARGIN  # 36+36+8=80
        W_OVERHEAD = self._FP_MIN_W + self._FP_MARGIN_X * 2  # 右パネル + 横マージン
        # board_size を、画面に収まる最大値で決定
        # ・縦から決まる上限: max_h - H_OVERHEAD
        # ・横から決まる上限: max_w - W_OVERHEAD
        board_size = min(max_h - H_OVERHEAD, max_w - W_OVERHEAD)
        # 最小値の保証(setMinimumSize 900×650 以上 → 碁盤 480px 以上を保証)
        board_size = max(board_size, 480)
        # 後で起動時パネル閉じ処理で参照するため保持
        self._startup_board_size = board_size
        # ウィンドウサイズ確定
        _target_w = board_size + W_OVERHEAD
        _target_h = board_size + H_OVERHEAD
        self.resize(_target_w, _target_h)
        self.move(screen.center() - self.rect().center())
        self.setStyleSheet(
            f"QMainWindow{{background:{T().BG.name()};}}"
            + menu_qss()
            + statusbar_qss()
        )

        self._game: Optional[SGFGame] = None
        self._node_analyses: dict[int, MoveAnalysis] = {}  # id(SGFNode) -> MoveAnalysis
        self._current_idx = 0
        self._game_state: Optional[GameState] = None  # 着手・分岐管理
        # 保存忘れ防止のための変更追跡:
        # _is_dirty: 棋譜の内容に未保存の変更があるか
        #   True にする操作: 盤面クリックで石を打つ / ノード削除 / コメント編集
        #   False にするタイミング: 保存成功時 / 棋譜差し替え時(新規作成・SGF 読込・貼付)
        # _current_sgf_path: 現在の棋譜の保存先パス。
        #   None = 未保存 (新規作成や貼付の直後)、str = 読込済み or 一度保存済み。
        #   保存時に既存パスがあれば上書き、無ければ QFileDialog を出す。
        self._is_dirty: bool = False
        self._current_sgf_path: Optional[str] = None
        # ポンダリング用: 現在解析中のノード
        self._pondering_node: Optional[object] = None
        # ── ポンダリング中間結果のスロットリング ──
        # _on_ponder_result は reportDuringSearchEvery=0.1 秒ごとに呼ばれるが、
        # その中で _update_graph() と BranchTreeWidget.update_tree() は手数 N に
        # 比例するコストがかかる(O(N) のレイアウトと再描画)。後半の手数では
        # これが重くなるため、中間結果ではこれら2つの処理を 300ms に1回まで
        # 間引く。最終結果(is_during_search=False)は常に走らせるので、
        # 解析完了時の表示には影響しない。盤面・候補手・MoveInfoCard などの
        # 局所更新は毎回行う(これらは軽く、リアルタイム性が重要)。
        self._ponder_heavy_throttle_ms: int = 300
        self._ponder_heavy_last_t: float = 0.0
        # ── ポンダリング中間結果の全体スロットリング(段階3) ──
        # _on_ponder_result の中身全体(勝率バー・MoveInfoCard・盤面再描画・
        # ownership 反映・親辿りループなど)も 0.1 秒ごとに走るとUIが重い。
        # 特に勝率バー(_set_winrate_target)と MetricLabel の数値カードは
        # set_value() のたびに 300ms の QVariantAnimation を再起動するため、
        # 0.1 秒ごとに呼ばれるとアニメが永遠に終わらず常にアニメ中=60fps で
        # update() が発火し続ける状態になる。
        # そこで中間結果は _ponder_full_throttle_ms に1回までに間引く。
        # 最終結果は必ず通す(早期 return しない)。
        # 値は 300ms (= 約 3.3 回/秒)。Lizzie 等の同種ソフトと同等の更新頻度で、
        # 視覚的に十分滑らかに見え、かつ解析中の CPU 負荷を約半分に削減できる。
        self._ponder_full_throttle_ms: int = 300
        self._ponder_full_last_t: float = 0.0
        # ── ポンダリング結果の値変化シグネチャキャッシュ(段階4) ──
        # _on_ponder_result の中で「上位8候補手・勝率・目差」の実質的な値が
        # 前回と変わっていなければ重い再描画(set_position / update_card /
        # update_graph / update_tree)をスキップする。シグネチャは粗い粒度で
        # (visits は 500 単位、勝率は 1% 単位、目差は 0.5 目単位)、実質変化
        # だけを拾う。同一ノード内でのみ有効で、ノード切替時はクリアする。
        self._last_ponder_node_id = None
        self._last_ponder_sig_board = None      # 盤面候補手リング+next_moves+blunder
        self._last_ponder_sig_analysis = None   # 解析カード+グラフラベル
        self._last_ponder_sig_heavy = None      # 目差グラフ+分岐ツリー
        # ── _update_graph 用の構造キャッシュ ──
        # path_to_root() / main_line() は手数 N のリスト走査で、ノード移動や
        # 棋譜編集が起きない限り結果は不変。ポンダリング中はノード構造が
        # 変わらないので、これらの結果を構造キャッシュとして再利用する。
        # _invalidate_graph_struct_cache() で破棄する(ノード移動・追加・削除時)。
        # キャッシュ内容: ((cur_node, root) のキー, move_nodes_in_path,
        # main_move_nodes, on_main_line, graph_total) のタプル。
        self._graph_struct_cache: Optional[tuple] = None
        # 解析・形勢の ON/OFF を QSettings から復元（ToggleBar と同じキー）。
        # デフォルトは 解析=ON / 形勢=OFF（初回起動時の従来挙動）。
        from PyQt6.QtCore import QSettings as _QS
        _qs_init = _QS("Kizuki", "Kizuki")
        self._ai_enabled = _qs_init.value("analysis_enabled", True, type=bool)
        # 形勢判断 ON/OFF。AI 解析が OFF でも、形勢判断 ON なら ownership 取得用に
        # ポンダリングだけ走らせる（UI 情報は更新しない）。
        self._ownership_enabled = _qs_init.value("ownership_enabled", False, type=bool)
        # 手順番号オーバーレイの起点（手数のインデックス: ルート=0、1手目=1、…）
        # None なら手順番号は表示しない。棋譜を切替えるたびにリセットされる。
        # 起点ノード自身の次の手から 1, 2, 3, ... と番号を振る。
        self._move_number_anchor: Optional[int] = None
        # 手順番号のマスターON/OFF（メニューで制御）
        self._move_numbers_enabled: bool = False
        # サウンド
        self._sound = SoundPlayer()
        # 音量を QSettings から復元（デフォルト: 0.6 = 60%）
        _saved_vol = _qs_init.value("sound_volume", 0.6, type=float)
        try:
            self._sound.volume = max(0.0, min(1.0, float(_saved_vol)))
        except (TypeError, ValueError):
            self._sound.volume = 0.6

        self._engine: Optional[KataGoEngine] = None
        # 起動時のモデル: katago/models/ 直下の .bin.gz をスキャンする。
        # main() 側で _check_models_or_exit() による事前チェック済みなので、
        # ここに来る時点でちょうど1個存在することが保証されている。
        if engine is not None:
            # 既起動エンジンを受け取るパス(main() スプラッシュ並行起動経由)
            # _current_model_file は engine.model の絶対パスからファイル名を抽出
            from pathlib import Path
            self._engine = engine
            self._current_model_file = Path(engine.model).name
        else:
            # 後方互換: 従来どおりここで生成・起動(同期ブロック)
            available = self._scan_models()
            self._current_model_file = available[0]
            self._engine = self._create_engine(self._current_model_file)
            self._engine.start()

        # ── ルール/コミ設定の復元 ────────────────────────────────
        # SGF の KM/RU は信頼しない方針なので、ソフト側で管理した設定値を
        # QSettings から復元してエンジンに伝える。
        from PyQt6.QtCore import QSettings
        _qs = QSettings("Kizuki", "Kizuki")
        saved_rules = _qs.value("katago_rules", self.DEFAULT_RULES, type=str)
        if saved_rules not in [k for k, _ in self.RULES]:
            saved_rules = self.DEFAULT_RULES
        try:
            saved_komi = float(_qs.value("katago_komi", self.DEFAULT_KOMI))
        except (TypeError, ValueError):
            saved_komi = self.DEFAULT_KOMI
        self._current_rules = saved_rules
        self._current_komi = saved_komi
        # 起動直後は置き石なし（SGF を読み込んだ時点で _build_states が再設定する）
        try:
            self._engine.set_game_info(saved_komi, saved_rules, [])
        except Exception:
            pass

        # シグナル接続
        self._ponder_result_signal.connect(self._on_ponder_result)

        # ── スプラッシュスピナーのアニメ継続のための processEvents ──
        # __init__ 内で _build_ui / _build_menu / _load_demo はそれぞれ
        # 数百ms オーダーでメインスレッドをブロックしうる重い処理。
        # その間スプラッシュ画面のスピナーアニメがフリーズして見えるため、
        # 節目ごとに QApplication.processEvents() を呼んでイベントループを
        # 回し、スピナーの再描画を進める。スプラッシュ画面が表示されていない
        # 状況(後方互換 engine=None パス等)でも害はない。
        _qa = QApplication.instance()
        _proc = _qa.processEvents if _qa is not None else (lambda: None)

        _proc()
        self._build_ui()
        _proc()
        self._build_menu()
        _proc()

        # ── 右パネル開閉状態を QSettings から復元 ──────────────────
        from PyQt6.QtCore import QSettings
        _saved_panel_collapsed = QSettings("Kizuki", "Kizuki").value(
            "right_panel_collapsed", False, type=bool)
        # アニメ中フラグ初期化(コンストラクタ後の_build_uiでは未定義のため)
        self._panel_anim_running = False
        self._right_panel_collapsed = _saved_panel_collapsed
        # 復元用に保存しておくパネル幅(初期値: _FP_MIN_W + マージン)
        self._last_panel_width = self._FP_MIN_W + self._FP_MARGIN_X * 2

        if _saved_panel_collapsed:
            # 起動時に閉じた状態 → ウィンドウ幅から右パネル分を引く + パネル非表示
            if hasattr(self, "_right_col"):
                self._right_col.setVisible(False)
            cur = self.geometry()
            # ウィンドウ幅 = 碁盤の最大可能サイズ + 横マージン
            # こうすることで:
            #   ・閉じた状態:碁盤がウィンドウいっぱいに表示(余白最小)
            #   ・開いた状態に切替時:碁盤サイズを維持したまま右カラム(_FP_MIN_W)分
            #     だけウィンドウが広がる(_last_panel_width = _FP_MIN_W + _FP_MARGIN_X*2)
            #   結果として開いた状態のウィンドウ幅 = board_size + _FP_MIN_W + _FP_MARGIN_X*2
            #   = _target_w(画面 85% ベースで計算した開いた状態の自然なサイズ)に戻る。
            # 高さは現状の計算(画面 85% ベース、_target_h)を維持する。
            # 「閉じた状態の最小幅」(580) より小さくならないよう下限を確保。
            closed_min_w = (self._MIN_WIN_OPEN_W
                            - self._FP_MIN_W - self._FP_MARGIN_X * 2)
            board_size = getattr(self, "_startup_board_size", 480)
            new_w = max(closed_min_w, board_size + self._FP_MARGIN_X * 2)
            # 重要: resize の前に最小ウィンドウサイズを閉じた状態(=580 幅)に下げる。
            # L9430 で setMinimumSize(_MIN_WIN_OPEN_W=900, ...) が適用済みのため、
            # この順序にしないと resize(new_w<900) が Qt に阻止されて 900 に強制される。
            self._apply_min_window_size(panel_open=False)
            self.resize(new_w, cur.height())
            self.move(self.screen().geometry().center() - self.rect().center())
        # トグルボタンのアイコンを現在状態に同期
        if hasattr(self, "_titlebar"):
            self._titlebar.update_panel_toggle_icon(is_open=not _saved_panel_collapsed)
            # 起動直後はウェルカム画面なのでトグルボタンを非アクティブにする
            # (碁盤画面に遷移した際に _set_welcome_mode(False) で active=True に
            # 戻る)。setVisible は使わずアイコン透明化で代替し、解析画面遷移時の
            # ウィンドウ操作ボタン群の動きを抑止する。
            if hasattr(self._titlebar, "set_panel_toggle_active"):
                self._titlebar.set_panel_toggle_active(False)
        # frameless化に伴い、Qt 標準の QMenuBar は非表示にする。
        # メニューの QAction は自動付与されたショートカットキー経由でも動作するので、
        # 既存のキーボード操作(Ctrl+N、Ctrl+O 等)はこのまま生きる。
        # Phase 2 で _CustomTitleBar のメニューボタンに各 QMenu を接続予定。
        if self.menuBar():
            self.menuBar().hide()
        _proc()
        self._load_demo()
        _proc()

        # キーボード操作をメインウィンドウで確実に受け取るための設定
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        # frameless ウィンドウ: マウス追跡を有効化しないとボタン押下なしの
        # MouseMove イベントが届かない。リサイズホバー検出に必要。
        self.setMouseTracking(True)
        # 全子ウィジェットにeventFilterをインストール（フォーカスがどこにあってもキーを捕捉）
        # 同時に setMouseTracking(True) で MouseMove イベントを取得可能にする
        for w in self.findChildren(QWidget):
            if w is not self._comment:
                w.installEventFilter(self)
                w.setMouseTracking(True)
        _proc()

        # ── 初回起動時の棋力選択ダイアログ ────────────────────────
        # QSettings に "player_rank" キーが存在しなければ初回起動と判定し、
        # メイン画面のフェードイン完了後 (約 300ms 後) にモーダルダイアログを
        # 開く。スプラッシュ閉鎖 (150ms) と被らないよう少し遅らせる。
        from PyQt6.QtCore import QSettings as _QS_init, QTimer as _QT_init
        if not _QS_init("Kizuki", "Kizuki").contains("player_rank"):
            _QT_init.singleShot(300, self._show_first_launch_rank_dialog)

    # ── 全画面 D&D ハンドラ ─────────────────────────────────────────
    # SGF 棋譜ファイルをウィンドウ全体のどこにドロップしても開けるようにする。
    # オーバーレイはタイトルバーを除外した root 領域全体を覆い、中央に
    # 「棋譜を開く」カードを表示する。
    _SUPPORTED_KIFU_EXTS = (".sgf",)

    def _build_ui(self):
        # ── 2層構造の outer ─────────────────────────────────
        # outer = QVBoxLayout に [_titlebar, root] を縦並び。
        # root は従来通り絶対配置の親(全子ウィジェットは _place_panels で配置)。
        outer = QWidget()
        outer.setStyleSheet(f"background:{T().BG.name()};")
        outer_l = QVBoxLayout(outer)
        outer_l.setContentsMargins(0, 0, 0, 0)
        outer_l.setSpacing(0)

        # カスタムタイトルバー
        self._titlebar = _CustomTitleBar(outer)
        # 最小化: オーバーレイ + 内部再描画停止 + ジオメトリ&フェードアウトを
        # 並列アニメ → showMinimized() の自前実装(_animated_minimize)に
        # ルーティング。最大化アニメと同じ「中身を隠して縮める」方式で、
        # frameless でもガタつかず Win11 風の吸い込み演出を実現する。
        self._titlebar.minimize_clicked.connect(self._animated_minimize)
        self._titlebar.maximize_toggle_clicked.connect(self._toggle_maximized)
        self._titlebar.close_clicked.connect(self._animated_close)
        self._titlebar.panel_toggle_clicked.connect(self._toggle_right_panel)
        # 音量アイコン初期状態を _sound の現在値に同期
        # (volume_clicked シグナル接続は _build_menu_bar 側で行う)
        try:
            _v0 = float(getattr(self._sound, "_volume", 0.6))
            _muted0 = bool(getattr(self._sound, "_muted", False))
            self._titlebar.update_volume_icon(muted=_muted0 or _v0 <= 0.0)
        except Exception:
            pass
        outer_l.addWidget(self._titlebar)

        # 従来の root(絶対配置の親)
        # setStyleSheet だけでは描画が子ウィジェットを越えて伝わらない場合が
        # あるため、QPalette + autoFillBackground も併用して背景を確実にする。
        root = QWidget()
        root.setStyleSheet(f"background:{T().BG.name()};")
        from PyQt6.QtGui import QPalette as _QPalette
        _root_pal = _QPalette()
        _root_pal.setColor(_QPalette.ColorRole.Window, T().BG)
        _root_pal.setColor(_QPalette.ColorRole.Base, T().BG)
        root.setPalette(_root_pal)
        root.setAutoFillBackground(True)
        outer_l.addWidget(root, stretch=1)

        self.setCentralWidget(outer)
        # _root_widget として保持(従来の self.centralWidget() を使う代わり)
        self._root_widget = root
        # 起動時の中身フェードイン用 QGraphicsOpacityEffect は使用しない。
        # QGraphicsOpacityEffect は描画パイプライン常時負荷でカクつきの
        # 原因となるため、フェードインは windowOpacity だけで全体を
        # フェードする方式に統一した(_animated_show_on_startup を参照)。
        # 後方互換のため属性自体は None で持っておく(他箇所の参照対策)。
        self._root_opacity_effect = None
        # root はレイアウトなし・全子ウィジェットを _place_panels で絶対配置

        # ── 盤面エリア（root の子・絶対配置） ────────────────────────
        # _left_col: 背景色用の全体コンテナ（root の子、絶対配置）
        left = QWidget(root)
        left.setStyleSheet(f"background:{T().BG.name()};")
        self._left_col = left

        self._board = BoardWidget()
        self._board.show_ownership = self._ownership_enabled
        # hints フェード判定で AI解析状態を参照するため同期
        self._board.ai_enabled = self._ai_enabled
        self._board.stone_clicked.connect(self._on_board_click)
        self._board_container = BoardContainer(self._board)

        # ── 画面全体での D&D 受付 ─────────────────────────────
        # SGF 棋譜のドラッグ&ドロップは、BoardContainer のような部分領域ではなく
        # MainWindow 全体で受け付ける(本クラス内の dragEnterEvent 等で実装)。
        # オーバーレイは _root_widget の子として全画面表示するが、
        # 構築タイミングは _build_ui の最後で行う(_drop_overlay_card 経由)。

        # _left_stack: root の直接の子として絶対配置（レイアウト管理は _place_panels）
        from PyQt6.QtWidgets import QStackedWidget
        self._left_stack = QStackedWidget(root)
        self._left_stack.addWidget(self._board_container)  # index 0: 碁盤

        self._welcome_pane = WelcomePane()
        self._welcome_pane.open_sgf_requested.connect(self._open_sgf)
        self._welcome_pane.new_game_requested.connect(self._new_game)
        self._welcome_pane.new_game_with_size_requested.connect(self._new_game)
        self._welcome_pane.sgf_drop_requested.connect(self._open_sgf_path)
        self._welcome_pane.paste_requested.connect(self._paste_sgf)
        self._left_stack.addWidget(self._welcome_pane)     # index 1: ウェルカム
        self._left_stack.setCurrentIndex(1)  # 起動時はウェルカム画面

        # ナビバーは root（centralWidget）の子としてフローティング配置
        self._navbar = NavBar(root)
        self._navbar.slider.setMinimum(0)
        self._navbar.slider.valueChanged.connect(self._on_slider_value_changed)
        self._navbar.slider.sliderPressed.connect(self._on_slider_pressed)
        self._navbar.slider.sliderMoved.connect(self._on_slider_drag)
        self._navbar.slider.sliderReleased.connect(self._on_slider_released)
        self._navbar.btn_prev.clicked.connect(self._prev)
        self._navbar.btn_next.clicked.connect(self._next)
        self._navbar.btn_comment.clicked.connect(self._toggle_comment_overlay)

        # left の配置は _place_panels で管理

        # ── 右カラム: 情報パネル ───────────────────────────────────────
        right = QWidget(root)
        right.setObjectName("floating_panel")
        right.setStyleSheet(
            f"QWidget#floating_panel {{"
            f"  background:{T().PANEL.name()};"
            f"  border-radius:16px;"
            f"}}"
        )
        # QSS の background は角丸 (border-radius:16) の内側しか塗らないため、
        # 角丸の外 (角の三角形領域) はリサイズ時に古い色が残ることがある。
        # autoFillBackground を True にして親と同じ BG 色を矩形塗りしておく。
        from PyQt6.QtGui import QPalette as _QPalRC
        _rc_pal = _QPalRC()
        _rc_pal.setColor(_QPalRC.ColorRole.Window, T().BG)
        _rc_pal.setColor(_QPalRC.ColorRole.Base, T().BG)
        right.setPalette(_rc_pal)
        right.setAutoFillBackground(True)
        self._right_col = right
        rvl = QVBoxLayout(right)
        rvl.setContentsMargins(0, 0, 0, 0)
        rvl.setSpacing(0)

        # ── カードエリア（スクロール可能） ──
        self._cards_scroll = QScrollArea()
        cards_scroll = self._cards_scroll
        cards_scroll.setWidgetResizable(True)
        # 右パネルはスクロールが発生しない仕様のため、スクロールバーを常に非表示
        cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards_scroll.setStyleSheet(
            f"QScrollArea {{ border:none; background:{T().BG.name()}; }}"
            f"QScrollArea > QWidget > QWidget {{ background:{T().BG.name()}; }}"
        )
        # viewport 背景を QPalette で確実に設定（新規パレットで既存色の混入を防ぐ）
        from PyQt6.QtGui import QPalette as _QPalette
        _vp_palette = _QPalette()
        _vp_palette.setColor(_QPalette.ColorRole.Window, T().BG)
        _vp_palette.setColor(_QPalette.ColorRole.Base, T().BG)
        cards_scroll.viewport().setPalette(_vp_palette)
        cards_scroll.viewport().setAutoFillBackground(True)

        cards_widget = QWidget()
        cards_widget.setStyleSheet(f"background:{T().BG.name()};")
        cards_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        _cw_pal = _QPalette()
        _cw_pal.setColor(_QPalette.ColorRole.Window, T().BG)
        _cw_pal.setColor(_QPalette.ColorRole.Base, T().BG)
        cards_widget.setPalette(_cw_pal)
        cards_widget.setAutoFillBackground(True)
        # Phase 2: 対局モード削除につき QStackedWidget → 単一レイアウト化
        # _right_stack は後方互換のため残す（呼び出し側で setCurrentIndex(0) が呼ばれる）
        cards_outer = QVBoxLayout(cards_widget)
        cards_outer.setContentsMargins(0, 0, 0, 0)
        cards_outer.setSpacing(0)

        # ── 解析パネル ──
        analyze_panel = QWidget()
        analyze_panel.setStyleSheet("background:transparent;")
        # 子ウィジェットの sizeHint が大きくても横に膨らまないよう抑制
        analyze_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cards_vl = QVBoxLayout(analyze_panel)
        # パネル内側padding: 左0 / 右16 / 上8 / 下4
        # (カード自体の padding は PAD_CARD=12px のまま)
        cards_vl.setContentsMargins(0, SP_SM, SP_LG, SP_XS)
        cards_vl.setSpacing(SPACING_ROW)
        cards_outer.addWidget(analyze_panel)

        # ① スコアボード + グラフ（InfoPanel内部でカード化済み）
        self._info = InfoPanel()
        # 目差グラフのクリック/ドラッグで手数ジャンプ
        _analyze_graph = self._info.get_graph()
        _analyze_graph.move_dragged.connect(self._on_graph_dragged)
        _analyze_graph.move_released.connect(self._on_graph_released)
        cards_vl.addWidget(self._info, stretch=5)

        # ② 手の情報カード（分岐ツリーを内包）
        self._move_card = MoveInfoCard()
        self._branch_tree = BranchTreeWidget()
        self._branch_tree.node_clicked.connect(self._on_branch_node_clicked)
        self._branch_tree.node_delete_requested.connect(self._on_delete_branch_node)
        self._branch_tree.move_number_anchor_requested.connect(
            self._on_move_number_anchor_requested)
        self._branch_tree.node_comment_requested.connect(self._on_node_comment_requested)
        self._move_card.set_tree(self._branch_tree)
        self._branch_scroll = self._move_card.tree_scroll()
        cards_vl.addWidget(self._move_card, stretch=5)

        # ⑤ コメントカード → MoveInfoCard のポップアップに移行
        # ⑥ トグルバーカード
        self._toggle_bar = ToggleBar()
        self._toggle_bar.ai_toggled.connect(self._on_ai_toggle)
        self._toggle_bar.ownership_toggled.connect(self._on_ownership_toggle)
        self._toggle_bar.move_numbers_toggled.connect(self._on_move_numbers_toggled)
        cards_vl.addWidget(self._toggle_bar)

        # ToggleBar の保存値で初期状態を同期（メニュー時代と同じデフォルト=OFF）
        from PyQt6.QtCore import QSettings as _QSet
        _saved_mn = _QSet("Kizuki", "Kizuki").value(
            "move_numbers_enabled", False, type=bool)
        if _saved_mn:
            self._move_numbers_enabled = True
            if self._move_number_anchor is None:
                self._move_number_anchor = 0

        cards_scroll.setWidget(cards_widget)
        rvl.addWidget(cards_scroll, stretch=1)

        # 情報パネル（root の子として絶対配置、_place_panels で位置確定）
        self._right_col = right

        # ── コメントオーバーレイ（ナビバー上にフローティング表示） ──
        self._comment_overlay = TranslucentWidget(root, alpha=230)
        self._comment_overlay.setObjectName("comment_overlay")
        # 背景・ボーダー・角丸は TranslucentWidget.paintEvent で描画
        # 子ウィジェット（QTextEdit等）の背景が透けないよう transparent に
        self._comment_overlay.setStyleSheet(
            "QWidget#comment_overlay { background: transparent; border: none; }"
        )
        ov_vl = QVBoxLayout(self._comment_overlay)
        # 上下マージンを 0 にすることで、テキストエディットがオーバーレイ枠の
        # 端ぴったりから始まる。これによりフェードオーバーレイ(テキストエディット
        # 全体に被さる)の上下端がコメント欄の端に一致する。
        # 左右は SP_LG=16 を維持(テキストの左右余白はそのまま)。
        ov_vl.setContentsMargins(SP_LG, 0, SP_LG, 0)
        ov_vl.setSpacing(0)
        # 閉じるボタンをオーバーレイ右上に絶対配置（ヘッダー行なし）
        self._comment_close_btn = QPushButton("✕")
        self._comment_close_btn.setFixedSize(24, 24)
        self._comment_close_btn.setParent(self._comment_overlay)
        self._comment_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # コメントオーバーレイ専用のスタイル(共通の icon_button_qss は使わない):
        # - 文字色は T().TEXT(ダーク=#fff)で、メインテキストと同じ濃さで視認性を担保
        # - ホバー時も背景色は変えない(透過のまま)。コメントオーバーレイ自体が
        #   半透明のパネルなので、ホバー背景を出すと二重に塗られて重く見えるため。
        # apply_theme でテーマ切替時に再生成する必要があるため、生成処理を
        # メソッド化して両方から呼べるようにしてある(_apply_comment_close_btn_qss)。
        self._apply_comment_close_btn_qss()
        self._comment_close_btn.clicked.connect(self._close_comment_overlay)
        # テキスト入力欄
        # 通常の QTextEdit はスクロール限界でホイールが親に伝播し、
        # 背後のグラフ/盤面の手送りを誤発火させるため専用サブクラスを使う。
        self._comment_textedit = _AlwaysAcceptWheelTextEdit()
        self._comment_textedit.setPlaceholderText("コメントを入力...")
        self._comment_textedit.setStyleSheet(
            f"QTextEdit {{ background:transparent; color:{T().TEXT.name()};"
            f" border:none; font-size:14px;"
            f" font-family:'Yu Gothic UI','BIZ UDGothic'; }}"
        )
        self._comment_textedit.setMinimumHeight(80)
        # documentMargin は左右の余白に効くため、既存値(2)に戻す。
        # 上下の余白はテキストエディット側で setViewportMargins で確保している。
        self._comment_textedit.document().setDocumentMargin(2)
        self._comment_textedit.textChanged.connect(self._on_comment_overlay_changed)
        ov_vl.addWidget(self._comment_textedit)

        # スクロール余地を示すフェードオーバーレイ。
        # 親をコメントオーバーレイ自身にすることで、フェードが左右端まで伸びる。
        # スクロール監視対象は内側のテキストエディット。
        self._comment_fade_overlay = _ScrollFadeOverlay(
            self._comment_overlay, textedit=self._comment_textedit
        )
        sb_v = self._comment_textedit.verticalScrollBar()
        sb_v.valueChanged.connect(self._comment_fade_overlay.update_visibility)
        sb_v.rangeChanged.connect(
            lambda *_: self._comment_fade_overlay.update_visibility())
        self._comment_textedit.textChanged.connect(
            self._comment_fade_overlay.update_visibility)

        self._comment_overlay.hide()

        # _comment は後方互換のため _comment_textedit へのエイリアス
        self._comment = self._comment_textedit

        # ステータスバー（非表示）
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.setVisible(False)

        # ── 全画面 D&D オーバーレイ ─────────────────────────────
        # SGF 棋譜のドラッグ&ドロップは MainWindow 全体で受け付ける。
        # ドラッグホバー中は _root_widget(タイトルバー以下の領域)全体を
        # 暗くし、中央にウェルカム画面と同じ「棋譜を開く」カードを表示する。
        # _root_widget の子にすることで、タイトルバーは覆わずウィンドウ
        # 操作(ドラッグ移動・ダブルクリック最大化等)を阻害しない。
        self.setAcceptDrops(True)
        self._drop_overlay = QWidget(self._root_widget)
        # マウスイベントをスルー(D&D 自体は MainWindow のイベントハンドラで処理)
        self._drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._drop_overlay.setStyleSheet("background: rgba(0,0,0,220);")
        # オーバーレイ内にウェルカム画面と共通のカードを配置(文言変更時の
        # 反映漏れを防ぐため _WelcomeCard.OPEN_CARD_PARAMS を共用)
        self._drop_card = _WelcomeCard(**_WelcomeCard.OPEN_CARD_PARAMS)
        self._drop_card.setParent(self._drop_overlay)
        # カード自身が背景色を塗らないようにする(暗オーバーレイをそのまま透過)
        # ライトモード時に Qt のパレット由来でカード矩形が明るく塗られて
        # しまう症状を防ぐため明示的に透過を設定する
        self._drop_card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 暗背景の上では塗りつぶしパネルが見えにくいため抑制(枠と文字のみ)
        self._drop_card.set_disable_hover_panel(True)
        # 暗背景固定のオーバーレイ上では、ライトモード時にも文字・アイコンを
        # 白系で描画する必要があるため、配色をダーク基準に固定する
        self._drop_card.set_force_dark_palette(True)
        # ドラッグホバー中は常にアクティブ表示
        self._drop_card.set_drag_hovered(True)
        self._drop_overlay.hide()

        # ウィンドウ表示後に配置を確定（showEvent でも呼ばれるが念のため）
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._place_panels)

    def _build_menu(self):
        from PyQt6.QtCore import QSettings
        settings = QSettings("Kizuki", "Kizuki")

        mb = self.menuBar()
        fm = mb.addMenu("ファイル")
        # 新規作成: サブメニューで盤面サイズを選択（19路にCtrl+Nを割当て）
        new_menu = fm.addMenu("新規作成")
        style_qmenu(new_menu, leaf=True)
        if hasattr(self, "_titlebar"):
            self._titlebar.attach_submenu_filter(new_menu)
        na19 = QAction("19路盤", self); na19.setShortcut(QKeySequence.StandardKey.New)
        na19.triggered.connect(lambda: self._new_game(19)); new_menu.addAction(na19)
        na13 = QAction("13路盤", self)
        na13.triggered.connect(lambda: self._new_game(13)); new_menu.addAction(na13)
        na9 = QAction("9路盤", self)
        na9.triggered.connect(lambda: self._new_game(9)); new_menu.addAction(na9)
        oa = QAction("開く",self); oa.setShortcut(QKeySequence.StandardKey.Open)
        oa.triggered.connect(self._open_sgf); fm.addAction(oa)
        self._save_act = QAction("保存",self); self._save_act.setShortcut(QKeySequence.StandardKey.Save)
        self._save_act.triggered.connect(self._save_sgf); fm.addAction(self._save_act)
        self._copy_act = QAction("コピー", self)
        self._copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        self._copy_act.triggered.connect(self._copy_sgf); fm.addAction(self._copy_act)
        pa_sgf = QAction("貼り付け", self)
        pa_sgf.setShortcut(QKeySequence.StandardKey.Paste)
        pa_sgf.triggered.connect(self._paste_sgf); fm.addAction(pa_sgf)
        fm.addSeparator()
        self._ss_act = QAction("盤面をスクリーンショット", self)
        self._ss_act.setShortcut(QKeySequence("Ctrl+P"))
        self._ss_act.triggered.connect(self._save_board_screenshot); fm.addAction(self._ss_act)

        vm = mb.addMenu("表示")
        style_qmenu(vm, leaf=True)

        # 前回値を復元（デフォルト: 候補手=True、座標=False）
        show_hints_init  = settings.value("show_hints",  True,  type=bool)
        show_coords_init = settings.value("show_coords", False, type=bool)

        ha = QAction("候補手", self)
        ha.setCheckable(True)
        ha.setChecked(show_hints_init)
        self._board.show_hints = show_hints_init
        def _toggle_hints(v):
            self._board.show_hints = v
            QSettings("Kizuki", "Kizuki").setValue("show_hints", v)
            self._board.update()
        ha.triggered.connect(_toggle_hints)
        vm.addAction(ha)

        show_badges_init = settings.value("show_badges", True, type=bool)
        ba = QAction("評価バッジ", self)
        ba.setCheckable(True)
        ba.setChecked(show_badges_init)
        self._board.show_badges = show_badges_init
        def _toggle_badges(v):
            self._board.show_badges = v
            QSettings("Kizuki", "Kizuki").setValue("show_badges", v)
            self._board.update()
        ba.triggered.connect(_toggle_badges)
        vm.addAction(ba)

        # 最後の手をマーク: 直前の手の石中心に枠線リングを描画
        # (黒石上は白線、白石上は黒線)。デフォルト OFF。
        show_last_mark_init = settings.value(
            "show_last_move_mark", False, type=bool)
        lma = QAction("最後の手をマーク", self)
        lma.setCheckable(True)
        lma.setChecked(show_last_mark_init)
        self._board.show_last_move_mark = show_last_mark_init
        def _toggle_last_move_mark(v):
            self._board.show_last_move_mark = v
            QSettings("Kizuki", "Kizuki").setValue("show_last_move_mark", v)
            self._board.update()
        lma.triggered.connect(_toggle_last_move_mark)
        vm.addAction(lma)

        ca = QAction("座標", self)
        ca.setCheckable(True)
        ca.setChecked(show_coords_init)
        self._board.show_coords = show_coords_init
        def _toggle_coords(v):
            self._board.show_coords = v
            QSettings("Kizuki", "Kizuki").setValue("show_coords", v)
            self._board.update()
        ca.triggered.connect(_toggle_coords)
        vm.addAction(ca)

        # 盤面反転（180度回転、石とそれに紐づく要素のみ反転、座標ラベル等は固定）
        # 仕様: 永続化しない（セッション限り、棋譜切替でも保持）
        flip_act = QAction("盤面を反転", self)
        flip_act.setCheckable(True)
        flip_act.setChecked(False)
        def _toggle_flip(v):
            self._board.flipped = v
            self._board.update()
        flip_act.triggered.connect(_toggle_flip)
        vm.addAction(flip_act)

        # 「手順を表示」のグローバルトグルは撤去（分岐ツリー上のノード右クリックで
        # 「以降の手順を表示」を選ぶ方式に変更したため）。

        # ── 設定 メニュー (棋力 / ルール / コミ / テーマ をサブメニュー化) ──
        # メニューバーをスッキリさせるため、対局設定とアプリ設定を一括して
        # 「設定」配下に集約する。サブメニュー順序は対局設定→アプリ設定:
        #   1. 棋力     (プレイヤー固有: 解析閾値の判定基準)
        #   2. ルール   (対局の前提)
        #   3. コミ     (対局の前提、ルールに連動)
        #   4. テーマ   (アプリ表示)
        setting_menu = mb.addMenu("設定")

        # ── 棋力メニュー（設定 配下、先頭に配置） ──────────────────────────
        # 23 項目 (15級〜八段) と多く、QMenu の標準アクション一覧では
        # 低解像度時に画面外へはみ出る。Qt の組み込みスクロール矢印は
        # WA_TranslucentBackground + FramelessWindowHint 環境で描画されない
        # ため、QWidgetAction + QListWidget でリスト表示してスクロールバー
        # 経由でスクロールさせる(コミメニューの「その他」と同じ哲学)。
        rank_menu = setting_menu.addMenu("あなたの棋力")
        style_qmenu(rank_menu, leaf=True)
        if hasattr(self, "_titlebar"):
            self._titlebar.attach_submenu_filter(rank_menu)

        from PyQt6.QtCore import QSettings as _QS2
        _saved_rank = _QS2("Kizuki", "Kizuki").value(
            "player_rank", self.DEFAULT_RANK, type=int)
        set_player_rank(_saved_rank)

        # 棋力リスト本体: QListWidget を作って QWidgetAction でメニューに
        # 埋め込む。各行は QListWidgetItem で、UserRole にランク値、
        # UserRole+1 に「選択中フラグ」を持たせる。
        # チェックマーク・テキスト描画は _RankItemDelegate が担当する
        # (QStyleOptionViewItem の内部余白に左右されない、px 単位の正確な
        #  レイアウトを実現するため)。
        from PyQt6.QtWidgets import (
            QListWidget, QListWidgetItem, QWidgetAction, QAbstractItemView
        )
        rank_list = QListWidget(rank_menu)
        rank_list.setStyleSheet(rank_list_qss())
        rank_list.setFrameShape(QListWidget.Shape.NoFrame)
        rank_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        rank_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 幅は最長ラベル + チェック領域 + スクロールバー領域を見込んで固定。
        # delegate の TEXT_LEFT(36) + 最長ラベル「九段」(28) + 右余白(8)
        # + スクロールバー(6) + α で 88px。
        # スクロールバーは右端 (margin:0) に表示され、テキストが
        # スクロール表示中に見切れないようにする。
        rank_list.setFixedWidth(88)

        # 独自描画デリゲートをセット。Qt の自動レイアウトをバイパスして
        # チェックマーク・テキスト位置を完全に固定 px で制御する。
        rank_delegate = _RankItemDelegate(rank_list)
        rank_list.setItemDelegate(rank_delegate)
        self._rank_item_delegate = rank_delegate

        for label, val in self.RANK_OPTIONS:
            is_checked = (val == _saved_rank)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, val)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_checked)
            rank_list.addItem(item)

        # クリックで選択 → ランク変更 → メニュー閉じる。
        # itemClicked は左クリックでも触れた瞬間に発火するため、
        # ホバー流れで誤発火しないよう itemActivated を使う。
        # ただし itemActivated はダブルクリック既定なので、
        # singleClick で扱うため activationBehavior を選ぶ。
        # メニューに QWidgetAction として埋め込む(itemPressed ハンドラで
        # 参照するため、先に rank_action を生成しておく)
        rank_action = QWidgetAction(rank_menu)
        rank_action.setDefaultWidget(rank_list)
        rank_menu.addAction(rank_action)

        # クリック確定で選択 → ランク変更 → メニューチェーン閉鎖。
        # itemPressed (マウスダウン) を使うと、押下した瞬間にメニューを
        # 閉じてしまい、release イベントが背後のウィジェットに届いて
        # クリックが貫通する (例: ファイル選択カードが反応してしまう)。
        # itemClicked は press と release の両方が同じ項目上で
        # 発生した時のみ発火するため、Qt 標準の QMenu と同じ「リリース
        # まで確定しない」挙動になり、貫通が起きない。
        # メニュー閉鎖は rank_action.trigger() で Qt 標準フローに乗せる。
        # 通常の QAction は triggered すると Qt がメニューチェーン全体
        # (サブメニュー → 親メニュー → メニューバー) を自動で閉じるが、
        # QWidgetAction.defaultWidget の内部クリックは triggered を発火
        # させない。明示的に trigger() を呼ぶことで、サブ/親/メニューバーが
        # 一括して閉じる挙動を他メニュー (コミ等) と揃える。
        def _on_item_clicked(it: QListWidgetItem):
            v = it.data(Qt.ItemDataRole.UserRole)
            if v is None:
                return
            self._on_rank_action(int(v))
            rank_action.trigger()
        rank_list.itemClicked.connect(_on_item_clicked)

        self._rank_menu = rank_menu
        self._rank_list_widget = rank_list

        # ── 棋力メニューの長大化対応 ─────────────────────────────
        # 表示直前に QListWidget の高さを MainWindow 連動で制限し、
        # 同時に選択中項目を可視位置へスクロールする。詳細は
        # _on_rank_menu_about_to_show 参照。
        # 閉じる際に固定サイズ制約を解除して、次回開く時にゼロから
        # 再計算できるようにする(ウィンドウリサイズ後の初回表示で
        # 古いサイズが残る問題の対策)。
        rank_menu.aboutToShow.connect(self._on_rank_menu_about_to_show)
        rank_menu.aboutToHide.connect(self._on_rank_menu_about_to_hide)

        # ── ルール メニュー ────────────────────────────────────────
        # SGF の RU を信頼せず、ここで指定した値を解析エンジンに渡す。
        # 嘘 SGF（野狐の RU[Japanese] 等）の影響を排除するための仕様。
        # ルール変更時、コミは「コミ」メニュー側で当該ルールの標準値に自動連動する。
        from PyQt6.QtGui import QActionGroup
        rule_menu = setting_menu.addMenu("ルール")
        style_qmenu(rule_menu, leaf=True)
        if hasattr(self, "_titlebar"):
            self._titlebar.attach_submenu_filter(rule_menu)
        rules_group = QActionGroup(self)
        rules_group.setExclusive(True)
        self._action_rules: dict[str, QAction] = {}
        for key, label in self.RULES:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(key == self._current_rules)
            act.triggered.connect(
                lambda _checked, k=key: self._on_rules_changed(k))
            rules_group.addAction(act)
            rule_menu.addAction(act)
            self._action_rules[key] = act

        # ── コミ メニュー（設定 配下） ──────────────────────────
        # メニュー構成:
        #   ・プリセット (KOMI_PRESETS): 排他チェックの選択肢
        #   ・──── (区切り線)
        #   ・「その他: [−] X.X [+]」: インラインで 0.5 刻み調整(_KomiCustomWidget)
        #
        # 「その他」を使うと、現在値はプリセットでは表せないカスタム値になる。
        # その間プリセット側のチェックは全て外れ、カスタムウィジェットの値が
        # 現在値を表す(視覚的にも一意)。「その他」の値を 6.5 など既存プリセット
        # に合わせて操作した場合は、プリセット側のチェックを動的に同期する。
        komi_menu = setting_menu.addMenu("コミ")
        style_qmenu(komi_menu, leaf=True)
        if hasattr(self, "_titlebar"):
            self._titlebar.attach_submenu_filter(komi_menu)
        komi_group = QActionGroup(self)
        komi_group.setExclusive(True)
        self._action_komi: dict[float, QAction] = {}
        for v in self.KOMI_PRESETS:
            act = QAction(f"{v}", self)
            act.setCheckable(True)
            act.setChecked(abs(v - self._current_komi) < 1e-6)
            act.triggered.connect(
                lambda _checked, k=v: self._on_komi_changed(k))
            komi_group.addAction(act)
            komi_menu.addAction(act)
            self._action_komi[v] = act
        # 区切り線
        komi_menu.addSeparator()
        # ── 「その他」: インライン − / 値 / + ウィジェット ───────────
        # ・− / + で値だけ調整(コミは確定しない、メニューは開いたまま)
        # ・ウィジェット領域(ラベル/値ラベル等、ボタン以外)をクリックすると
        #   現在のウィジェット値で確定 → _on_komi_changed → メニュー閉じる
        from PyQt6.QtGui import QAction as _QA
        from PyQt6.QtWidgets import QWidgetAction
        # 「その他」ウィジェットの初期表示値:
        # ・現在値がプリセット外(= 起動時に「その他」経由扱い): 現在値を表示
        # ・現在値がプリセット内: QSettings に保存された前回の調整値を復元
        #   (初回起動 = キーなし → デフォルト 0.0)。これにより、ユーザーが
        #   「その他」で調整中だった値は次回起動時にも保持される。
        from PyQt6.QtCore import QSettings as _QS
        _qs_komi = _QS("Kizuki", "Kizuki")
        is_in_preset_init = any(abs(v - self._current_komi) < 1e-6 for v in self.KOMI_PRESETS)
        if is_in_preset_init:
            try:
                _other_init = float(_qs_komi.value("katago_komi_other_value", 0.0))
            except (TypeError, ValueError):
                _other_init = 0.0
        else:
            _other_init = self._current_komi
        custom_w = _KomiCustomWidget(value=_other_init)
        custom_w.confirmRequested.connect(self._on_komi_custom_confirmed)
        # 「その他」選択中の ± ボタンによる数値変更はリアルタイムでコミ反映
        # (メニューは開いたまま、確定済みのまま値だけ更新)
        custom_w.valueChangedRealtime.connect(self._on_komi_realtime_change)
        # ± ボタンによる「その他」値の調整は QSettings に永続化
        # (確定有無に関わらず、次回起動時にも調整値を復元するため)
        custom_w.valueAdjusted.connect(self._on_komi_other_value_adjusted)
        custom_action = QWidgetAction(self)
        custom_action.setDefaultWidget(custom_w)
        komi_menu.addAction(custom_action)
        self._komi_menu = komi_menu  # テーマ切替などで参照するため保持
        self._komi_group = komi_group
        self._komi_custom_widget = custom_w  # 値同期のため保持
        self._komi_custom_action = custom_action  # メニュー閉じる時に使う
        # 「その他」経由で確定された値かどうか(チェックマーク表示制御用)
        # プリセットボタン経由なら False、その他クリック経由なら True にする。
        # 起動時の現在値がプリセット外なら、初期状態で「その他」経由扱いにする
        # (QSettings から復元した 8.5 などのカスタム値はチェックを「その他」側に)。
        self._komi_via_other = not is_in_preset_init
        # 初期同期: 起動時の現在値からチェック表示を反映
        self._sync_komi_menu_check(self._current_komi)

        # ── テーマメニュー（設定 配下: ライト / ダーク）──
        tm = setting_menu.addMenu("テーマ")
        style_qmenu(tm, leaf=True)
        if hasattr(self, "_titlebar"):
            self._titlebar.attach_submenu_filter(tm)
        light_action   = QAction("ライト", self)
        dark_action    = QAction("ダーク", self)
        light_action.setCheckable(True)
        dark_action.setCheckable(True)
        # 現在のテーマに合わせてチェック状態を初期化
        cur_mode = T().mode
        light_action.setChecked(cur_mode == "light")
        dark_action.setChecked(cur_mode == "dark")
        # 相互排他グループ
        from PyQt6.QtGui import QActionGroup
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        theme_group.addAction(light_action)
        theme_group.addAction(dark_action)
        def _set_light(checked):
            if checked:
                self.apply_theme("light")
        def _set_dark(checked):
            if checked:
                self.apply_theme("dark")
        light_action.triggered.connect(_set_light)
        dark_action.triggered.connect(_set_dark)
        tm.addAction(light_action)
        tm.addAction(dark_action)
        # メニュー参照を保持（apply_theme でチェック状態を同期するため）
        self._action_light   = light_action
        self._action_dark    = dark_action

        # ── カラー調整(開発用) ────────────────────────────────────────
        setting_menu.addSeparator()
        color_adj_act = QAction("カラー調整(開発用)...", self)
        color_adj_act.triggered.connect(self._open_color_adjustment)
        setting_menu.addAction(color_adj_act)

        # ── 初回起動状態にして終了(開発用) ───────────────────────────
        # QSettings("Kizuki", "Kizuki") の全キーを削除してアプリを終了する。
        # 次回起動時には全 value(key, default) がデフォルト値を返すため、
        # 結果として「初回起動時」と同じ状態でアプリが立ち上がる。
        # 開発者向けの動作確認用機能のため、確認ダイアログは出さず即実行する。
        first_launch_act = QAction("初回起動状態にして終了(開発用)", self)
        first_launch_act.triggered.connect(self._reset_to_first_launch_and_quit)
        setting_menu.addAction(first_launch_act)

        # ── 音量 メニュー（トップレベル） ──────────────────────────
        # サブメニュー内に「NN%」ラベル + スライダー(0〜100%) を埋め込む。
        # ラベルはスライダーのつまみ位置に追従して横移動する。
        # 0% にすればミュート扱い（再生はスキップされる）。
        from PyQt6.QtWidgets import QWidgetAction

        # 音量メニュー: メニューバーには載せず、タイトルバーの音量アイコン
        # クリックから popup する独立メニューとして構築する
        # (objectName/style/positioner はタイトルバー登録時に style_qmenu で
        #  まとめて適用するので、ここでは QMenu の生成のみ)
        from PyQt6.QtWidgets import QMenu
        volume_menu = QMenu(self)

        # コンテナ widget: スライダーとラベルをまとめて埋め込むための入れ物
        # 縦置きスライダー: 上にラベル、下に縦スライダー、を並べる縦長レイアウト。
        vol_container = QWidget()
        vol_container.setStyleSheet("background:transparent;")
        SLIDER_LENGTH = 160      # 縦スライダーの高さ
        SLIDER_SIDE_PAD = 20     # スライダー両端からコンテナ枠までの余白
        V_TOP = SP_MD            # 12 (ラベル分の上余白)
        V_GAP = SP_SM            # 8  (ラベルとスライダーの隙間)
        V_BOTTOM = SP_MD         # 12
        # コンテナサイズ: 横幅は SLIDER_SIDE_PAD*2 + SLIDER_HEIGHT (= 20*2 + 28 = 68)
        # 「100%」ラベル(幅約 34px)は中央寄せで収まる(左右余白 17px)。
        # 縦は V_TOP + ラベル高さ(~20) + V_GAP + SLIDER_LENGTH + V_BOTTOM
        LABEL_H = 20
        vol_container.setFixedSize(
            SLIDER_SIDE_PAD * 2 + SLIDER_HEIGHT,
            V_TOP + LABEL_H + V_GAP + SLIDER_LENGTH + V_BOTTOM,
        )

        # ラベル: 上部中央に表示、つまみの値に追従して文字だけ更新する
        # (位置は固定。縦スライダーでは追従が難しいので簡素化)
        _init_pct = int(self._sound.volume * 100)
        self._volume_label = QLabel(f"{_init_pct}%", vol_container)
        # フォントサイズ 14px (デザイントークン SM)。NavBar の他テキストと統一感。
        self._volume_label.setStyleSheet(
            f"color:{T().TEXT.name()}; background:transparent; font-size:14px;"
        )
        self._volume_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._volume_label.setFixedWidth(vol_container.width())
        self._volume_label.setFixedHeight(LABEL_H)
        self._volume_label.move(0, V_TOP)

        # スライダー本体（垂直）
        self._volume_slider = FlatSlider(vol_container, orientation=Qt.Orientation.Vertical)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(int(self._sound.volume * 100))
        self._volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._volume_slider.setPageStep(5)
        self._volume_slider.setSingleStep(1)
        # 横方向に中央寄せ、縦は ラベル下から SLIDER_LENGTH 高
        slider_x = (vol_container.width() - SLIDER_HEIGHT) // 2
        slider_y = V_TOP + LABEL_H + V_GAP
        self._volume_slider.setGeometry(slider_x, slider_y, SLIDER_HEIGHT, SLIDER_LENGTH)
        self._volume_slider.update()
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        # 縦スライダー用クリックジャンプ
        self._volume_slider.mousePressEvent = self._volume_slider_mouse_press

        self._volume_container = vol_container

        action = QWidgetAction(self)
        action.setDefaultWidget(vol_container)
        volume_menu.addAction(action)
        self._volume_menu = volume_menu

        # 余白クリックでメニューが閉じないようにする
        # 標準では QMenu は QWidgetAction の widget 内でも、その widget が
        # マウスイベントを accept しない領域をクリックすると閉じる挙動。
        # vol_container にイベントフィルタを仕掛けて、スライダー外のクリックは
        # 「自分で処理した(accept)」扱いにする → QMenu までイベントが上らない
        # → QMenu の閉じる挙動が発火しない。
        # スライダー上のクリックは通常通り伝播させて値変更が動く。
        # メニュー外クリック / Esc / アイコン再クリックは別経路なので影響なし。
        from PyQt6.QtCore import QObject, QEvent

        class _VolContainerFilter(QObject):
            def __init__(self, container, slider):
                super().__init__(container)
                self._container = container
                self._slider = slider

            def eventFilter(self, obj, ev):
                if obj is self._container and ev.type() in (
                        QEvent.Type.MouseButtonPress,
                        QEvent.Type.MouseButtonRelease,
                        QEvent.Type.MouseButtonDblClick):
                    # クリック位置がスライダー領域内なら通常処理(伝播 → スライダーへ)
                    if self._slider.geometry().contains(ev.pos()):
                        return False
                    # 余白: イベントを「処理済み」とみなして親(QMenu)に伝えない
                    ev.accept()
                    return True
                return False

        self._vol_container_filter = _VolContainerFilter(vol_container, self._volume_slider)
        vol_container.installEventFilter(self._vol_container_filter)

        # ── カスタムタイトルバーへのメニュー接続 ──────────────────
        # _CustomTitleBar.MENU_LABELS と同じ順序で QMenu を渡す。
        # 各メニューに style_qmenu を呼んで QSS+Frameless+透過化を統一適用。
        # (vm は既に style_qmenu 済みだが冪等なので重複呼び出しでも問題なし)
        # 音量はメニューバーから外し、タイトルバー右端のアイコンから popup する。
        # ルール/コミ/テーマ/棋力は「設定」メニューのサブメニューとして集約。
        # サブメニュー位置補正は style_qmenu 内で _install_submenu_positioner
        # が仕込まれるので個別呼び出しは不要。
        if hasattr(self, "_titlebar"):
            # fm: 子に「新規作成」サブメニューを持つので非リーフ
            # vm: 表示メニュー (リーフ、既に style_qmenu 済み)
            # setting_menu: 子サブメニュー (棋力/ルール/コミ/テーマ) を持つので非リーフ
            style_qmenu(fm)
            style_qmenu(setting_menu)
            menus_in_order = [fm, vm, setting_menu]
            for i, menu in enumerate(menus_in_order):
                self._titlebar.set_menu(i, menu)
            # 音量メニューはリーフ (スライダー埋め込みのみ)
            style_qmenu(volume_menu, leaf=True)
            # 音量アイコンクリック → 音量メニューを popup
            self._titlebar.volume_clicked.connect(self._on_volume_icon_clicked)

        # ── ショートカットキーを MainWindow 直接アクションとして登録 ──
        # QMenuBar.hide() で QAction のショートカットも無効化されるため、
        # 主要な QAction を MainWindow に直接 addAction して復活させる。
        # 元のメニュー上の QAction とは同じインスタンスなので、両方から呼ばれない。
        for action in self.findChildren(QAction):
            if action.shortcut() and not action.isSeparator():
                # 既に MainWindow の actions() に含まれている場合は重複しない
                if action not in self.actions():
                    self.addAction(action)

    # ── KataGo エンジン管理 ────────────────────────────────────

    def _reset_to_first_launch_and_quit(self):
        """開発用: 全ての永続設定を削除してアプリを終了する。
        次回起動時、QSettings は空になっているため、value(key, default) は
        全てデフォルト値を返し、結果として「初回起動時」と同じ状態で
        アプリが立ち上がる。

        QSettings.clear() は ("Kizuki", "Kizuki") スコープに含まれる
        キーのみを削除するため、他アプリの設定には影響しない。
        sync() で確実にディスクへ書き込んでから quit() する。
        """
        from PyQt6.QtCore import QSettings
        from PyQt6.QtWidgets import QApplication
        qs = QSettings("Kizuki", "Kizuki")
        qs.clear()
        qs.sync()
        QApplication.quit()

    def _on_delete_node(self):
        """現在のノードを削除して1手戻る。
        現在ノードを親の children から除去し、親ノードへ移動する。
        ルートノード（親なし）では何もしない。
        """
        if not self._game_state:
            return
        node = self._game_state.current_node
        parent = node.parent
        if parent is None:
            # ルートは削除不可
            return

        # 親の children から現在ノードを除去（子孫も同時に消える）
        if node in parent.children:
            parent.children.remove(node)

        # 解析キャッシュから現在ノード以降を削除（メモリ節約）
        # ノードIDベースなので親を残して子孫だけ消す
        def _remove_from_cache(n):
            self._node_analyses.pop(id(n), None)
            for c in n.children:
                _remove_from_cache(c)
        _remove_from_cache(node)

        # 1手戻る（親へ移動）
        self._game_state.go_to_node(parent)

        # 注: ノード削除は分岐探索の一部とみなし、dirty フラグは立てない。
        # 詳細は _write_comment_to_node の docstring 参照。

        # スライダー最大値を再計算
        main_line = self._game.main_line()
        total = max(0, len(main_line) - 1)
        self._navbar.slider.setMaximum(total)
        self._info.get_graph().set_total_moves(total)

        self._refresh_board()
        self._status_bar.showMessage("手を削除しました")

    # ── 対局情報の正規化とエンジンへの伝達 ──────────────────────────

    def _build_states(self):
        """GameState を初期化し、スライダーの最大値を設定する。
        加えて、現在のソフト側ルール/コミ設定と棋譜の置き石を KataGo に伝える。
        SGF の KM/RU は信頼しない方針なので、ここでは無視する。
        盤面サイズ（SZ）のみ SGF から取得して BoardWidget と KataGoEngine に反映する。"""
        if not self._game: return
        self._node_analyses = {}       # ノード解析結果をリセット
        # 棋譜切替時に手順番号起点をリセット。
        # ただし「手順」トグルが ON のままなら、リセット先は None ではなく
        # 0 (= ルートから全手順) にする。None にしてしまうと、トグルが ON
        # のままでも _refresh_board で手順番号辞書が空になり、棋譜を開いた
        # 直後に手順が表示されない不具合になる。
        self._move_number_anchor = 0 if self._move_numbers_enabled else None
        if hasattr(self, "_info"):
            self._info.get_graph().clear_data()  # グラフデータをリセット
        self._game_state = GameState(self._game)
        # 盤面サイズを反映（19路以外の棋譜にも対応）
        bs = self._game.board_size
        if bs != self._board.board_size:
            self._board.board_size = bs
            self._board.update()
        if self._engine and bs != self._engine.board_size:
            # ポンダリング中の古いクエリを止めてからサイズ変更（次回クエリで反映される）
            try:
                self._engine.stop_pondering()
            except Exception:
                pass
            self._engine.board_size = bs
        # 手数カウント（メインライン）
        main_line = self._game.main_line()
        total = max(0, len(main_line) - 1)
        self._navbar.slider.setMaximum(total)
        self._info.get_graph().set_total_moves(total)
        # ソフト側設定値 + 新しい棋譜の置き石をエンジンへ伝達。
        # ポンダリング再開は呼び出し側（_load_demo / _open_sgf_path 等）が
        # _goto_first → _refresh_board 経由で行うので、ここでは再開しない。
        self._apply_rules_komi_to_engine(restart_pondering=False)

    @_profile_method("_refresh_board")
    def _refresh_board(self, skip_ponder: bool = False):
        """現在の GameState の状態を盤面・UIに反映する。"""
        if not self._game_state: return
        # ノード移動・着手追加・分岐切替などのタイミングで呼ばれるので、
        # _update_graph の構造キャッシュを無効化しておく。
        # （cache_key は (cur_node, root) の id で構成されるため、ノード追加・
        # 削除では id が変わらず自動再構築されないケースがあり、明示的に無効化
        # する必要がある。）
        self._invalidate_graph_struct_cache()
        gs = self._game_state
        node = gs.current_node
        stones = gs.stones
        last_move = node.move
        sgf_comment = node.comment

        # 解析OFFかつキャッシュなしの手に移動した場合のみ_label_scoreをリセット
        # → ハイフン表示（解析ONの場合は前回値保持でチラつき防止）
        if hasattr(self, "_info"):
            graph = self._info.get_graph()
            node_ma = self._node_analyses.get(id(node))
            if node_ma is None and not self._ai_enabled:
                graph._label_score = None

        # 手数インデックス（メインライン上の位置）
        path = gs.path_to_root()
        idx = len(path) - 1
        self._current_idx = idx

        # 解析結果: ノード単位で取得（分岐対応）
        ma = self._node_analyses.get(id(node), None)
        candidates = ma.best_moves if ma else []
        blunder = ma.blunder if ma else None

        # 次の手・分岐を収集
        next_moves = []
        for i, child in enumerate(node.children):
            if child.move_color and child.move:
                col2, row2 = child.move
                next_moves.append((col2, row2, child.move_color, i == 0))

        # 手順番号辞書を構築:
        #   self._move_number_anchor が None なら何も入れない（描画なし）
        #   anchor が指定されていれば、path[anchor+1] が "1"、以降を 2, 3, ... と
        #   振り直す（振り直し方式）。anchor は「番号 1 を振る手の path インデックス
        #   - 1」を保持しており、_on_move_number_anchor_requested 側で計算される。
        #   現在位置が anchor より先(= path 長が anchor 以下)なら自動的に空辞書になる。
        move_numbers: dict = {}
        anchor = self._move_number_anchor
        if self._move_numbers_enabled and anchor is not None:
            for i in range(anchor + 1, len(path)):
                n = path[i]
                if n.move_color and n.move:
                    move_numbers[n.move] = i - anchor  # 1始まり
        self._board.move_numbers = move_numbers
        with _profile("_refresh_board.set_position"):
            self._board.set_position(stones, candidates, last_move, blunder, next_moves, turn=gs.turn)
        with _profile("_refresh_board.update_analysis"):
            self._info.update_analysis(ma, sgf_comment)
        with _profile("_refresh_board.load_comment"):
            self._load_comment_to_overlay(sgf_comment)
        # 手の情報カードを更新:
        # キャッシュあり → 即時表示
        # キャッシュなし＋AI解析OFF → 基本情報（石・手数・座標）+ 「不明」バッジ
        # キャッシュなし＋AI解析ON → 呼ばない（ポンダリング結果が来た時に更新）
        if ma is not None:
            self._move_card.update_card(ma, ma.color)
        elif not self._ai_enabled:
            # 第2引数には「現在ノードで打たれた手の色」を渡す。
            # gs.turn は「次に打つ手番」なので逆になる点に注意。
            # ルートノード等で node.move_color が空の場合のみ gs.turn にフォールバック。
            mc = node.move_color if node.move_color else gs.turn
            # move_number: ルートから現在ノードまでの打ち手数（move_color がある手のみ）
            _mn = 0
            _n = node
            while _n:
                if _n.move_color:
                    _mn += 1
                _n = _n.parent
            # 座標は SGF 形式で取得し、人間可読形式（例: G16）に変換
            _coord_sgf = node.get(node.move_color, "") if node.move_color else ""
            _human = (sgf_coord_to_human(_coord_sgf, self._game.board_size)
                      if _coord_sgf and self._game else "")
            self._move_card.update_card(None, mc, _mn, _human)
        # アゲハマ: GameState._black_captures = 白が取った黒石の数 = 白のアゲハマ
        # なので表示用には入れ替えて渡す
        self._info.update_captures(gs._white_captures, gs._black_captures)
        # グラフ更新は _update_graph に委譲（メインライン描画＋縦線位置＋ラベル）
        self._update_graph()

        # スライダー同期:
        # 案A: 現在ノードがメインライン上ならスライダー maximum はメインライン長、
        #      サブ分岐上ならスライダー maximum は「サブ分岐の真の末尾まで」の
        #      手数(現在ノードを起点に主分岐方向に最後まで辿ったノードまで)。
        #      これによりサブ分岐内でも cur_idx < total となるケースが生まれ、
        #      スライダーがサブ分岐内で意味を持って前後に動く。
        all_main_nodes = self._game.main_line() if self._game else []
        path_nodes = self._game_state.path_to_root() if self._game_state else []
        path_move_count = sum(1 for n in path_nodes if n.move_color)
        main_move_count = sum(1 for n in all_main_nodes if n.move_color)
        on_main_line = node in all_main_nodes
        if on_main_line:
            total = main_move_count
        else:
            # サブ分岐の真の末尾を求める: 現在ノードから主分岐の子(children[0])を
            # 末端まで辿る。子がなくなった時点で末尾。
            tail = node
            while tail.children:
                tail = tail.children[0]
            # ルートから tail までの打ち手数 = サブ分岐全体の手数
            t = tail
            tail_move_count = 0
            while t.parent is not None:
                if t.move_color:
                    tail_move_count += 1
                t = t.parent
            total = tail_move_count
        # 現在の手数 = 現在ノードまでの「打ち手」数(ルートは含まない)
        cur_idx = path_move_count
        if self._navbar.slider.maximum() != total:
            self._navbar.slider.blockSignals(True)
            self._navbar.slider.setMaximum(total)
            self._navbar.slider.blockSignals(False)
        # スライダー値を更新(枠外にならないよう maximum 内でクランプ)
        self._navbar.slider.blockSignals(True)
        self._navbar.slider.setValue(min(cur_idx, self._navbar.slider.maximum()))
        self._navbar.slider.blockSignals(False)

        # 分岐ツリー更新＋現在ノードへスクロール（中央寄せ）
        _active_tree   = self._branch_tree
        _active_scroll = self._branch_scroll
        _active_tree.update_tree(gs, self._node_analyses)
        # 同じ行の移動かを判定
        same_row = bool(getattr(_active_tree, "_last_move_same_row", False))
        new_xy = getattr(_active_tree, "_last_move_new_xy", None)
        def _do_scroll():
            pos = _active_tree.current_node_xy()
            if not pos:
                return
            vp_w = _active_scroll.viewport().width()
            vp_h = _active_scroll.viewport().height()
            target_x = pos[0] - vp_w // 2
            target_y = pos[1] - vp_h // 2
            if same_row and new_xy is not None and _active_tree._cur_marker_xy is not None:
                # 同じ行の移動: 統合アニメでスクロールとリング絶対座標を同期駆動
                # (1つの valueChanged の中で両方を setValue するためフレーム間
                # ズレなし。リング画面位置は完全に固定または滑らかに移動する)
                self._animate_unified(
                    _active_scroll, target_x, target_y,
                    _active_tree, new_xy,
                )
            else:
                # 通常: スクロールアニメのみ(リングは update_tree が絶対座標
                # アニメ or 瞬時切替で処理済み)
                self._smooth_scroll_to(_active_scroll, target_x, target_y)
        if same_row:
            # 統合アニメ起動のため即座に呼ぶ(QTimer 経由だと 1 フレーム遅延)
            _do_scroll()
        else:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, _do_scroll)

        # ステータス: 手番表示
        turn_str = "黒番" if gs.turn == "B" else "白番"
        self._status_bar.showMessage(f"手 {idx}  {turn_str}  {'分岐あり (' + str(len(node.children)) + ')' if len(node.children) > 1 else ''}")



        # 局面が変わるたびにポンダリングを再開（ドラッグ中はスキップ）
        if not skip_ponder:
            self._start_pondering_current()

    def _goto(self, idx):
        """指定インデックスへ移動（ポンダリングあり）。
        メインライン上にいる場合: idx はメインライン上の打ち手インデックス。
        サブ分岐上にいる場合: idx は現在のパス(ルート→現在ノード)内の
                              インデックス。これによりスライダーや目差グラフで
                              サブ分岐内を辿る操作が可能になる。"""
        if not self._game_state: return
        self._save_comment_if_editing(self._game_state.current_node)
        target = self._resolve_goto_target(idx)
        if target is None: return
        self._game_state.go_to_node(target)
        self._refresh_board()

    def _goto_no_ponder(self, idx):
        """ドラッグ中の盤面表示のみ更新（ポンダリングなし、軽量更新版）。
        stop_pondering の IPC コストを避けるため、ドラッグ開始時に
        _on_slider_pressed で1度だけ停止する設計。各フレームでは
        _refresh_board_minimal で最小限の更新のみ行う。
        idx の解釈は _goto と同じ(現在のパス上 or メインライン上)。
        """
        if not self._game_state: return
        target = self._resolve_goto_target(idx)
        if target is None: return
        self._game_state.go_to_node(target)
        self._refresh_board_minimal()

    def _resolve_goto_target(self, idx):
        """スライダー値 idx を「移動先ノード」に変換する共通ヘルパー。
        idx は 0 始まり: 0 = ルート(初期局面)、N = N手目。

        - メインライン上にいる場合: main_line[idx] へ
        - サブ分岐上にいる場合: 現在パスの「ルート + 打ち手」内に閉じる
          (idx がパス長を超えたらサブ分岐の末尾でクランプ)。
          メインラインに戻るには分岐ツリーから操作する必要がある。
        """
        if not self._game_state or not self._game:
            return None
        cur = self._game_state.current_node
        main_line = self._game.main_line()
        on_main_line = cur in main_line
        if on_main_line:
            i = max(0, min(idx, len(main_line) - 1))
            return main_line[i] if main_line else None
        else:
            # サブ分岐上: 「ルート → サブ分岐の真の末尾」までのパスを構築する。
            # スライダー/グラフの total は同じ範囲で計算されているため、
            # idx が現在ノードを超えてサブ分岐の末尾まで指せるようにするため、
            # path_to_root(現在ノードまで)ではなく主分岐の子を末端まで辿る。
            tail = cur
            while tail.children:
                tail = tail.children[0]
            # tail からルートまで遡って逆順にする
            extended_path = []
            n = tail
            while n is not None:
                extended_path.append(n)
                n = n.parent
            extended_path.reverse()  # ルート → tail
            # 「ルート + 打ち手」で構成(コメントノード等の move_color なしを除外)
            move_path = [n for n in extended_path
                         if n is extended_path[0] or n.move_color]
            # サブ分岐内に閉じる: idx を範囲内にクランプ
            i = max(0, min(idx, len(move_path) - 1))
            return move_path[i] if move_path else None

    def _refresh_board_minimal(self):
        """ドラッグ中用の最小更新: 盤面 + アゲハマ + ステータスバー + グラフ縦線
        + 分岐ツリー更新＋スクロール + スライダー値追従。
        通常の _refresh_board が行う以下の処理は省略する（リリース時の _goto で復活）:
          - コメント欄更新
          - 移動カード更新(評価は省略、軽量更新のみ)
          - グラフ折れ線再描画（縦線位置のみ更新）
          - 候補手・悪手ハイライト（ドラッグ中は意味が薄い）
        スライダー値はスライダー自身がドラッグ中の場合のみ省略し、
        目差グラフ等の外部ソースからのドラッグ時は連動更新する。
        これにより1フレームあたりの処理量を大幅削減し、マウス追従性を改善する。
        """
        if not self._game_state: return
        gs = self._game_state
        node = gs.current_node

        # 次の手・分岐
        next_moves = []
        for i, child in enumerate(node.children):
            if child.move_color and child.move:
                col2, row2 = child.move
                next_moves.append((col2, row2, child.move_color, i == 0))

        # 1) 盤面更新（候補手・悪手ハイライトは省略）
        self._board.move_numbers = {}  # 手順番号もドラッグ中は描画しない
        self._board.set_position(
            gs.stones, [], node.move, None, next_moves, turn=gs.turn)

        # 2) アゲハマ更新（軽量）
        self._info.update_captures(gs._white_captures, gs._black_captures)

        # 3) 手数インデックス更新＋ステータスバー
        path = gs.path_to_root()
        idx = len(path) - 1
        self._current_idx = idx
        turn_str = "黒番" if gs.turn == "B" else "白番"
        self._status_bar.showMessage(f"手 {idx}  {turn_str}")

        # 4) MoveInfoCard の手番・手数・座標を軽量更新（評価は更新しない）
        mc = node.move_color or ""
        _mn = idx
        _coord = node.get(mc, "") if mc else ""
        _human = sgf_coord_to_human(_coord, self._game.board_size) if _coord else ""
        cur_ma = self._node_analyses.get(id(node))
        self._move_card.update_card(cur_ma, mc, _mn, _human)

        # 4) グラフ縦線のみ追従（折れ線データは更新しない）
        cur_ma = self._node_analyses.get(id(node))
        score = cur_ma.score_lead if cur_ma else None
        try:
            self._info.get_graph().set_current(idx, score)
        except Exception:
            pass

        # 4.5) スライダー値を追従（スライダー自身がドラッグ中の場合のみ省略）
        # 目差グラフや他のソースからのドラッグの場合、スライダーは
        # 動きを知らないため明示的に値を更新する必要がある。
        # 自身がドラッグ中の場合は値が既に正しいので setValue 不要。
        try:
            slider = self._navbar.slider
            if not slider.isSliderDown():
                slider.blockSignals(True)
                slider.setValue(min(idx, slider.maximum()))
                slider.blockSignals(False)
        except Exception:
            pass

        # 5) 分岐ツリー更新＋現在ノードへスクロール（中央寄せ）
        _active_tree   = self._branch_tree
        _active_scroll = self._branch_scroll
        _active_tree.update_tree(gs, self._node_analyses)
        same_row = bool(getattr(_active_tree, "_last_move_same_row", False))
        new_xy = getattr(_active_tree, "_last_move_new_xy", None)
        def _do_scroll_2():
            pos = _active_tree.current_node_xy()
            if not pos:
                return
            vp_w = _active_scroll.viewport().width()
            vp_h = _active_scroll.viewport().height()
            target_x = pos[0] - vp_w // 2
            target_y = pos[1] - vp_h // 2
            if same_row and new_xy is not None and _active_tree._cur_marker_xy is not None:
                self._animate_unified(
                    _active_scroll, target_x, target_y,
                    _active_tree, new_xy,
                )
            else:
                self._smooth_scroll_to(_active_scroll, target_x, target_y)
        if same_row:
            _do_scroll_2()
        else:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, _do_scroll_2)

    # 分岐ツリーのスクロール追従アニメ用: scroll_area ごとに進行中アニメを保持
    # する辞書(キー = id(scroll_area))。1つのスクロールエリアに対する複数回の
    # 呼び出しは、進行中アニメを停止して現在値を起点に新しい目標値へ再開する。
    _SCROLL_ANIM_DURATION_MS = 280

    def _goto_first(self): self._goto(0)
    def _goto_last(self):  self._goto(len(self._game.main_line()) - 1 if self._game else 0)
    def _prev(self):
        if self._game_state:
            # ルートノード（親なし）では何もしない
            if self._game_state.current_node.parent is None:
                return
            self._save_comment_if_editing(self._game_state.current_node)
            self._game_state.backward()
            self._refresh_board()
    def _next(self):
        if self._game_state:
            # 最終手（子ノードなし）では何もしない
            if not self._game_state.current_node.children:
                return
            self._save_comment_if_editing(self._game_state.current_node)
            cap_before = (self._game_state._black_captures + self._game_state._white_captures)
            self._game_state.forward()
            cap_after = (self._game_state._black_captures + self._game_state._white_captures)
            if cap_after > cap_before:
                self._sound.play_capture()
            elif self._game_state.current_node.move_color:
                self._sound.play_place()
            self._refresh_board()
    def _on_slider_value_changed(self, v):
        """クリックによる値変化時のみ処理（ドラッグ中は sliderMoved が担当）。"""
        if not self._navbar.slider.isSliderDown():
            self._goto(v)

    def _on_slider_pressed(self):
        """ドラッグ開始時に1度だけポンダリングを停止する（追従性能のため）。
        ドラッグ中の各フレームで stop_pondering を呼ぶと IPC コストでマウスに
        ついていけなくなるため、開始時に1度だけ停止し、リリース時に再開する。
        """
        if self._engine and self._engine.is_running():
            self._engine.stop_pondering()

    def _on_slider_drag(self, v):
        """ドラッグ中: 盤面表示のみ更新、ポンダリングなし。"""
        self._goto_no_ponder(v)

    def _on_slider_released(self):
        """ドラッグ完了: 現在のスライダー値でポンダリング付き移動。"""
        v = self._navbar.slider.value()
        self._goto(v)

    def _on_graph_dragged(self, idx: int):
        """目差グラフのクリック/ドラッグ中: 盤面表示のみ更新（ポンダリングなし）。
        idx の解釈は _goto と同じ(メインライン上なら main_line のインデックス、
        サブ分岐上なら path_to_root のインデックス)。"""
        if not self._game_state:
            return
        self._goto_no_ponder(idx)

    def _on_graph_released(self, idx: int):
        """目差グラフのクリック完了: その手に確定移動（ポンダリング再開）。"""
        if not self._game_state:
            return
        self._goto(idx)

    def _on_board_click(self, col: int, row: int):
        """碁盤クリック。解析モード: 石を打つ/右クリックで1手戻る。"""
        # 右クリック: 1手戻る
        if col == -1 and row == -1:
            self._prev()
            return
        # 解析モード: 石を打つ
        if not self._game_state: return
        self._save_comment_if_editing(self._game_state.current_node)
        cap_before = (self._game_state._black_captures + self._game_state._white_captures)
        node = self._game_state.play(col, row)
        if node is None:
            self._status_bar.showMessage("着手不可（既に石がある、または自殺手）")
            return
        cap_after = (self._game_state._black_captures + self._game_state._white_captures)
        if cap_after > cap_before:
            self._sound.play_capture()
        else:
            self._sound.play_place()
        # 注: 盤面クリック (分岐探索) は dirty フラグを立てない。
        # 詳細は _write_comment_to_node の docstring 参照。
        self._refresh_board()

    # コメントオーバーレイのアニメ用パラメータ
    _COMMENT_ANIM_DURATION = 250    # アニメ時間 (ms)
    _COMMENT_ANIM_SLIDE_PX = 16     # スライド距離 (下から上へ立ち上がる量)

        # フォーカスはアニメ完了後に当てる方が自然 (アニメ中にフォーカスを
        # 当てるとカーソルが点滅し始めて視覚的にうるさい)
        # → _on_comment_overlay_open_done で setFocus する

    def _app_event_filter_active(self):
        return (hasattr(self, "_comment_overlay")
                and self._comment_overlay.isVisible())

    def mousePressEvent(self, ev):
        super().mousePressEvent(ev)

    def _on_branch_node_clicked(self, node):
        """分岐ツリーのノードをクリック → そこへジャンプ。"""
        if not self._game_state: return
        with _profile("node_clicked.save_comment"):
            self._save_comment_if_editing(self._game_state.current_node)
        with _profile("node_clicked.goto_node"):
            self._game_state.go_to_node(node)
        # _refresh_board と _update_graph はそれぞれ自身に @_profile_method 付き
        self._refresh_board()
        #  分岐ノードが対象に含まれるよう _update_graph も明示的に呼ぶ)
        self._update_graph()

    def _on_delete_branch_node(self, node):
        """分岐ツリーの右クリックメニューから指定ノードを削除する。"""
        if not self._game_state:
            return
        parent = node.parent
        if parent is None:
            return  # ルートは削除不可（念のため）

        # 削除対象ノードが現在カーソル位置か、その子孫にいる場合は親へ移動
        cur = self._game_state.current_node
        def _is_descendant(target, ancestor):
            n = target
            while n is not None:
                if n is ancestor:
                    return True
                n = n.parent
            return False

        if cur is node or _is_descendant(cur, node):
            self._game_state.go_to_node(parent)

        # 親の children から除去
        if node in parent.children:
            parent.children.remove(node)

        # 解析キャッシュから再帰削除
        def _remove_from_cache(n):
            self._node_analyses.pop(id(n), None)
            for c in n.children:
                _remove_from_cache(c)
        _remove_from_cache(node)

        # 注: ノード削除は分岐探索の一部とみなし、dirty フラグは立てない。
        # 詳細は _write_comment_to_node の docstring 参照。

        # スライダー最大値を再計算
        main_line = self._game.main_line()
        total = max(0, len(main_line) - 1)
        self._navbar.slider.setMaximum(total)
        self._info.get_graph().set_total_moves(total)

        self._refresh_board()
        self._status_bar.showMessage("ノードを削除しました")

    def _on_move_number_anchor_requested(self, node):
        """分岐ツリーの右クリックメニュー
        「この手以降の手順を表示」(通常ノード) / 「全手の手順を表示」(ルート) が
        選ばれたとき。

        指定ノードを起点として 1, 2, 3, ... の番号を表示する（振り直し方式）。
        - 通常ノード: クリックした手自身が "1"、以降が 2, 3, ...
        - ルートノード: 着手ではないため、1手目を "1" として 2, 3, ...
          （= 結果的に全手表示）

        内部状態 self._move_number_anchor は「番号 1 を振る手の path
        インデックス - 1」として保持する。これにより _refresh_board の
        ループ (range(anchor+1, len(path)) で i-anchor を番号にする) と整合する。
        """
        if not self._game:
            return
        # ルートから node までの距離を計算（root = 0、1手目 = 1, ...）
        depth = 0
        n = node.parent
        while n is not None:
            depth += 1
            n = n.parent
        # ルートクリック時は path[1] (=1手目) を "1" にしたいので anchor=0。
        # 通常ノード(depth>=1)クリック時は path[depth] (=その手) を "1" に
        # したいので anchor=depth-1。
        if depth == 0:
            self._move_number_anchor = 0
        else:
            self._move_number_anchor = depth - 1
        # 右クリックで起点設定したら手順トグルをONにする。
        # ToggleBar 側のスイッチを ON にすれば move_numbers_toggled シグナル経由で
        # _on_move_numbers_toggled が呼ばれ、_move_numbers_enabled の更新・永続化・
        # 盤面再描画まで一括で行われる（既に ON の場合はシグナル不発）。
        if hasattr(self, '_toggle_bar') and not self._toggle_bar._sw_mn.isChecked():
            self._toggle_bar._sw_mn.setChecked(True)
        else:
            # 既に ON の場合（または ToggleBar 未生成の念のため）は手動で同期
            self._move_numbers_enabled = True
            self._refresh_board()

    # ── ポンダリング ─────────────────────────────────────────────

    def _build_moves_to_node(self, node) -> list[tuple[str, str]]:
        """指定ノードまでの手順を [(color, gtp_coord), ...] で返す。"""
        # ルートから現在ノードまでのパスを取得
        path = []
        n = node
        while n is not None:
            path.append(n)
            n = n.parent
        path.reverse()  # ルートから順に並べる

        moves = []
        bs = self._game.board_size
        for n in path:
            color = n.move_color
            if not color:
                continue
            coord = n.get(color, "")
            if not coord or coord == "tt":
                moves.append((color, "pass"))
                continue
            pos = sgf_coord_to_pos(coord)
            if pos is None:
                moves.append((color, "pass"))
                continue
            col2, row2 = pos
            gtp = f"{'ABCDEFGHJKLMNOPQRST'[col2]}{bs - row2}"
            moves.append((color, gtp))

        return moves

    def eventFilter(self, obj, ev):
        """アプリ全体のイベント監視:
        ⓪ frameless ウィンドウのリサイズ/ドラッグ/ダブルクリック判定
           (子ウィジェット上のマウス操作でも端でリサイズできるようにする)
        ① コメントオーバーレイ表示中は、オーバーレイ外への入力(マウス/ホイール/
           キー)を遮断する。マウスは Press で閉じ、ホイール/キーは消費。
           Esc キーでオーバーレイを閉じる。
        ② コメント欄のフォーカスアウト保存
        ③ 子ウィジェットのキー操作をメインウィンドウへ転送
        """
        from PyQt6.QtCore import QEvent, QRect
        from PyQt6.QtWidgets import QApplication

        # ポップアップ(QMenu 等)表示中は、frameless ウィンドウ向けの
        # マウス処理(端リサイズ・タイトルバードラッグ・ダブルクリックで
        # 最大化)をすべてスキップする。これらの処理はクリック位置で
        # ウィンドウ端を検出して startSystemResize() を呼んだり、
        # タイトルバー上で startSystemMove() を呼んだりするが、
        # メニュー項目クリックがウィンドウ端付近で発生するとそれらが
        # 誤発火し、メニューがキャンセルされて triggered が発火しなく
        # なってしまう。ポップアップ中は Qt 側のメニュー処理を最優先する。
        if QApplication.activePopupWidget() is not None:
            mouse_types = (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
                QEvent.Type.MouseMove,
            )
            if ev.type() in mouse_types:
                return super().eventFilter(obj, ev)

        # ⓪ frameless ウィンドウ操作: 端でリサイズ、タイトルバー上空きでドラッグ
        # 子ウィジェットがマウスイベントを先取りするため、eventFilter で
        # 親(MainWindow) の役割を代行する。MainWindow 座標系で判定する。
        if (ev.type() == QEvent.Type.MouseMove
                and isinstance(obj, QWidget)
                and not self.isMaximized()
                and not self._is_pseudo_maximized()):
            try:
                gpos = ev.globalPosition().toPoint()
                win_pos = self.mapFromGlobal(gpos)
                edge = self._edge_at(win_pos)
                if edge is not None:
                    cursor_map = {
                        Qt.Edge.LeftEdge:  Qt.CursorShape.SizeHorCursor,
                        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                        Qt.Edge.TopEdge:   Qt.CursorShape.SizeVerCursor,
                        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
                        Qt.Edge.LeftEdge | Qt.Edge.TopEdge:     Qt.CursorShape.SizeFDiagCursor,
                        Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
                        Qt.Edge.RightEdge | Qt.Edge.TopEdge:    Qt.CursorShape.SizeBDiagCursor,
                        Qt.Edge.LeftEdge | Qt.Edge.BottomEdge:  Qt.CursorShape.SizeBDiagCursor,
                    }
                    self.setCursor(cursor_map.get(edge, Qt.CursorShape.ArrowCursor))
                    self._on_resize_edge = True
                else:
                    # 端から外れた直後だけ unsetCursor を呼ぶ(チラつき防止)。
                    # 通常時(端ではない)は子ウィジェット側のカーソルを尊重する。
                    if getattr(self, "_on_resize_edge", False):
                        self.unsetCursor()
                        self._on_resize_edge = False
            except Exception:
                pass

        if (ev.type() == QEvent.Type.MouseButtonPress
                and isinstance(obj, QWidget)
                and ev.button() == Qt.MouseButton.LeftButton):
            try:
                gpos = ev.globalPosition().toPoint()
                win_pos = self.mapFromGlobal(gpos)
                # 1) ウィンドウ端ならリサイズ開始
                edge = self._edge_at(win_pos)
                if edge is not None:
                    wh = self.windowHandle()
                    if wh:
                        wh.startSystemResize(edge)
                        return True  # イベント消費
                # 2) タイトルバー空き領域ならドラッグ移動
                #    ただし「メニューがアクティブ中 / 直近に閉じたばかり」なら
                #    そのクリックは「メニューを閉じる動作」とみなしてドラッグ起動しない。
                if hasattr(self, "_titlebar"):
                    tb = self._titlebar
                    tb_global_top = tb.mapToGlobal(tb.rect().topLeft())
                    tb_rect_global = QRect(tb_global_top, tb.size())
                    if tb_rect_global.contains(gpos):
                        local = gpos - tb_global_top
                        on_button = tb.hit_button_at(local)
                        m_active = tb.menu_active()
                        m_just_closed = tb.menu_just_closed()
                        # 音量メニューは _titlebar._menus に登録されていないので
                        # 別途 visible 判定する
                        vol_menu = getattr(self, "_volume_menu", None)
                        vol_active = vol_menu is not None and vol_menu.isVisible()
                        if not on_button:
                            # メニュー操作の余韻中はドラッグ起動を抑制
                            if m_active:
                                # アクティブメニューを明示的に閉じる
                                # (popup grab 中の Qt は外側クリックを自動で閉じるが、
                                # eventFilter で消費すると自動処理が走らないため手動)
                                cur_idx = getattr(tb, "_active_menu_index", -1)
                                if 0 <= cur_idx < len(getattr(tb, "_menus", [])):
                                    cur_menu = tb._menus[cur_idx]
                                    if cur_menu is not None:
                                        cur_menu.close()
                                return True  # クリック消費(ドラッグ起動しない)
                            if vol_active:
                                # 音量メニューも明示的に閉じてドラッグ起動を抑制
                                vol_menu.close()
                                return True
                            if m_just_closed:
                                return True  # 直前に閉じた連打防止
                            wh = self.windowHandle()
                            if wh:
                                wh.startSystemMove()
                                return True
            except Exception:
                pass

        if (ev.type() == QEvent.Type.MouseButtonDblClick
                and isinstance(obj, QWidget)
                and ev.button() == Qt.MouseButton.LeftButton):
            # タイトルバー空き領域のダブルクリックで最大化/復元
            try:
                if hasattr(self, "_titlebar"):
                    gpos = ev.globalPosition().toPoint()
                    tb = self._titlebar
                    tb_global_top = tb.mapToGlobal(tb.rect().topLeft())
                    tb_rect_global = QRect(tb_global_top, tb.size())
                    if tb_rect_global.contains(gpos):
                        local = gpos - tb_global_top
                        if not tb.hit_button_at(local):
                            self._toggle_maximized()
                            return True
            except Exception:
                pass

        # ① オーバーレイ表示中の外側マウス操作
        # ── 先に Wheel / Key も遮断する ──────────────────────────────
        # コメント欄(オーバーレイ)が開いている間は、コメント編集以外の
        # 操作(マウスホイールでの手送り、矢印キー/スペース等のショートカット)を
        # すべて無効化したい。以下のルールで処理する:
        #   - Wheel: マウス座標がオーバーレイ矩形内なら通す。
        #            外側ならイベント消費(盤面ナビ・グラフ等が動かない)。
        #            ※ obj ベース判定だと QTextEdit の内部 viewport が
        #              親チェーンで辿れないことがあり、コメント欄上の
        #              スクロールまで遮断してしまうため座標ベースで判定。
        #   - KeyPress/KeyRelease: 現在のフォーカスがオーバーレイ内部なら
        #            すべてのキーを通す(コメント編集を妨げない)。
        #            フォーカスが外側にある時のキーだけ消費する。
        #            ※ obj 単独で判定すると Qt のキー伝播経路の都合で
        #              テキスト入力まで遮断してしまうことがあるため、
        #              キーは focusWidget で判定するのが確実。
        #   - Esc は専用ハンドリングで閉じる(フォーカスがオーバーレイ内でも有効)。
        if (hasattr(self, "_comment_overlay")
                and self._comment_overlay.isVisible()):
            etype = ev.type()
            # Esc キーでオーバーレイを閉じる(内外問わず)
            if etype == QEvent.Type.KeyPress:
                try:
                    if ev.key() == Qt.Key.Key_Escape:
                        self._close_comment_overlay()
                        return True
                except Exception:
                    pass
            # Wheel: マウス座標がオーバーレイ矩形内なら通す。外側は消費。
            # ただしポップアップ(QMenu 等)表示中はメニュー側のホイール処理を
            # 邪魔しないよう素通りさせる。
            if etype == QEvent.Type.Wheel:
                from PyQt6.QtWidgets import QApplication
                if QApplication.activePopupWidget() is not None:
                    pass  # 後段の通常処理に任せる
                else:
                    try:
                        gpos = ev.globalPosition().toPoint()
                        ov = self._comment_overlay
                        ov_global = ov.mapToGlobal(ov.rect().topLeft())
                        ov_rect_global = QRect(ov_global, ov.size())
                        if not ov_rect_global.contains(gpos):
                            return True  # オーバーレイ外のホイールは消費
                    except Exception:
                        pass
            # Key 系: フォーカスがオーバーレイ内部にあるなら通す(編集中)。
            #         外側にフォーカスがある時のみ消費する。
            elif etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                from PyQt6.QtWidgets import QApplication
                fw = QApplication.focusWidget()
                if not self._is_inside_comment_overlay(fw):
                    return True

        MOUSE_EVENTS = (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        )
        if (ev.type() in MOUSE_EVENTS
                and hasattr(self, "_comment_overlay")
                and self._comment_overlay.isVisible()):
            # ポップアップ(QMenu 等)表示中は「外側クリック=閉じる」判定を
            # スキップする。コメント入力欄の右クリックメニュー項目を選ぶと
            # メニュー項目はオーバーレイ矩形外で発生する MouseButtonPress に
            # 見えるため、そのままだとメニュー操作のたびにオーバーレイが
            # 閉じてしまう。activePopupWidget() がある間はメニュー由来の
            # 操作とみなし素通りさせる。
            from PyQt6.QtWidgets import QApplication
            if QApplication.activePopupWidget() is not None:
                return super().eventFilter(obj, ev)
            gpos = ev.globalPosition().toPoint()
            ov_global = self._comment_overlay.mapToGlobal(
                self._comment_overlay.rect().topLeft())
            ov_rect_global = QRect(ov_global,
                                   self._comment_overlay.size())
            # コメントボタン矩形(グローバル座標): ボタン上クリックは
            # ボタン側 clicked → _toggle_comment_overlay に任せる
            btn_contains = False
            if hasattr(self, "_navbar") and hasattr(self._navbar, "btn_comment"):
                btn = self._navbar.btn_comment
                btn_global = btn.mapToGlobal(btn.rect().topLeft())
                btn_rect_global = QRect(btn_global, btn.size())
                btn_contains = btn_rect_global.contains(gpos)
            if not ov_rect_global.contains(gpos) and not btn_contains:
                # オーバーレイ外のマウス操作:
                # - MouseButtonPress: オーバーレイを閉じてイベント消費
                #   (背後のウィジェットへ伝播させない)
                # - MouseButtonRelease/DblClick: 消費しない(素通り)。
                #   QMenu の項目クリックは Release タイミングで triggered を
                #   発火するため、ここで Release を消費してしまうと選択した
                #   メニュー項目のアクションが実行されない。Press で閉じる
                #   経路と Release/DblClick で素通りする経路を分離する。
                if ev.type() == QEvent.Type.MouseButtonPress:
                    self._close_comment_overlay()
                    return True  # Press のみ消費
                # Release/DblClick はそのまま通す(QMenu の triggered を妨げない)

        # ToggleSwitchのイベントは素通り（ONにしたらOFFにできない問題の修正）
        if type(obj).__name__ == "ToggleSwitch":
            return super().eventFilter(obj, ev)
        # ② コメント欄のフォーカスアウト保存
        # _write_comment_to_node 経由で内容変化があれば dirty フラグも立てる。
        if obj is self._comment and ev.type() == QEvent.Type.FocusOut:
            if self._game_state:
                node = self._game_state.current_node
                self._write_comment_to_node(node, self._comment_textedit.toPlainText())
                self._comment.document().setModified(False)
        # ③ コメント欄以外の子ウィジェットのキー操作をメインウィンドウへ転送
        # ただしコメントオーバーレイ表示中は転送しない(オーバーレイ内 close ボタン等に
        # フォーカスがある時の矢印キー漏れを防ぐ二重ガード)。
        if ev.type() == QEvent.Type.KeyPress and obj is not self._comment:
            if (hasattr(self, "_comment_overlay")
                    and self._comment_overlay.isVisible()):
                return super().eventFilter(obj, ev)
            self.keyPressEvent(ev)
            if ev.isAccepted():
                return True
        return super().eventFilter(obj, ev)

    # フローティングパネル定数
    _FP_RATIO  = 0.30   # 情報パネル幅 = ウィンドウ幅 × この比率
    _FP_MIN_W  = 320    # 情報パネルの最小幅（ToggleBar の実用最小幅 ≈ 320px）
    _FP_MAX_W  = 460    # 情報パネルの最大幅
    _FP_MARGIN = 8      # パネルの上下マージン（縦方向）
    _FP_MARGIN_X = 0    # パネルの左右マージン（横方向。碁盤右端・画面右端との余白）
    _FP_RADIUS = 16     # 角丸

    # ── 最小ウィンドウサイズ定義 ───────────────────────────────────────
    # パネル開閉に応じて切り替える。値は理論計算から導出:
    #   - 開いた状態: (890, 650)
    #   - 閉じた状態: 890 - _FP_MIN_W = 570 幅(高さは同じ)
    # 高さ 650 から内部使用領域を引くと:
    #   avail_board_h = (650 - 36 タイトルバー) - (36 NB_HEIGHT + 8 NB_MARGIN) = 570
    # 幅 890 から右パネル分を引くと:
    #   max_board_w_by_panel = 890 - 320 (_FP_MIN_W) = 570
    # avail_board_h == max_board_w_by_panel == 570 で一致するため、
    # 最小サイズで _left_stack が 570x570 の正方形になり、パネル閉時
    # (widget = 570x570) と完全に一致する → 碁盤周辺余白が一定。
    _MIN_WIN_OPEN_W = 890
    _MIN_WIN_OPEN_H = 650

    # ── frameless ウィンドウ用のイベント処理 ──────────────────────────
    # FramelessWindowHint で OS 標準のタイトルバーを廃したため、
    # ドラッグ移動・ダブルクリック最大化・端のリサイズを自前で実装する。

    _RESIZE_BORDER = 6  # ウィンドウ端からこのピクセル以内をリサイズホットエリアとする

    # ── 最小化・閉じる・起動時のアニメーション ────────────────────────
    # ───────────────────────── 右パネル開閉 ─────────────────────────
    def mousePressEvent(self, ev):
        """frameless ウィンドウ: 端ならリサイズ、タイトルバーの空きならドラッグ。"""
        if ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.position().toPoint()
            edge = self._edge_at(pos)
            if edge is not None:
                wh = self.windowHandle()
                if wh:
                    wh.startSystemResize(edge)
                    ev.accept()
                    return
            # タイトルバー領域内かつボタン以外ならシステムドラッグ移動
            if hasattr(self, "_titlebar"):
                tb = self._titlebar
                tb_rect = tb.geometry()  # outer 内のジオメトリ
                # タイトルバーの位置(MainWindow座標系)
                if tb_rect.contains(pos):
                    # タイトルバー内座標に変換
                    local = pos - tb_rect.topLeft()
                    if not tb.hit_button_at(local):
                        wh = self.windowHandle()
                        if wh:
                            wh.startSystemMove()
                            ev.accept()
                            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        """タイトルバー空き領域のダブルクリックで最大化/復元。"""
        if ev.button() == Qt.MouseButton.LeftButton and hasattr(self, "_titlebar"):
            pos = ev.position().toPoint()
            tb = self._titlebar
            tb_rect = tb.geometry()
            if tb_rect.contains(pos):
                local = pos - tb_rect.topLeft()
                if not tb.hit_button_at(local):
                    self._toggle_maximized()
                    ev.accept()
                    return
        super().mouseDoubleClickEvent(ev)

    def mouseMoveEvent(self, ev):
        """端ホバー時にリサイズカーソルを表示する。"""
        edge = self._edge_at(ev.position().toPoint())
        if edge is None:
            self.unsetCursor()
        else:
            cursor_map = {
                Qt.Edge.LeftEdge:  Qt.CursorShape.SizeHorCursor,
                Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                Qt.Edge.TopEdge:   Qt.CursorShape.SizeVerCursor,
                Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge:     Qt.CursorShape.SizeFDiagCursor,
                Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
                Qt.Edge.RightEdge | Qt.Edge.TopEdge:    Qt.CursorShape.SizeBDiagCursor,
                Qt.Edge.LeftEdge | Qt.Edge.BottomEdge:  Qt.CursorShape.SizeBDiagCursor,
            }
            self.setCursor(cursor_map.get(edge, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(ev)

    def keyPressEvent(self, ev):
        k = ev.key()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)

        nav_keys = (
            Qt.Key.Key_Down, Qt.Key.Key_Right,
            Qt.Key.Key_Up,   Qt.Key.Key_Left,
            Qt.Key.Key_Home, Qt.Key.Key_End,
        )
        if k in nav_keys:
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Right): self._next()
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_Left):  self._prev()
            elif k == Qt.Key.Key_Home: self._goto_first()
            elif k == Qt.Key.Key_End:  self._goto_last()
            ev.accept()
        elif k == Qt.Key.Key_Backspace:
            # コメント欄にフォーカスがある場合は通常のテキスト編集に委譲
            if not self._comment.hasFocus():
                self._on_delete_node()
                ev.accept()
            else:
                super().keyPressEvent(ev)
        else:
            super().keyPressEvent(ev)

    # ホイール連続操作のスロットル設定。
    # 「先頭即時 + 後続スロットル」(leading edge + trailing edge) パターン:
    # ・直前のホイールから _WHEEL_THROTTLE_MS 以上経過していれば、_refresh_board()
    #   を **即時** 実行する(=単発ホイール・低速ホイールは遅延ゼロで反応する)
    # ・閾値未満で連続発火した場合は、_refresh_board() を遅延予約(タイマー)に
    #   切り替えて中間状態を全部間引く。連続が止まると最終位置で1回だけ実行。
    # これにより N=300 棋譜の高速ホイールでも UI が追従しつつ、
    # 単発ホイールでは遅延が発生しない。
    _WHEEL_THROTTLE_MS = 60

    def wheelEvent(self, ev):
        if self._left_stack.currentIndex() == 1:  # ウェルカム画面
            ev.ignore()
            return
        delta = ev.angleDelta().y()
        if delta < 0:
            self._wheel_step(forward=True)
        elif delta > 0:
            self._wheel_step(forward=False)

    def _wheel_step(self, forward: bool):
        """ホイール 1 段あたりの処理。
        GameState の進行・着手音は常に即時実行。
        _refresh_board() は 「先頭即時 + 後続スロットル」 で振り分け:
          ・直前のホイールから _WHEEL_THROTTLE_MS 以上経過 → 即時実行
          ・閾値未満で連続発火 → タイマー予約(中間間引き)、最終位置で1回実行
        """
        if not self._game_state:
            return
        gs = self._game_state
        # 端での発火は即終了(タイマー・状態変更ともになし)
        if forward:
            if not gs.current_node.children:
                return
        else:
            if gs.current_node.parent is None:
                return
        # コメント編集中なら保存(キーボード操作の _next/_prev と同じ扱い)
        self._save_comment_if_editing(gs.current_node)
        # 状態進行 + 着手音は即時(ホイール連続時にも音は鳴らしておく)
        if forward:
            cap_before = (gs._black_captures + gs._white_captures)
            gs.forward()
            cap_after = (gs._black_captures + gs._white_captures)
            if cap_after > cap_before:
                self._sound.play_capture()
            elif gs.current_node.move_color:
                self._sound.play_place()
        else:
            gs.backward()

        # ── _refresh_board の「先頭即時 + 後続スロットル」振り分け ──
        # 初回呼び出し用にタイマーを準備
        if not hasattr(self, "_wheel_refresh_timer") or self._wheel_refresh_timer is None:
            from PyQt6.QtCore import QTimer
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._wheel_trailing_refresh)
            self._wheel_refresh_timer = t
        if not hasattr(self, "_wheel_last_refresh_t"):
            self._wheel_last_refresh_t = 0.0

        now_t = time.monotonic()
        elapsed_ms = (now_t - self._wheel_last_refresh_t) * 1000.0
        if elapsed_ms >= self._WHEEL_THROTTLE_MS:
            # 先頭(または十分間隔があいた次の回): 即時実行
            # 通常の _refresh_board() を呼ぶ(skip_ponder=False)。 これにより、
            # 末尾で _start_pondering_current() が走って ponder が即時開始される。
            # 既解析の手に進んだ場合は、 _refresh_board 内で _node_analyses から
            # ma を取得して set_position(stones, ma.best_moves, ...) として
            # 候補手が即時表示される。
            # 連続ホイール時の中間結果は _on_ponder_result の node 不一致チェック
            # (L11627) で捨てられるため、 ponder の起動コスト(IPC ~1.5ms)以外の
            # オーバーヘッドはない。
            self._wheel_last_refresh_t = now_t
            self._refresh_board()
        else:
            # 連続発火中: 最終位置で1回だけ実行するようタイマー予約
            self._wheel_refresh_timer.start(self._WHEEL_THROTTLE_MS)

    def _wheel_trailing_refresh(self):
        """ホイールスロットル中の最終 _refresh_board 実行(タイマー発火経由)。
        ここで ponder を開始する(skip_ponder=False、 デフォルト)。
        次の単発ホイールが直後に来たときに即時実行と誤判定しないよう、
        _wheel_last_refresh_t をここでも更新する。
        """
        self._wheel_last_refresh_t = time.monotonic()
        # 末尾は通常の _refresh_board(=skip_ponder=False) で ponder 再開
        self._refresh_board()


if __name__=="__main__":
    from gui.startup import main
    main()
