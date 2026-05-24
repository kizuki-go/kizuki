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
)
from gui.widgets.board import BoardWidget, BoardContainer
from gui.widgets.graph import ScoreLeadAxis, IntegerBottomAxis, _GraphLabelOverlay, WinRateGraph
from gui.widgets.titlebar import _CustomTitleBar

logger = logging.getLogger(__name__)


def style_qmenu(qmenu, leaf: bool = False) -> None:
    """QMenu に共通スタイル(menu_qss)と透過化設定を適用する。
    透過化により、border-radius の外側がアプリ背景に透けて
    「ポップアップ枠外の塗り残し」が解消される。
    タイトルバーメニュー・コンテキストメニューの両方で使用する共通処理。

    leaf=True を指定すると objectName="leaf_menu" がセットされ、
    リーフメニュー(サブメニューを持たない、選択肢だけのメニュー)向けに
    padding-right を狭めた QSS が適用される(menu_qss 側で対応)。

    サブメニューの位置補正(親メニューに重ねず右隣に開く)は
    _install_submenu_positioner() で eventFilter を仕込んで行う。
    """
    if leaf:
        qmenu.setObjectName("leaf_menu")
    qmenu.setStyleSheet(menu_qss())
    qmenu.setWindowFlags(
        qmenu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    qmenu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # サブメニュー位置補正フィルタを仕込む(自身がサブメニューとして開く時の補正)
    _install_submenu_positioner(qmenu)


# ── サブメニュー位置補正 ───────────────────────────────────────────────
# Qt の QMenu はサブメニューを開く時、デフォルトでは親メニューに少し重ねる
# (PM_SubMenuOverlap)。これを QSS や QProxyStyle で補正しようとしたが、
# 環境依存で確実には効かなかったため、Show イベントを eventFilter で捕捉
# してサブメニューの位置を「親メニューの右端 + 数px」に強制移動する方式を
# 採用する。副作用なく確実に効く。
class _SubMenuPositioner(QObject):
    """QMenu の Show イベントを捕捉し、自身が他の QMenu の子として開かれる
    場合 (= サブメニュー時) のみ、親メニューに重ならない位置に補正する。
    親が QMenu でない場合 (= トップレベルメニュー) は何もしない。
    """
    GAP_PX = 1  # 親メニューの右端から1px = ぴったり接続(QRect.right()は最右ピクセル座標を返すので+1で隣接)

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.Type.Show:
                from PyQt6.QtWidgets import QMenu
                if isinstance(obj, QMenu):
                    parent = obj.parent()
                    # 親が QMenu の場合のみ補正(サブメニュー扱い)
                    if isinstance(parent, QMenu) and parent.isVisible():
                        # 親メニューで現在アクティブな項目(=このサブメニューを
                        # 開いた項目)の矩形を取得
                        action = parent.activeAction()
                        if action is not None:
                            item_rect = parent.actionGeometry(action)
                            # アイテムのグローバル左上 = 親メニューの mapToGlobal(item_rect.topLeft())
                            global_top_left = parent.mapToGlobal(item_rect.topLeft())
                            # サブメニューを「親メニュー右端 + GAP, アイテムY」に配置
                            new_x = parent.geometry().right() + self.GAP_PX
                            new_y = global_top_left.y()
                            obj.move(new_x, new_y)
        except Exception:
            pass
        return False


# シングルトン: モジュールレベルで保持し、ガベージコレクト回避
_submenu_positioner_instance = None

def _install_submenu_positioner(qmenu) -> None:
    """指定 QMenu に _SubMenuPositioner をインストールする。
    シングルトンで保持(複数 QMenu に同じインスタンスを使い回す)。
    """
    global _submenu_positioner_instance
    if _submenu_positioner_instance is None:
        _submenu_positioner_instance = _SubMenuPositioner()
    qmenu.installEventFilter(_submenu_positioner_instance)

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

# ナビバーの戻る/進むボタン用 角丸三角アイコン
# fill された三角形に同色の stroke を被せ、stroke-linejoin="round" で角を丸める。
# stroke-width を控えめにして、丸み具合は柔らかく。
_SVG_NAV_PREV = (  # ◀ (左向き)
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path d="M11 3 L11 13 L4 8 Z" '
    'fill="{{color}}" stroke="{{color}}" stroke-width="1.6" '
    'stroke-linejoin="round" stroke-linecap="round"/>'
    '</svg>'
)
_SVG_NAV_NEXT = (  # ▶ (右向き)
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path d="M5 3 L5 13 L12 8 Z" '
    'fill="{{color}}" stroke="{{color}}" stroke-width="1.6" '
    'stroke-linejoin="round" stroke-linecap="round"/>'
    '</svg>'
)


# ── Score board widget ──────────────────────────────────────────────────────
class ScoreBoard(QWidget):
    """
    アゲハマ・プレイヤー名・目差・勝率バーをまとめて表示するウィジェット。
    レイアウト：
      [ ●アゲハマ ]  [ ● 黒名  vs  白名 ○ ]  [ ○アゲハマ ]
                          目差: +X.X 目
               [■■■■■■■■░░░░░░░] 勝率バー
    """
    # 勝率バーアニメーション設定
    _WINRATE_ANIM_DURATION_MS = 500  # アニメ時間(ms)
    # ルール/コミ表示行のクロスフェード時間 (_CrossFadeLabel と同じ 250ms)
    _INFO_FADE_DURATION_MS = 250

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self._black_name = "—"
        self._white_name = "—"
        self._komi = 6.5
        self._rules = ""
        self._black_cap = 0
        self._white_cap = 0
        self._winrate = 0.5          # 目標値(setter で更新)
        self._winrate_display = 0.5  # 表示値(アニメで補間、paintEvent で参照)
        self._winrate_anim = None    # QVariantAnimation(遅延生成)
        self._score_lead = None
        # ── ルール/コミ表示行のクロスフェード用 ─────────────────
        # update_game_info でルール or コミが変わると、旧テキストは
        # フェードアウトしながら新テキストがフェードインする(同位置で
        # 重ね描画、合計 250ms / OutCubic)。_CrossFadeLabel と同じ流儀。
        self._info_text_cur: str = self._compute_info_text(self._rules, self._komi)
        self._info_text_old: str = ""
        self._info_fade_t: float = 1.0  # 1.0=完了(旧不可視)、0.0=遷移開始
        self._info_fade_anim = None     # QVariantAnimation(遅延生成)
        # 案1(碁石+アゲハマ統合)レイアウト(SP 4pxグリッド準拠):
        # Y1=SP_LG(16) + ROW_H=SP_XL(24) + SP_MD(12) + BH=SP_XL(24)
        # + SP_MD(12) + rule=SP_LG(16) + bottom=SP_LG(16) = 120
        self.setFixedHeight(120)
        # ── 静的レイヤの pixmap キャッシュ ────────────────────────
        # 行1(プレイヤー名+石+アゲハマ)と行3(ルール+コミ)はアゲハマ・名前・
        # ルール・コミが変わらない限り描画結果が同一。これらを pixmap として
        # 1回だけ描画しておき、勝率アニメ中などの頻繁な paint では drawPixmap
        # で済ませる。動的な行2(勝率バー)のみ毎回描画する。
        # キャッシュキー: (W, H, dpr, _black_cap, _white_cap, _black_name,
        # _white_name, _info_text_cur, テーマ識別)
        # フェードアニメ中(_info_fade_t < 1.0)はキャッシュ無効化(行3 が変動するため)
        self._static_pm = None       # type: Optional[QPixmap]
        self._static_pm_key = None   # type: Optional[tuple]

    @staticmethod
    def _compute_info_text(rules: str, komi: float) -> str:
        """ルール/コミから表示用テキストを生成する。
        paintEvent と update_game_info の両方から呼ばれる(DRY)。"""
        rule_map = {
            "japanese":     "日本ルール",
            "chinese":      "中国ルール",
            "korean":       "韓国ルール",
            "aga":          "AGAルール",
            "tromp-taylor": "Tromp-Taylorルール",
            "new-zealand":  "ニュージーランドルール",
        }
        rule_str = rule_map.get(rules.lower(), rules) if rules else ""
        if rule_str:
            return f"{rule_str}  /  コミ {komi}"
        return f"コミ {komi}"

    def _start_info_fade_anim(self):
        """ルール/コミ行のクロスフェードを開始する。"""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if self._info_fade_anim is None:
            anim = QVariantAnimation(self)
            anim.setDuration(self._INFO_FADE_DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            def _on_changed(v):
                with _profile("ScoreBoard.anim.info_fade"):
                    try:
                        self._info_fade_t = float(v)
                    except (TypeError, ValueError):
                        return
                    self.update()
            def _on_finished():
                self._info_fade_t = 1.0
                self._info_text_old = ""
                self.update()
            anim.valueChanged.connect(_on_changed)
            anim.finished.connect(_on_finished)
            self._info_fade_anim = anim
        anim = self._info_fade_anim
        anim.stop()
        # 遷移中の連続変更にも対応: 現在の補間値を起点にせず、
        # 旧→新の標準遷移(0→1)として再開する(_CrossFadeLabel と同じ流儀)。
        self._info_fade_t = 0.0
        anim.start()

    def set_player_names(self, black: str, white: str):
        """対局者名を直接設定する。"""
        self._black_name = black
        self._white_name = white
        self.update()

    def update_game_info(self, game, *, rules: str = "", komi: float = 6.5):
        """対局情報を更新。プレイヤー名は SGF から、コミ・ルールは
        外から渡された値（= ソフト側設定値）をそのまま使う。
        SGF の KM/RU は嘘が多いため信頼しない。
        ルール or コミが変わった時はルール/コミ表示行をクロスフェードする。"""
        if not game:
            self._black_name = "—"
            self._white_name = "—"
            self._komi = komi
            self._rules = rules
        else:
            self._black_name = game.player_black or "黒"
            self._white_name = game.player_white or "白"
            self._komi = komi
            self._rules = rules
        # ── ルール/コミ表示テキストの更新とクロスフェード起動 ──
        new_info = self._compute_info_text(self._rules, self._komi)
        if new_info != self._info_text_cur:
            self._info_text_old = self._info_text_cur
            self._info_text_cur = new_info
            self._start_info_fade_anim()
        self.update()

    def update_captures(self, black_cap: int, white_cap: int):
        self._black_cap = black_cap
        self._white_cap = white_cap
        self.update()

    def update_analysis(self, win_rate: float, score_lead: float):
        """ポンダリング結果で勝率バーと目差を更新する。"""
        self._score_lead = score_lead
        self._set_winrate_target(win_rate)

    def update_winrate(self, wr: float, score_lead):
        self._score_lead = score_lead
        self._set_winrate_target(wr)

    def set_winrate(self, wr, *args):
        self._set_winrate_target(wr)

    # 勝率の小変化しきい値: これ未満の変化はアニメ起動せず即時反映する。
    # ポンダリング中は 0.1〜1% の小変動が高頻度(4〜10回/秒)で来るが、
    # 毎回 300ms QVariantAnimation を再起動すると、アニメが完了せずに
    # 永続的に valueChanged(60fps) で update() が発火し続ける問題があった。
    # しきい値未満の変化は瞬時反映 + update() 1回だけにすることで、
    # 60fps の常時再描画を 4〜10fps の必要時のみに削減できる。
    # ノードジャンプ等で勝率が大きく変わる時(>= しきい値)は従来通りアニメ起動。
    _WINRATE_ANIM_THRESHOLD = 0.01  # 1% (0.01)

    @_profile_method("ScoreBoard.set_target")
    def _set_winrate_target(self, wr: float):
        """目標値 self._winrate を更新し、表示値 self._winrate_display を
        QVariantAnimation で滑らかに補間する。
        既にアニメ中なら、現在の表示値を起点に新しい目標値へ向けて再開する。

        小変化(< _WINRATE_ANIM_THRESHOLD)はアニメ起動せず瞬時反映する。
        ポンダリング中の頻繁な小更新による60fps常時再描画を防ぐため。
        """
        try:
            wr = max(0.0, min(1.0, float(wr)))
        except (TypeError, ValueError):
            return
        self._winrate = wr

        # ── 小変化の早期短絡: アニメ起動せず瞬時反映 ──
        # ポンダリング中は 0.1〜1% の小変動が 4〜10回/秒で来る。これを毎回
        # 300ms アニメで処理すると、アニメが完了する前に必ず再起動され、
        # 結果として永続的に valueChanged(60fps) が発火し続けて常時再描画と
        # なる(ScoreBoard.paint が 60回/秒で 60ms/秒の負荷を作る)。
        #
        # 解決策: 値変化が小さい場合は両ケース短絡する。
        #   - アニメ非進行中 → 表示値を即時更新 + update() 1回(60fps→4〜10fps)
        #   - アニメ進行中 → 既存アニメの目標値だけ更新する(アニメ再起動しない)
        # 大きく変わる時(ノードジャンプ等)だけ従来通り 300ms アニメ起動。
        delta = abs(wr - self._winrate_display)
        anim_running = (self._winrate_anim is not None
                        and self._winrate_anim.state()
                        == self._winrate_anim.State.Running)
        if delta < self._WINRATE_ANIM_THRESHOLD:
            if anim_running:
                # 既存アニメ続行: 目標値だけ静かに更新する。
                # 次の valueChanged で _winrate_display が新目標へ向けて補間される。
                # ※ setEndValue だけでは現在の補間カーブが微妙に変わる可能性があるが、
                # 0.5%未満の差なら視覚的に検知できないので問題なし。
                self._winrate_anim.setEndValue(float(wr))
            else:
                # アニメ非進行: 瞬時反映 + 1回だけ再描画
                self._winrate_display = wr
                self.update()
            return

        # アニメを遅延生成(初回呼び出し時のみ)
        if self._winrate_anim is None:
            from PyQt6.QtCore import QVariantAnimation, QEasingCurve
            anim = QVariantAnimation(self)
            anim.setDuration(self._WINRATE_ANIM_DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            def _on_value_changed(v):
                with _profile("ScoreBoard.anim.winrate"):
                    try:
                        self._winrate_display = float(v)
                    except (TypeError, ValueError):
                        return
                    self.update()
            anim.valueChanged.connect(_on_value_changed)
            self._winrate_anim = anim

        anim = self._winrate_anim
        # 進行中なら停止して、現在の表示値から再アニメ
        anim.stop()
        anim.setStartValue(float(self._winrate_display))
        anim.setEndValue(float(wr))
        anim.start()

    def _draw_capture_centered(self, p: QPainter, cx: float, cy: float,
                                text: str, font: QFont, color: QColor) -> None:
        """指定座標 (cx, cy) を中心としてアゲハマ数字テキストを描画する。

        QPainter.drawText は Windows DirectWrite の字形別ヒンティングで
        グリフごとに 1〜2px の描画位置ずれが起きる(例: 「0」と「3」を
        切り替えると Y 位置がわずかに変わる)。これを回避するため、
        テキストを QPainterPath にベクター変換してから fillPath で塗り
        つぶす。パスはピクセルグリッドに整数化されないため、字形に依らず
        一定の中央揃えになる。

        Args:
            p:    既に begin 済みの QPainter (Antialiasing 推奨)
            cx:   テキスト中央 X (= 石の中心 X)
            cy:   テキスト中央 Y (= 石の中心 Y)
            text: 描画するテキスト (アゲハマ数字)
            font: 使用フォント
            color: 塗り色
        """
        from PyQt6.QtGui import QPainterPath
        # baseline (0,0) にテキストパスを構築 → bounding rect を取って
        # その中央が (cx, cy) に来るよう offset を付けてパス全体を移動。
        path = QPainterPath()
        path.addText(0, 0, font, text)
        br = path.boundingRect()
        # br.center() を (cx, cy) に持っていく
        offset_x = cx - (br.left() + br.width() / 2)
        offset_y = cy - (br.top() + br.height() / 2)
        path.translate(offset_x, offset_y)
        # AA を有効にして塗りつぶし
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
        p.restore()

    def _build_static_pixmap(self, W: int, H: int, dpr: float) -> QPixmap:
        """ScoreBoard の静的レイヤ(行1: プレイヤー名+石+アゲハマ、行3: ルール+コミ)
        を pixmap として生成する。フェードアニメ中は呼ばれない(動的)。
        実行内容は paintEvent の対応箇所と完全に同一(コピー元)。
        """
        from PyQt6.QtGui import QFontMetrics, QPainterPath
        pm = QPixmap(int(round(W * dpr)), int(round(H * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        PAD     = SP_MD
        BAR_W   = W - PAD * 2
        ROW_H   = SP_XL
        Y1      = SP_LG
        HALF    = W // 2
        STONE_DIA = 22
        STONE_R   = STONE_DIA // 2
        STONE_TO_NAME_GAP = SP_SM

        fm_name = QFontMetrics(Font_MD(True))
        fm_cap_in_stone = QFontMetrics(Font_XS(True))
        CY = Y1 + ROW_H / 2
        name_baseline = CY + fm_name.ascent() / 2 - fm_name.descent() / 2

        # ── 黒(左半分) ──
        black_stone_cx = PAD + STONE_R
        p.setBrush(QBrush(T().STONE_BLACK))
        p.setPen(QPen(T().STONE_BORDER_BLACK, 1))
        p.drawEllipse(QPointF(black_stone_cx, CY), STONE_R, STONE_R)
        black_cap_text = str(self._black_cap)
        # アゲハマ数字を石の中心に揃える。
        # drawText は Windows DirectWrite のグリフ別ヒンティングで字形に
        # よって 1-2px 描画位置がズレるため、QPainterPath にテキストを
        # 変換して fillPath で塗りつぶす方式に統一する。これによりベクター
        # ベースの正確な配置が可能で、字形に依らない安定した中央揃えに
        # なる。
        self._draw_capture_centered(
            p, black_stone_cx, CY, black_cap_text,
            Font_XS(True), QColor(255, 255, 255),
        )

        x_name = PAD + STONE_DIA + STONE_TO_NAME_GAP
        max_name_w = HALF - x_name - SP_SM
        black_name = fm_name.elidedText(
            self._black_name, Qt.TextElideMode.ElideRight, max(max_name_w, 20))
        p.setFont(Font_MD(True))
        p.setPen(QPen(T().TEXT))
        p.drawText(QPointF(x_name, name_baseline), black_name)

        # ── 白(右半分) ──
        white_stone_cx = W - PAD - STONE_R
        p.setBrush(QBrush(QColor("#ffffff")))
        p.setPen(QPen(T().STONE_BORDER_WHITE, 1))
        p.drawEllipse(QPointF(white_stone_cx, CY), STONE_R, STONE_R)
        white_cap_text = str(self._white_cap)
        # 黒側と同じく QPainterPath ベースで中央揃え。
        self._draw_capture_centered(
            p, white_stone_cx, CY, white_cap_text,
            Font_XS(True), T().STONE_BLACK,
        )

        x_name_right = white_stone_cx - STONE_R - STONE_TO_NAME_GAP
        max_name_w = int(x_name_right - (HALF + SP_XS))
        white_name = fm_name.elidedText(
            self._white_name, Qt.TextElideMode.ElideRight, max(max_name_w, 20))
        actual_white_w = fm_name.horizontalAdvance(white_name)
        white_name_x = x_name_right - actual_white_w
        p.setFont(Font_MD(True))
        p.setPen(QPen(T().TEXT))
        p.drawText(QPointF(white_name_x, name_baseline), white_name)

        # ── 行3: ルール + コミ(中央揃え) ──
        # フェードアニメ非中(_info_fade_t == 1.0)のときのみキャッシュ対象。
        BH = SP_XL
        Y2 = Y1 + ROW_H + SP_MD
        Y3 = Y2 + BH + SP_MD
        p.setFont(Font_SM())
        p.setPen(QPen(QColor(T().TEXT2)))
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        p.drawText(PAD, Y3, BAR_W, SP_LG, align, self._info_text_cur)

        p.end()
        return pm

    @_profile_method("ScoreBoard.paint")
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        H = self.height()
        PAD     = SP_MD   # 12: 左右パディング
        BAR_W   = W - PAD * 2
        BH      = SP_XL   # 24: 勝率バーの高さ
        ROW_H   = SP_XL
        Y1      = SP_LG
        Y2      = Y1 + ROW_H + SP_MD

        info_text = self._info_text_cur
        # フェードアニメ中は静的キャッシュは使わず、行1/行3 もインライン描画する。
        fade_active = (self._info_fade_t < 1.0 and bool(self._info_text_old))

        # ── 静的レイヤ(行1: プレイヤー名+石、行3: ルール+コミ)を pixmap で描画 ──
        if not fade_active:
            dpr = float(self.devicePixelRatioF())
            # テーマインスタンスは set_mode で内部状態が変わるだけで id() は
            # 不変なので、is_dark を含めてキーにする(モード切替時に再生成)。
            theme_id = (id(T()), T().is_dark)
            cache_key = (
                W, H, round(dpr, 3),
                self._black_cap, self._white_cap,
                self._black_name, self._white_name,
                self._info_text_cur,
                theme_id,
            )
            if self._static_pm is None or self._static_pm_key != cache_key:
                self._static_pm = self._build_static_pixmap(W, H, dpr)
                self._static_pm_key = cache_key
            p.drawPixmap(0, 0, self._static_pm)
        else:
            # フェードアニメ中: 行1 はキャッシュ可能だが、簡潔さのためインラインで全部描く
            # (アニメ持続時間 250ms と短いので影響は限定的)
            from PyQt6.QtGui import QFontMetrics
            HALF  = W // 2
            STONE_DIA = 22
            STONE_R   = STONE_DIA // 2
            STONE_TO_NAME_GAP = SP_SM
            fm_name = QFontMetrics(Font_MD(True))
            fm_cap_in_stone = QFontMetrics(Font_XS(True))
            CY = Y1 + ROW_H / 2
            name_baseline = CY + fm_name.ascent() / 2 - fm_name.descent() / 2

            # ── 黒(左半分) ──
            black_stone_cx = PAD + STONE_R
            p.setBrush(QBrush(T().STONE_BLACK))
            p.setPen(QPen(T().STONE_BORDER_BLACK, 1))
            p.drawEllipse(QPointF(black_stone_cx, CY), STONE_R, STONE_R)
            black_cap_text = str(self._black_cap)
            # アゲハマ数字を石の中心に揃える(_build_static_pixmap と同じロジック)
            self._draw_capture_centered(
                p, black_stone_cx, CY, black_cap_text,
                Font_XS(True), QColor(255, 255, 255),
            )
            x_name = PAD + STONE_DIA + STONE_TO_NAME_GAP
            max_name_w = HALF - x_name - SP_SM
            black_name = fm_name.elidedText(
                self._black_name, Qt.TextElideMode.ElideRight, max(max_name_w, 20))
            p.setFont(Font_MD(True))
            p.setPen(QPen(T().TEXT))
            p.drawText(QPointF(x_name, name_baseline), black_name)

            # ── 白(右半分) ──
            white_stone_cx = W - PAD - STONE_R
            p.setBrush(QBrush(QColor("#ffffff")))
            p.setPen(QPen(T().STONE_BORDER_WHITE, 1))
            p.drawEllipse(QPointF(white_stone_cx, CY), STONE_R, STONE_R)
            white_cap_text = str(self._white_cap)
            self._draw_capture_centered(
                p, white_stone_cx, CY, white_cap_text,
                Font_XS(True), T().STONE_BLACK,
            )
            x_name_right = white_stone_cx - STONE_R - STONE_TO_NAME_GAP
            max_name_w = int(x_name_right - (HALF + SP_XS))
            white_name = fm_name.elidedText(
                self._white_name, Qt.TextElideMode.ElideRight, max(max_name_w, 20))
            actual_white_w = fm_name.horizontalAdvance(white_name)
            white_name_x = x_name_right - actual_white_w
            p.setFont(Font_MD(True))
            p.setPen(QPen(T().TEXT))
            p.drawText(QPointF(white_name_x, name_baseline), white_name)

        # ── 行2: 勝率バー(動的、毎回描画) ──
        p.setBrush(QBrush(T().PANEL2)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(PAD, Y2, BAR_W, BH, 4, 4)

        bw = int(BAR_W * self._winrate_display)
        ww = BAR_W - bw

        from PyQt6.QtGui import QPainterPath
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(PAD, Y2, BAR_W, BH), 4, 4)
        p.save()
        p.setClipPath(clip_path)

        if bw > 0:
            p.setBrush(QBrush(T().STONE_BLACK))
            p.drawRect(PAD, Y2, bw, BH)
        if ww > 0:
            p.setBrush(QBrush(T().WINRATE_WHITE))
            p.drawRect(PAD + bw, Y2, ww, BH)

        p.restore()

        from PyQt6.QtGui import QFontMetrics
        f_num  = FontMono_LG(True)
        f_unit = FontMono_SM(True)
        fm_num  = QFontMetrics(f_num)
        fm_unit = QFontMetrics(f_unit)
        baseline_offset = (BH + fm_num.ascent() - fm_num.descent()) // 2
        SIDE_MARGIN = 6
        unit_w = fm_unit.horizontalAdvance("%")

        num_str_b = f"{self._winrate_display*100:.1f}"
        num_w_b   = fm_num.horizontalAdvance(num_str_b)
        needed_b  = num_w_b + unit_w + SIDE_MARGIN * 2
        if bw >= needed_b:
            p.setPen(QPen(QColor(255,255,255)))
            x0 = PAD + SIDE_MARGIN
            p.setFont(f_num)
            p.drawText(x0, Y2 + baseline_offset, num_str_b)
            p.setFont(f_unit)
            p.drawText(x0 + num_w_b, Y2 + baseline_offset, "%")

        num_str_w = f"{(1-self._winrate_display)*100:.1f}"
        num_w_w   = fm_num.horizontalAdvance(num_str_w)
        needed_w  = num_w_w + unit_w + SIDE_MARGIN * 2

        if ww >= needed_w:
            wbar_text = QColor(30,30,30) if T().is_dark else QColor(51,51,51)
            p.setPen(QPen(wbar_text))
            x0 = PAD + bw + (ww - SIDE_MARGIN) - (num_w_w + unit_w)
            p.setFont(f_num)
            p.drawText(x0, Y2 + baseline_offset, num_str_w)
            p.setFont(f_unit)
            p.drawText(x0 + num_w_w, Y2 + baseline_offset, "%")

        # ── 行3: ルール + コミ(フェードアニメ中のみインライン描画) ──
        # アニメ非中は静的レイヤキャッシュに含まれているので何もしない。
        if fade_active:
            Y3 = Y2 + BH + SP_MD
            p.setFont(Font_SM())
            text_color = QColor(T().TEXT2)
            align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            rect_args = (PAD, Y3, BAR_W, SP_LG)
            t = max(0.0, min(1.0, float(self._info_fade_t)))
            old_color = QColor(text_color)
            old_color.setAlphaF(text_color.alphaF() * (1.0 - t))
            p.setPen(QPen(old_color))
            p.drawText(*rect_args, align, self._info_text_old)
            new_color = QColor(text_color)
            new_color.setAlphaF(text_color.alphaF() * t)
            p.setPen(QPen(new_color))
            p.drawText(*rect_args, align, info_text)

        p.end()


def _make_card(title: str = "", badge_widget=None) -> tuple:
    """
    統一カードウィジェットを生成する。
    戻り値: (card_widget, body_widget)
    card_widget: border + radius 付きの外枠
    body_widget: コンテンツを addWidget する先
    """
    card = QWidget()
    card.setStyleSheet(
        f"QWidget#card {{"
        f"  background:{T().PANEL.name()};"
        f"  border:1px solid {T().BORDER2.name()};"
        f"  border-radius:12px;"
        f"}}"
    )
    card.setObjectName("card")
    vl = QVBoxLayout(card)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(0)

    if title:
        hdr = QWidget()
        hdr.setObjectName("card_hdr")
        hdr.setStyleSheet(
            f"QWidget#card_hdr {{"
            f"  background:{T().PANEL2.name()};"
            f"  border-bottom:1px solid {T().BORDER2.name()};"
            f"  border-top-left-radius:8px;"
            f"  border-top-right-radius:8px;"
            f"}}"
        )
        hdr.setFixedHeight(28)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(SP_MD, 0, SP_MD, 0)
        lbl = QLabel(title.upper())
        lbl.setFont(Font_MD())
        lbl.setStyleSheet(
            f"color:{T().TEXT2.name()}; letter-spacing:1px; background:transparent;"
        )
        hl.addWidget(lbl)
        if badge_widget:
            hl.addStretch()
            hl.addWidget(badge_widget)
        vl.addWidget(hdr)

    body = QWidget()
    body.setStyleSheet("background:transparent;")
    vl.addWidget(body)

    return card, body


# ── Right info panel ─────────────────────────────────────────────────────────
class InfoPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── スコアボードカード ──
        score_card, score_body = _make_card()
        self._scoreboard = ScoreBoard()
        bl = QVBoxLayout(score_body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._scoreboard)
        layout.addWidget(score_card)

        layout.addSpacing(SP_MD)

        # ── 目差グラフカード ──
        graph_card, graph_body = _make_card()
        self._graph = WinRateGraph()
        gl = QVBoxLayout(graph_body)
        gl.setContentsMargins(*PAD_CARD)
        gl.setSpacing(0)
        gl.addWidget(self._graph)
        layout.addWidget(graph_card, stretch=1)

    def update_game_info(self, game, *, rules: str = "", komi: float = 6.5):
        self._scoreboard.update_game_info(game, rules=rules, komi=komi)

    def update_captures(self, black_cap: int, white_cap: int):
        self._scoreboard.update_captures(black_cap, white_cap)

    def get_graph(self):
        return self._graph

    def update_analysis(self, ma: Optional[MoveAnalysis], sgf_comment: str = ""):
        if ma is None:
            return
        # win_rate は「打つ前の黒視点勝率」= そのノードでの局面評価
        self._scoreboard.update_winrate(ma.win_rate, ma.score_lead)

    def apply_theme(self):
        """テーマ切り替え時に InfoPanel とカード背景を再適用する。"""
        # _make_card で生成したカードは ObjectName "card" を持つ
        for card in self.findChildren(QWidget):
            if card.objectName() == "card":
                card.setStyleSheet(
                    f"QWidget#card{{"
                    f"background:{T().PANEL.name()};"
                    f"border:1px solid {T().BORDER2.name()};"
                    f"border-radius:12px;}}"
                )
        self._scoreboard.update()
        if hasattr(self, "_graph"):
            self._graph.update_theme()

class MetricLabel(QWidget):
    """
    数値と単位を同一ベースラインで描画するカスタムウィジェット。
    QPainter で直接描画することでフォントサイズ混在のズレを防ぐ。

    set_value() で値が変わると、数値同士の遷移は QVariantAnimation で
    カウントアップ風に補間される(ハイフン ↔ 数値の遷移は補間不可なので
    瞬時切替)。アニメ時間 300ms / OutCubic は WinRateGraph と同期。
    """
    # 数値カウントアップアニメの時間 (ms)
    _NUM_ANIM_DURATION_MS = 300

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self._num_text  = "—"
        self._unit_text = ""
        self._color     = T().TEXT2
        # カテゴリ依存色を再計算するための情報を保持(テーマ切替・色調整時に追従)
        self._category  = None  # "best"/"good"/"inaccuracy"/"mistake"/"blunder" or None
        # 数値: Font_XXL (28px太字)、単位: Font_MD (16px太字)。
        # ピルラベル(Font_XS=12px)とのコントラストを大きくし、勝率変化・
        # 目差変化の数値を視覚的に強調する。
        self._num_font  = Font_XXL(True)
        self._unit_font = Font_MD(True)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # 数値補間アニメ
        self._num_anim = None         # QVariantAnimation(遅延生成)
        self._num_anim_start = 0.0    # アニメ開始時の数値
        self._num_anim_end   = 0.0    # アニメ目標値

    @staticmethod
    def _try_parse_signed_float(s: str):
        """'+12.3' / '-0.5' / '0.0' / '±0.0' のような表示文字列を float に変換する。
        パースできなければ None を返す(ハイフン等)。"""
        if not isinstance(s, str):
            return None
        st = s.strip()
        if not st or st == "—":
            return None
        # 先頭の '+' / '±' は float() がそのままでは受け付けないため除去
        # ('±0.0' は便宜上 0.0 として解釈する)
        if st.startswith("+") or st.startswith("\u00b1"):
            st = st[1:]
        try:
            return float(st)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_signed_float(v: float) -> str:
        """補間中の数値を set_value で渡されるのと同じ書式で文字列化する。
        update_card 側の符号付け処理と一致させる:
          - 四捨五入後 0.0(0.0/-0.0 とも)        → "±0.0"
          - 正値                                  → "+X.X"
          - 負値                                  → "-X.X" (f-string で自動)
        """
        rounded = round(v, 1)
        # round(-0.0, 1) は -0.0 を返すので、abs() で +0.0 と等価判定
        if abs(rounded) < 1e-9:
            return "\u00b10.0"
        sign = "+" if rounded > 0 else ""  # 負値は f-string で自動的に '-' が付く
        return f"{sign}{rounded:.1f}"

    def set_value(self, num_text: str, unit_text: str, color, category=None):
        """数値・単位・色を一括設定。
        category を渡すと、後続の update_theme() でテーマ依存色を再計算可能。
        旧値と新値が両方とも数値の場合はカウントアップアニメで補間する。
        ハイフンが絡む場合は瞬時切替。
        """
        # 単位・色・カテゴリは即時反映(これらをアニメすると挙動が複雑になる)
        self._unit_text = unit_text
        self._color     = QColor(color) if isinstance(color, str) else color
        self._category  = category

        old_v = self._try_parse_signed_float(self._num_text)
        new_v = self._try_parse_signed_float(num_text)

        if old_v is not None and new_v is not None and old_v != new_v:
            # 数値 → 数値: アニメで補間
            self._start_num_anim(old_v, new_v)
        else:
            # ハイフン絡み or 同値: 瞬時切替
            if self._num_anim is not None:
                self._num_anim.stop()
            self._num_text = num_text
            self.update()

    def _start_num_anim(self, start_v: float, end_v: float):
        if self._num_anim is None:
            from PyQt6.QtCore import QVariantAnimation, QEasingCurve
            anim = QVariantAnimation(self)
            anim.setDuration(self._NUM_ANIM_DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.valueChanged.connect(self._on_num_anim_value_changed)
            self._num_anim = anim
        self._num_anim_start = float(start_v)
        self._num_anim_end   = float(end_v)
        self._num_anim.stop()
        self._num_anim.start()

    def _on_num_anim_value_changed(self, t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            return
        v = self._num_anim_start + (self._num_anim_end - self._num_anim_start) * t
        self._num_text = self._format_signed_float(v)
        self.update()

    def update_theme(self):
        """テーマ切替時にハイフン表示中なら TEXT2 に、数値表示中なら
        カテゴリ依存色を再計算して反映する(ColorAdjustmentDialog で
        色を変えた際にもこの経路で追従させる)。
        """
        if self._num_text == "—":
            self._color = T().TEXT2
            self.update()
            return
        # 数値表示中: カテゴリ依存色を再計算
        if self._category is not None:
            if not T().is_dark:
                # ライト: Theme.BLUNDER
                c = T().BLUNDER.get(self._category)
                if c is not None:
                    self._color = c
                    self.update()
                    return
            else:
                # ダーク: EVAL_COLORS["text_dark_mode"]
                v = EVAL_COLORS.get(self._category)
                if v is not None:
                    self._color = QColor(v["text_dark_mode"])
                    self.update()
                    return

    def sizeHint(self):
        from PyQt6.QtGui import QFontMetrics
        fm_num  = QFontMetrics(self._num_font)
        fm_unit = QFontMetrics(self._unit_font)
        w = fm_num.horizontalAdvance(self._num_text) + fm_unit.horizontalAdvance(self._unit_text) + 6
        h = fm_num.height() + 4
        return QSize(max(w, 60), h)

    def paintEvent(self, ev):
        from PyQt6.QtGui import QFontMetrics
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self._color))

        fm_num  = QFontMetrics(self._num_font)
        fm_unit = QFontMetrics(self._unit_font)

        # ベースライン = ascent を基準に上から配置
        baseline = fm_num.ascent()

        # 数値を描画
        p.setFont(self._num_font)
        p.drawText(0, baseline, self._num_text)

        # 単位を数値の右横・同一ベースラインで描画
        num_w = fm_num.horizontalAdvance(self._num_text)
        p.setFont(self._unit_font)
        p.drawText(num_w + 2, baseline, self._unit_text)

        p.end()

class BadgeWidget(QWidget):
    """
    評価バッジを QPainter で描画するカスタムウィジェット。
    盤面バッジと同じアイコン形状（✓ △ ✕）をテキストの左に描画する。

    set_category() でカテゴリが変わると、背景色・前景色は QVariantAnimation
    でクロスフェードする(200ms / OutCubic)。アイコン形状は新カテゴリで
    即時切替するが、色フェードに紛れて違和感は出ない。
    """
    BADGE_LABEL = {
        "best": "最善", "good": "良手", "inaccuracy": "緩手",
        "mistake": "疑問", "blunder": "悪手",
    }
    # 色フェードのアニメ時間 (ms)。MetricLabel(300ms)より少し短くして
    # 色が着座する印象に。
    _COLOR_ANIM_DURATION_MS = 200

    def __init__(self):
        super().__init__()
        self._category = None
        self.setFixedHeight(24)
        # 色補間用: 表示中の色(paintEvent はこれを参照)
        bg0, fg0 = self._resolve_colors(None)
        self._display_bg = QColor(bg0)
        self._display_fg = QColor(fg0)
        # アニメ補間元(start)/補間先(end)
        self._anim_start_bg = QColor(bg0)
        self._anim_start_fg = QColor(fg0)
        self._anim_end_bg   = QColor(bg0)
        self._anim_end_fg   = QColor(fg0)
        self._color_anim = None  # QVariantAnimation(遅延生成)

    @staticmethod
    def _resolve_colors(category):
        """カテゴリから (bg_color, fg_color) の QColor タプルを返す。
        paintEvent 内の色決定ロジックと同じ規則:
          - ライト: T().BLUNDER に値があれば bg=その色/fg=#ffffff、無ければ
            eval_badge_tuple() にフォールバック
          - ダーク: eval_badge_tuple() を使用
        テーマ変更や ColorAdjustmentDialog の値変更にもこの関数が呼ばれる
        たびに追従する。"""
        if not T().is_dark:
            blunder_c = T().BLUNDER.get(category)
            if blunder_c:
                return QColor(blunder_c), QColor("#ffffff")
            bg_hex, fg_hex = eval_badge_tuple(category)
            return QColor(bg_hex), QColor(fg_hex)
        bg_hex, fg_hex = eval_badge_tuple(category)
        return QColor(bg_hex), QColor(fg_hex)

    def set_category(self, category):
        old_category = self._category
        self._category = category
        # テキスト幅に応じて横幅を動的に計算
        from PyQt6.QtGui import QFontMetrics
        label = self.BADGE_LABEL.get(category, "不明")
        fm = QFontMetrics(Font_SM(True))
        icon_w = 20  # アイコン領域幅
        text_w = fm.horizontalAdvance(label)
        self.setFixedWidth(icon_w + text_w + 18)  # padding左右6px+右余白追加

        # 色フェード(同じカテゴリなら何もしない)
        if old_category == category:
            self.update()
            return
        new_bg, new_fg = self._resolve_colors(category)
        self._start_color_anim(new_bg, new_fg)

    def _start_color_anim(self, end_bg: QColor, end_fg: QColor):
        """現在の表示色を起点に、新しい色までアニメで補間する。"""
        if self._color_anim is None:
            from PyQt6.QtCore import QVariantAnimation, QEasingCurve
            anim = QVariantAnimation(self)
            anim.setDuration(self._COLOR_ANIM_DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.valueChanged.connect(self._on_color_anim_value_changed)
            self._color_anim = anim
        # 開始色 = 現在の表示色(進行中アニメも途中値で正しく拾える)
        self._anim_start_bg = QColor(self._display_bg)
        self._anim_start_fg = QColor(self._display_fg)
        self._anim_end_bg   = QColor(end_bg)
        self._anim_end_fg   = QColor(end_fg)
        self._color_anim.stop()
        self._color_anim.start()

    def _on_color_anim_value_changed(self, t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            return
        self._display_bg = self._lerp_color(self._anim_start_bg, self._anim_end_bg, t)
        self._display_fg = self._lerp_color(self._anim_start_fg, self._anim_end_fg, t)
        self.update()

    @staticmethod
    def _lerp_color(c0: QColor, c1: QColor, t: float) -> QColor:
        """RGB(A) を線形補間して新しい QColor を返す。"""
        if t <= 0.0:
            return QColor(c0)
        if t >= 1.0:
            return QColor(c1)
        r = c0.red()   + (c1.red()   - c0.red())   * t
        g = c0.green() + (c1.green() - c0.green()) * t
        b = c0.blue()  + (c1.blue()  - c0.blue())  * t
        a = c0.alpha() + (c1.alpha() - c0.alpha()) * t
        return QColor(int(round(r)), int(round(g)), int(round(b)), int(round(a)))

    def refresh_colors(self):
        """テーマ切替・色調整時に表示色を新カテゴリ色へ瞬時更新する。
        アニメは挟まず、即座に paintEvent に反映する。"""
        bg, fg = self._resolve_colors(self._category)
        self._display_bg = QColor(bg)
        self._display_fg = QColor(fg)
        if self._color_anim is not None:
            self._color_anim.stop()
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W = self.width()
        H = self.height()
        cat = self._category

        # 表示色は補間済み(set_category 経由のアニメ後に _display_bg/_fg が
        # 確定する)。テーマ切替直後等で _display_bg が初期値のままの場合は
        # refresh_colors() で同期される。
        bg_color = self._display_bg
        fg_color = self._display_fg

        # 背景（角丸ピル、ボーダーなし）
        p.setBrush(QBrush(bg_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, W, H, 12, 12)

        # ── アイコン領域 ──────────────────────────────────────────
        icon_cx = 16   # アイコン中心X
        icon_cy = H / 2
        icon_r  = 9.5  # アイコン半径

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(fg_color))

        _stroker = QPainterPathStroker()
        _stroker.setWidth(icon_r * 0.34)
        _stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        _stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        if cat in ("best", "good"):
            # 〇 円アウトライン（盤面バッジと統一）
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(fg_color, icon_r * 0.22,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawEllipse(QPointF(icon_cx, icon_cy), icon_r * 0.52, icon_r * 0.52)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fg_color))

        elif cat == "inaccuracy":
            # △ アウトラインのみ（盤面バッジと統一）
            import math
            tri_h = icon_r * 0.94
            tri_w = tri_h / math.sqrt(3)
            offset = icon_r * 0.12  # 視覚的重心補正：少し下にずらす
            tri_top  = icon_cy - tri_h * 2 / 3 + offset
            tri_base = icon_cy + tri_h * 1 / 3 + offset
            tri = QPainterPath()
            tri.moveTo(icon_cx,         tri_top)
            tri.lineTo(icon_cx + tri_w, tri_base)
            tri.lineTo(icon_cx - tri_w, tri_base)
            tri.closeSubpath()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(fg_color, icon_r * 0.20,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(tri)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fg_color))

        elif cat == "mistake":
            # △ アウトラインのみ（疑問手は△、盤面バッジと統一）
            import math
            tri_h = icon_r * 0.94
            tri_w = tri_h / math.sqrt(3)
            offset = icon_r * 0.12  # 視覚的重心補正：少し下にずらす
            tri_top  = icon_cy - tri_h * 2 / 3 + offset
            tri_base = icon_cy + tri_h * 1 / 3 + offset
            tri = QPainterPath()
            tri.moveTo(icon_cx,         tri_top)
            tri.lineTo(icon_cx + tri_w, tri_base)
            tri.lineTo(icon_cx - tri_w, tri_base)
            tri.closeSubpath()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(fg_color, icon_r * 0.20,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(tri)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fg_color))

        elif cat == "blunder":
            # ✕ バツ
            _stroker.setWidth(icon_r * 0.22)
            d = icon_r * 0.44
            cross = QPainterPath()
            cross.moveTo(icon_cx - d, icon_cy - d)
            cross.lineTo(icon_cx + d, icon_cy + d)
            cross.moveTo(icon_cx + d, icon_cy - d)
            cross.lineTo(icon_cx - d, icon_cy + d)
            p.drawPath(_stroker.createStroke(cross))

        else:
            # ? クエスチョンマーク（アウトラインスタイル・他アイコンと統一）
            # 形状調整: 上半分の円弧から下へ滑らかに繋がる「フック型」を
            # 1本の連続パスで描く(角張りを抑える)。
            # 下の点は弧と分離して配置。
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(fg_color, icon_r * 0.22,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            # パス座標は icon_r 基準の相対係数で記述(描画スケール非依存)
            # 上半分の弧の幅、上下の高さ、引き終端の位置
            arc_x = icon_r * 0.38    # 弧の半幅
            top_y = icon_r * 0.68    # 弧の頂点までの距離(上方向)
            mid_y = icon_r * 0.30    # 弧の左右終端の y(やや上)
            tail_y = icon_r * 0.62   # 弧の下端(=引き終端)の位置
            arc_path = QPainterPath()
            # 左上 → 上を経由して右上(円弧の上半分)
            arc_path.moveTo(icon_cx - arc_x, icon_cy - mid_y)
            arc_path.cubicTo(
                icon_cx - arc_x, icon_cy - top_y,
                icon_cx + arc_x, icon_cy - top_y,
                icon_cx + arc_x, icon_cy - mid_y,
            )
            # 右上 → 中央下(下に向かって滑らかに引き降ろす)
            arc_path.cubicTo(
                icon_cx + arc_x, icon_cy + (tail_y - mid_y) * 0.4,
                icon_cx,         icon_cy + (tail_y - mid_y) * 0.4,
                icon_cx,         icon_cy + tail_y - mid_y,
            )
            p.drawPath(arc_path)
            # 点(弧の引き終端から少し離して配置)
            p.setBrush(QBrush(fg_color))
            p.setPen(Qt.PenStyle.NoPen)
            dot_r = icon_r * 0.14
            p.drawEllipse(QPointF(icon_cx, icon_cy + icon_r * 0.70), dot_r, dot_r)

        # ── テキスト ──────────────────────────────────────────────
        label = self.BADGE_LABEL.get(cat, "不明")
        p.setFont(Font_SM(True))
        p.setPen(QPen(fg_color))
        text_x = 27  # アイコンの右から（間隔を詰める）
        p.drawText(QRectF(text_x, 0, W - text_x - 6, H),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   label)
        p.end()


class _StoneIcon(QWidget):
    """悪手判定カード等で使う小さな碁石アイコン（円形）。
    QSS の border-radius は環境によっては QLabel に効かないため、
    paintEvent で確実にアンチエイリアス円を描画する。
    color: "B"=黒, "W"=白, それ以外（"—"等）=グレー（0手目用）。

    set_color() で色が変わると、旧色と新色を 250ms / OutCubic で
    クロスフェード遷移させる(_CrossFadeLabel と同期)。
    """
    _CROSSFADE_DURATION_MS = 250

    def __init__(self, diameter: int = 14, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        # 円外を完全に透過させ、親背景が見えるようにする
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._color = "—"
        # クロスフェード用
        self._old_color: str = "—"
        self._fade_t: float = 1.0  # 1.0 = 完了状態(=旧色不可視)
        self._fade_anim = None     # QVariantAnimation

    def set_color(self, color: str):
        if self._color == color:
            return
        # アニメ中の場合、現在の表示色(=新色になっている)を新たな旧色とする
        self._old_color = self._color
        self._color = color
        self._fade_t = 0.0
        self._start_fade_anim()
        self.update()

    def _start_fade_anim(self):
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if self._fade_anim is not None:
            self._fade_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(self._CROSSFADE_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        def _on_changed(v):
            try:
                self._fade_t = float(v)
            except (TypeError, ValueError):
                return
            self.update()
        def _on_finished():
            self._fade_t = 1.0
            self._old_color = self._color
            self.update()
        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._fade_anim = anim
        anim.start()

    @staticmethod
    def _stone_colors(color: str):
        """色文字列から (fill, edge) の QColor ペアを返す。"""
        from PyQt6.QtGui import QColor
        if color == "B":
            return QColor(T().STONE_BLACK), QColor(T().STONE_BORDER_BLACK)
        elif color == "W":
            # 白石は純白で対局者を象徴的に表現
            return QColor("#ffffff"), QColor(T().STONE_BORDER_WHITE)
        else:
            return QColor(T().STONE_NEUTRAL), QColor(T().STONE_BORDER_BLACK)

    def paintEvent(self, _ev):
        from PyQt6.QtGui import QPainter, QPen, QBrush
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 1pxボーダーを内側に収めるため 0.5px インセット
        r = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        # アニメ中でなければ通常描画
        if self._fade_t >= 1.0 or self._old_color == self._color:
            fill, edge = self._stone_colors(self._color)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(edge, 1.0))
            p.drawEllipse(r)
            p.end()
            return
        # アニメ中: 新色をフェードイン、旧色をフェードアウトで重ね描画
        # 新色(不透明度 fade_t)
        new_fill, new_edge = self._stone_colors(self._color)
        p.setOpacity(self._fade_t)
        p.setBrush(QBrush(new_fill))
        p.setPen(QPen(new_edge, 1.0))
        p.drawEllipse(r)
        # 旧色(不透明度 1-fade_t)を重ね描き
        old_fill, old_edge = self._stone_colors(self._old_color)
        p.setOpacity(1.0 - self._fade_t)
        p.setBrush(QBrush(old_fill))
        p.setPen(QPen(old_edge, 1.0))
        p.drawEllipse(r)
        p.end()


class _CrossFadeLabel(QLabel):
    """テキスト変更時にクロスフェードする QLabel。
    setText() で値が変わると、旧テキストはフェードアウトしながら新テキストが
    フェードインする(同位置で重ね描画、合計 250ms / OutCubic)。
    レイアウトは新テキストの大きさで決まる(旧は重ね描画なのでサイズ変動なし)。
    フォーカス時の主要な使い方は MoveInfoCard の「黒/白 N手目」表示。
    """
    _CROSSFADE_DURATION_MS = 250

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        # クロスフェード用の旧テキストと進捗
        self._old_text: str = ""
        self._fade_t: float = 1.0  # 1.0 = 完了状態(=旧テキスト不可視)
        self._fade_anim = None     # QVariantAnimation

    def setText(self, text: str):
        cur = self.text()
        if text == cur:
            super().setText(text)
            return
        # アニメ中だった場合は、現在の旧テキスト残像をスキップ(新→新の連続変更時)
        # 単に新テキストへの遷移として扱う(旧 = アニメ前のテキスト)。
        self._old_text = cur
        self._fade_t = 0.0
        super().setText(text)
        self._start_fade_anim()

    def _start_fade_anim(self):
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if self._fade_anim is not None:
            self._fade_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(self._CROSSFADE_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        def _on_changed(v):
            try:
                self._fade_t = float(v)
            except (TypeError, ValueError):
                return
            self.update()
        def _on_finished():
            self._fade_t = 1.0
            self._old_text = ""
            self.update()
        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._fade_anim = anim
        anim.start()

    def paintEvent(self, ev):
        # 通常描画: アニメ中でないなら QLabel のデフォルトに任せる
        if self._fade_t >= 1.0 or not self._old_text:
            super().paintEvent(ev)
            return
        # アニメ中: 新テキストと旧テキストの両方を、自前で QPainter 描画する。
        # super().paintEvent() は内部で別 QPainter を持つため外部から
        # setOpacity を効かせられず、フェードインを表現できない。よって
        # 自前で描画して両方の不透明度を制御する。
        from PyQt6.QtGui import QPainter, QColor
        from PyQt6.QtCore import Qt as _Qt
        # テキスト色を取得(stylesheet で指定された色を優先、なければ palette)
        col = self.palette().color(self.foregroundRole())
        try:
            ss = self.styleSheet()
            if "color:" in ss:
                seg = ss.split("color:", 1)[1].split(";", 1)[0].strip()
                fb = QColor(seg)
                if fb.isValid():
                    col = fb
        except Exception:
            pass
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self.font())
        p.setPen(col)
        flags = int(self.alignment())
        # 新テキスト: 不透明度 fade_t でフェードイン
        p.setOpacity(self._fade_t)
        p.drawText(self.rect(), flags, self.text())
        # 旧テキスト残像: 不透明度 1-fade_t でフェードアウト
        p.setOpacity(1.0 - self._fade_t)
        p.drawText(self.rect(), flags, self._old_text)
        p.end()


class MoveInfoCard(QWidget):
    """
    現在選択手の情報カード（カードスタイル版）。
    """

    BADGE_LABEL = {
        "best": "最善", "good": "良手", "inaccuracy": "緩手",
        "mistake": "疑問", "blunder": "悪手",
    }

    def _value_color(self, category) -> str:
        """テーマに応じた数値色を返す。ColorAdjustmentDialog からの色変更にも追従する。
        - ライトモード: T().BLUNDER (= LIGHT_BLUNDER_COLORS) から取得
        - ダークモード: EVAL_COLORS["text_dark_mode"] から取得
        """
        if not T().is_dark:
            c = T().BLUNDER.get(category) if category else None
            return c.name() if c else T().TEXT2.name()
        # ダーク: EVAL_COLORS から直接取得 (class変数キャッシュではなく)
        v = EVAL_COLORS.get(category) if category else None
        if v is not None:
            return v["text_dark_mode"]
        return T().TEXT2.name()

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")

        # ── カード外枠（_make_card と同じスタイル）──
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._card = QWidget()
        self._card.setStyleSheet(
            f"QWidget#moveinfo_card {{"
            f"  background:{T().PANEL.name()};"
            f"  border:1px solid {T().BORDER2.name()};"
            f"  border-radius:12px;"
            f"}}"
        )
        self._card.setObjectName("moveinfo_card")
        outer.addWidget(self._card)

        vl = QVBoxLayout(self._card)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # ── 上段: 石マーク + 手情報 + バッジ（ヘッダーは廃止し、ボディに統合）──
        # 旧実装ではヘッダー領域(背景PANEL2)に置いていたが、他カードと統一感を
        # 出すためボディ内に移動。背景は透明でカード本体(PANEL)に溶け込む。
        info_row = QWidget()
        info_row.setStyleSheet("background:transparent;")
        info_row.setFixedHeight(44)
        hl = QHBoxLayout(info_row)
        hl.setContentsMargins(SP_MD, SP_SM, SP_MD, SP_SM)
        # 要素間の余白: SP_SM(8). 碁石アイコンとテキストが近接して
        # 一体感を出す。テキスト→バッジ間は addStretch があるため
        # この spacing 値の影響は実質受けない。
        hl.setSpacing(SP_SM)

        self._stone = _StoneIcon(16)
        hl.addWidget(self._stone)

        self._move_lbl = _CrossFadeLabel("—")
        self._move_lbl.setFont(Font_MD(True))
        self._move_lbl.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        hl.addWidget(self._move_lbl)

        # 座標ラベルは削除（後方互換のため属性として残す）
        self._coord_lbl = QLabel("")
        self._coord_lbl.hide()

        hl.addStretch()

        self._badge = BadgeWidget()
        hl.addWidget(self._badge)

        vl.addWidget(info_row)

        # ── 情報行とメトリクス行の間の水平仕切り線 ──
        metric_div = QFrame()
        metric_div.setFixedHeight(1)
        metric_div.setStyleSheet(f"background:{T().BORDER2.name()}; border:none;")
        self._metric_div = metric_div  # apply_theme で更新するため保持
        vl.addWidget(metric_div)

        # ── カードボディ: 2列メトリクス（縦区切り線付き）──
        # メトリクス（勝率変化・目差変化）と「前手未解析メッセージ」は
        # body 内に同居させ、表示切替する。これにより body 自体の高さは常に
        # 一定で、カード全体のレイアウトが崩れない。
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        bl = QHBoxLayout(body)
        # 上下マージンを対称にして縦仕切り線が中央に見えるようにする
        bl.setContentsMargins(0, SP_SM, 0, SP_SM)
        bl.setSpacing(0)

        self._wr_card = self._metric_pill("勝率変化")
        self._sl_card = self._metric_pill("目差変化")
        bl.addWidget(self._wr_card["widget"])

        # 縦区切り線（QFrame.VLine は styleSheet が効かないため QWidget で代替）
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{T().BORDER.name()};")
        self._metric_sep = sep  # apply_theme で更新するため保持
        bl.addWidget(sep)

        bl.addWidget(self._sl_card["widget"])
        # body にメトリクスの自然な高さに相当する最小高さを設定する。
        # これがないとメトリクス側 widget を hide() した時にレイアウトが
        # 折りたたまれ、body の高さが縮んで メッセージが上に詰まって表示される。
        # body 上下マージン (SP_SM=8 + SP_SM=8 = 16) + ピル自然高 (ラベル12 +
        # 値40 + 上下padding 2+2 ≒ 56) + 余裕16 で 88px とする。
        # これによりメッセージ表示時もメトリクス表示時と同じ高さを保つ。
        body.setMinimumHeight(88)
        self._metrics_body = body
        vl.addWidget(body)

        # ── 前手未解析メッセージ（メトリクスと同じ場所に重ねて表示）──
        # body の子として絶対配置し、表示切替時には メトリクス側の3つの widget
        # （勝率変化ピル、縦区切り線、目差変化ピル）を hide()/show() で切替える。
        # body 自体の高さはメトリクスのレイアウトで決まり変わらない。
        self._missing_msg = QLabel(
            "前の手を解析してください",
            body  # 親=body にして、body 内に絶対配置
        )
        self._missing_msg.setWordWrap(True)
        self._missing_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._missing_msg.setFont(Font_MD())
        self._missing_msg.setStyleSheet(
            f"color:{T().TEXT2.name()}; background:transparent;"
        )
        self._missing_msg.hide()  # 初期は非表示

        # body のリサイズ時に _missing_msg も追従させる（eventFilter で安全に処理）
        body.installEventFilter(self)
        self._body_for_msg = body  # eventFilter から参照するため保持

        # ── ツリー区切り線 ──
        tree_div = QFrame()
        tree_div.setFixedHeight(1)
        tree_div.setStyleSheet(f"background:{T().BORDER2.name()}; border:none;")
        vl.addWidget(tree_div)

        # ── 分岐ツリー領域 ──
        tree_body = QWidget()
        tree_body.setStyleSheet("background:transparent;")
        tree_bl = QVBoxLayout(tree_body)
        # 通常は PAD_CARD (12,12,12,12) だが、ここでは:
        #   - 左/上: SP_SM=8 (ツリー本体の余白)
        #   - 右/下: 4 (スクロールバーをカード端に近づける)
        tree_bl.setContentsMargins(SP_SM, SP_SM, 4, 4)
        tree_bl.setSpacing(0)

        self._tree_scroll = QScrollArea()
        self._tree_scroll.setWidgetResizable(False)  # Trueだとresize()が無効化されるため
        self._tree_scroll.setMinimumHeight(44)
        self._tree_scroll.setMinimumWidth(0)
        # 内側Widget(BranchTreeWidget)はコンテンツサイズに resize() で固定される。
        # viewport が広くなっても中身は上端左寄せに固定し、余白(下端)に
        # 横スクロールバーが収まるようにする。
        self._tree_scroll.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._tree_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tree_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._tree_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._tree_scroll.setStyleSheet(
            f"QScrollArea {{ border:none; background:{T().PANEL.name()}; border-radius:4px; }}"
            f"QScrollBar:horizontal {{ background:transparent; height:6px; margin:0; }}"
            f"QScrollBar::handle:horizontal {{ background:{T().BORDER.name()};"
            f"border-radius:3px; min-width:20px; }}"
            f"QScrollBar::add-line:horizontal,"
            f"QScrollBar::sub-line:horizontal {{ width:0px; }}"
            f"QScrollBar:vertical {{ background:transparent; width:6px; margin:0; }}"
            f"QScrollBar::handle:vertical {{ background:{T().BORDER.name()};"
            f"border-radius:3px; min-height:20px; }}"
            f"QScrollBar::add-line:vertical,"
            f"QScrollBar::sub-line:vertical {{ height:0px; }}"
        )
        from PyQt6.QtGui import QPalette as _QPalette
        _ts_pal = _QPalette()
        _ts_pal.setColor(_QPalette.ColorRole.Window, T().PANEL)
        _ts_pal.setColor(_QPalette.ColorRole.Base, T().PANEL)
        self._tree_scroll.viewport().setPalette(_ts_pal)
        self._tree_scroll.viewport().setAutoFillBackground(True)
        tree_bl.addWidget(self._tree_scroll)
        vl.addWidget(tree_body)

        # ── ツリー上のマウスホイールの挙動 ──
        # 要件:
        #   - 縦スクロール可能なら縦スクロール (Qt デフォルト)
        #   - 縦スクロール不可なら何もしない (= 親 MainWindow に伝播させて手数移動が
        #     発火しないように吸収する)
        #   - Shift+ホイールも何もしない (吸収のみ)
        # QScrollArea のデフォルトでは縦スクロール不可時にイベントが親へ伝播し、
        # MainWindow.wheelEvent で手数移動が発火してしまう。これを抑止する。
        _orig_wheel = self._tree_scroll.wheelEvent
        def _tree_wheel(ev, _orig=_orig_wheel, _scroll=self._tree_scroll):
            mods = ev.modifiers()
            if mods & Qt.KeyboardModifier.ShiftModifier:
                # Shift+ホイール: 何もせずに吸収
                ev.accept()
                return
            vbar = _scroll.verticalScrollBar()
            if vbar.minimum() < vbar.maximum():
                # 縦スクロール可能: Qt デフォルト処理
                _orig(ev)
                # 念のため accept (Qt のデフォルトは accept 済みのはず)
                ev.accept()
            else:
                # 縦スクロール不可: 何もせず吸収 (親への伝播を止める)
                ev.accept()
        self._tree_scroll.wheelEvent = _tree_wheel

        # ツリーウィジェット本体は外部から set_tree() で設定する
        self._inner_tree: Optional["BranchTreeWidget"] = None

    def eventFilter(self, obj, ev):
        """body のリサイズ時に前手未解析メッセージを追従させる。
        既存のフェードオーバーレイ更新も同じ eventFilter 内で処理する。"""
        # body のリサイズ → メッセージのジオメトリ追従
        if (hasattr(self, "_body_for_msg")
            and obj is self._body_for_msg
            and ev.type() == QEvent.Type.Resize
            and hasattr(self, "_missing_msg")):
            self._missing_msg.setGeometry(
                0, 0, self._metrics_body.width(), self._metrics_body.height())
        # ツリー scroll viewport のリサイズ → フェードオーバーレイ更新
        if (hasattr(self, "_fade_left")
            and hasattr(self, "_tree_scroll")
            and obj is self._tree_scroll.viewport()):
            if ev.type() == QEvent.Type.Resize:
                self._update_tree_fade_overlays()
        return super().eventFilter(obj, ev)

    def set_tree(self, tree: "BranchTreeWidget"):
        """分岐ツリーウィジェットをカード内に設定する。"""
        self._inner_tree = tree
        tree.setStyleSheet(f"background:{T().PANEL.name()};")
        self._tree_scroll.setWidget(tree)

        # ── 端フェードオーバーレイを viewport の子として配置 ──
        viewport = self._tree_scroll.viewport()
        self._fade_left   = _TreeEdgeFadeOverlay(viewport, "left")
        self._fade_right  = _TreeEdgeFadeOverlay(viewport, "right")
        self._fade_top    = _TreeEdgeFadeOverlay(viewport, "top")
        self._fade_bottom = _TreeEdgeFadeOverlay(viewport, "bottom")
        # viewport の resize とスクロール位置に追従させる
        viewport.installEventFilter(self)
        hbar = self._tree_scroll.horizontalScrollBar()
        hbar.valueChanged.connect(self._update_tree_fade_overlays)
        hbar.rangeChanged.connect(lambda _mn, _mx: self._update_tree_fade_overlays())
        vbar = self._tree_scroll.verticalScrollBar()
        vbar.valueChanged.connect(self._update_tree_fade_overlays)
        vbar.rangeChanged.connect(lambda _mn, _mx: self._update_tree_fade_overlays())
        # 初期配置
        self._update_tree_fade_overlays()

    def _update_tree_fade_overlays(self):
        """フェードオーバーレイの位置・サイズ・表示状態を更新する。"""
        if not hasattr(self, "_fade_left") or self._inner_tree is None:
            return
        viewport = self._tree_scroll.viewport()
        vw = viewport.width()
        vh = viewport.height()
        # フェード幅 = ノード半径の半分（ノード直径の 1/4、アイコン1つの半分相当）
        node_r = getattr(self._inner_tree, "_node_r", 13)
        fade_w = max(6, int(node_r))  # 半径分 = 直径の半分 ≒ アイコン半分

        # スクロール可能かどうかで各方向の表示を切り替える(ハイブリッド)
        hbar = self._tree_scroll.horizontalScrollBar()
        can_scroll_left   = hbar.value() > hbar.minimum()
        can_scroll_right  = hbar.value() < hbar.maximum()
        vbar = self._tree_scroll.verticalScrollBar()
        can_scroll_up     = vbar.value() > vbar.minimum()
        can_scroll_down   = vbar.value() < vbar.maximum()

        # 左右オーバーレイ: viewport の左右端、高さは viewport いっぱい
        self._fade_left.set_fade_width(fade_w)
        self._fade_right.set_fade_width(fade_w)
        self._fade_left.setGeometry(0, 0, fade_w, vh)
        self._fade_right.setGeometry(max(0, vw - fade_w), 0, fade_w, vh)
        self._fade_left.setVisible(can_scroll_left)
        self._fade_right.setVisible(can_scroll_right)

        # 上下オーバーレイ: viewport の上下端、幅は viewport いっぱい
        self._fade_top.set_fade_width(fade_w)
        self._fade_bottom.set_fade_width(fade_w)
        self._fade_top.setGeometry(0, 0, vw, fade_w)
        self._fade_bottom.setGeometry(0, max(0, vh - fade_w), vw, fade_w)
        self._fade_top.setVisible(can_scroll_up)
        self._fade_bottom.setVisible(can_scroll_down)

        # オーバーレイを最前面に
        self._fade_left.raise_()
        self._fade_right.raise_()
        self._fade_top.raise_()
        self._fade_bottom.raise_()

    def tree_scroll(self) -> QScrollArea:
        """外部からスクロール制御するための参照を返す。"""
        return self._tree_scroll

    def _metric_pill(self, label: str, pad_left: int = SP_LG, pad_right: int = SP_LG) -> dict:
        w = QWidget()
        w.setObjectName("metric_pill")
        w.setStyleSheet("QWidget#metric_pill { background:transparent; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(pad_left, 2, pad_right, 2)
        # ラベル（"勝率変化" 等）と MetricLabel（"-7.4%" 等）の間隔を詰める
        lay.setSpacing(0)
        lbl = QLabel(label)
        lbl.setFont(Font_XS())
        lbl.setStyleSheet(f"color:{T().TEXT2.name()}; background:transparent;")
        val = MetricLabel()
        lay.addWidget(lbl)
        lay.addWidget(val)
        return {"widget": w, "val": val, "unit": None, "sub": None}

    def _set_stone(self, color: str):
        # _StoneIcon が paintEvent で円を描画する。
        # "B"=黒, "W"=白, それ以外=グレー(0手目等)
        self._stone.set_color(color if color in ("B", "W") else "—")

    def _set_badge(self, category):
        self._badge.set_category(category)

    # メトリクス↔メッセージのクロスフェード時間 (ms)
    _MODE_FADE_DURATION_MS = 250

    def _set_message_mode(self, show_message: bool):
        """前手未解析メッセージとメトリクスの表示を切り替える。
        body の高さは変わらず、内部のwidgetだけ切替えるのでレイアウト維持。
        切替時は QGraphicsOpacityEffect でクロスフェードする。
        """
        # 初期状態の同期(初回呼び出し時 or 何らかの理由で属性未設定の時)
        if not hasattr(self, "_mode_show_message"):
            self._mode_show_message = None  # 未確定

        # 既に同じ表示状態ならアニメ不要
        if self._mode_show_message == show_message:
            return

        # メッセージは表示時に body サイズに追従させる
        if show_message:
            self._missing_msg.raise_()
            self._missing_msg.setGeometry(
                0, 0, self._metrics_body.width(), self._metrics_body.height())

        metric_widgets = [
            self._wr_card["widget"],
            self._metric_sep,
            self._sl_card["widget"],
        ]
        msg_widget = self._missing_msg

        # 初回表示時はアニメせず即時設定(body サイズが未確定など)
        first_call = (self._mode_show_message is None)
        self._mode_show_message = show_message
        if first_call:
            for w in metric_widgets:
                w.setVisible(not show_message)
            msg_widget.setVisible(show_message)
            return

        # アニメ準備: フェードアウト側の opacity = 1.0 → 0.0、
        #             フェードイン側の opacity = 0.0 → 1.0
        # 両方とも setVisible(True) にしてからアニメ、完了時に消す側を hide。
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve

        for w in metric_widgets:
            w.setVisible(True)
        msg_widget.setVisible(True)

        # 既存のアニメがあれば停止して effect も外す(重複防止)
        prev_anim = getattr(self, "_mode_fade_anim", None)
        if prev_anim is not None:
            prev_anim.stop()
            self._clear_mode_fade_effects()

        # OpacityEffect を装着(各ウィジェットに別々の effect が必要)
        metric_effs = []
        for w in metric_widgets:
            eff = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(eff)
            metric_effs.append(eff)
        msg_eff = QGraphicsOpacityEffect(msg_widget)
        msg_widget.setGraphicsEffect(msg_eff)
        self._mode_fade_metric_effs = metric_effs
        self._mode_fade_msg_eff = msg_eff
        self._mode_fade_metric_widgets = metric_widgets
        self._mode_fade_msg_widget = msg_widget

        # 開始値を即時反映してチラつき防止
        if show_message:
            for eff in metric_effs:
                eff.setOpacity(1.0)
            msg_eff.setOpacity(0.0)
        else:
            for eff in metric_effs:
                eff.setOpacity(0.0)
            msg_eff.setOpacity(1.0)

        anim = QVariantAnimation(self)
        anim.setDuration(self._MODE_FADE_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_changed(t):
            try:
                t = float(t)
            except (TypeError, ValueError):
                return
            if show_message:
                # メトリクス: 1→0、メッセージ: 0→1
                m_op = 1.0 - t
                msg_op = t
            else:
                m_op = t
                msg_op = 1.0 - t
            try:
                for eff in self._mode_fade_metric_effs:
                    eff.setOpacity(m_op)
                self._mode_fade_msg_eff.setOpacity(msg_op)
            except RuntimeError:
                # ウィジェット破棄等で effect 参照が無効になった場合は黙って終了
                pass

        def _on_finished():
            # アニメ完了: 不要側を hide + effect を外す(常時負荷を回避)
            for w in self._mode_fade_metric_widgets:
                w.setVisible(not show_message)
            self._mode_fade_msg_widget.setVisible(show_message)
            self._clear_mode_fade_effects()

        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._mode_fade_anim = anim
        anim.start()

    def _clear_mode_fade_effects(self):
        """フェード用に装着した QGraphicsOpacityEffect を全て外す。
        QGraphicsOpacityEffect は描画パイプライン常時負荷のため、
        アニメ完了後は必ず外す。"""
        for w in getattr(self, "_mode_fade_metric_widgets", []):
            try:
                w.setGraphicsEffect(None)
            except RuntimeError:
                pass
        msg_w = getattr(self, "_mode_fade_msg_widget", None)
        if msg_w is not None:
            try:
                msg_w.setGraphicsEffect(None)
            except RuntimeError:
                pass
        self._mode_fade_metric_effs = []
        self._mode_fade_msg_eff = None

    @_profile_method("MoveCard.update")
    def update_card(self, ma: "Optional[MoveAnalysis]", move_color: str = "B",
                    move_number: int = 0, human_coord: str = ""):
        """悪手判定カードの内容を更新する。
        ma がある（解析済み）場合は ma の情報を全面的に使用する。
        ma が None の場合は move_color / move_number / human_coord から
        基本情報（石マーク・「白 N手目」・座標）だけ表示し、評価は「不明」とする。
        ルートノード等の「打ち手なし」状態は move_number == 0 で表現する。
        """
        if ma is None:
            # ルートノード等の「打ち手なし」: 全要素ハイフン
            if move_number == 0 or not move_color:
                self._set_stone(move_color or "—")
                self._move_lbl.setText("—")
                self._coord_lbl.setText("")
                self._set_badge(None)
                self._wr_card["val"].set_value("—", "", T().TEXT2)
                self._sl_card["val"].set_value("—", "", T().TEXT2)
                self._set_message_mode(False)
                return
            # 解析未実行の打ち手あり局面: 基本情報を表示、評価は「不明」
            color_str = "黒" if move_color == "B" else "白"
            self._set_stone(move_color)
            self._move_lbl.setText(f"{color_str} {move_number}手目")
            self._coord_lbl.setText(human_coord)
            self._set_badge(None)  # 「不明」バッジ
            self._wr_card["val"].set_value("—", "", T().TEXT2)
            self._sl_card["val"].set_value("—", "", T().TEXT2)
            self._set_message_mode(False)
            return

        blunder = ma.blunder
        category = blunder.category if blunder else None
        vc = self._value_color(category)

        # 0手目（ルートノード）はハイフン表示・石アイコンをグレーに
        if ma.move_number == 0:
            self._set_stone("—")  # グレー
            self._move_lbl.setText("—")
            self._coord_lbl.setText("")
            self._set_badge(None)
            self._wr_card["val"].set_value("—", "", T().TEXT2)
            self._sl_card["val"].set_value("—", "", T().TEXT2)
            # ルートでは前手が無いのでメッセージは出さずメトリクス側
            self._set_message_mode(False)
            return

        color_str = "黒" if ma.color == "B" else "白"
        self._set_stone(ma.color)
        self._move_lbl.setText(f"{color_str} {ma.move_number}手目")
        self._coord_lbl.setText(ma.human_coord)
        self._set_badge(category)

        if blunder:
            wr_before = blunder.win_rate_before * 100
            wr_after  = blunder.win_rate_after  * 100
            wr_delta  = wr_after - wr_before
            # 書式は MetricLabel._format_signed_float に統一(±0.0 ハンドリング含む)
            self._wr_card["val"].set_value(MetricLabel._format_signed_float(wr_delta), "%", vc, category=category)

            sl_before = blunder.score_lead_before
            sl_after  = blunder.score_lead_after
            sl_delta  = sl_after - sl_before
            self._sl_card["val"].set_value(MetricLabel._format_signed_float(sl_delta), "目", vc, category=category)
            # 悪手判定が出る = メトリクス側
            self._set_message_mode(False)
        else:
            # 前手未解析等で blunder が計算できない: メッセージ側
            for card in (self._wr_card, self._sl_card):
                card["val"].set_value("—", "", T().TEXT2)
            self._set_message_mode(True)

    def apply_theme(self):
        """テーマ切り替え時にカード背景・ツリースクロールを再適用する。"""
        self._card.setStyleSheet(
            f"QWidget#moveinfo_card {{"
            f"  background:{T().PANEL.name()};"
            f"  border:1px solid {T().BORDER2.name()};"
            f"  border-radius:12px;"
            f"}}"
        )
        # バッジ色をテーマ/色調整値で即時再計算(アニメは挟まない)
        if hasattr(self, "_badge") and self._badge:
            self._badge.refresh_colors()
        # 情報行とメトリクス行の間の水平仕切り線
        if hasattr(self, "_metric_div") and self._metric_div:
            self._metric_div.setStyleSheet(
                f"background:{T().BORDER2.name()}; border:none;")
        # 前手未解析メッセージ
        if hasattr(self, "_missing_msg") and self._missing_msg:
            self._missing_msg.setStyleSheet(
                f"color:{T().TEXT2.name()}; background:transparent;"
            )
        # 分岐ツリーのスクロールエリア
        if hasattr(self, "_tree_scroll"):
            self._tree_scroll.setStyleSheet(
                f"QScrollArea {{ border:none; background:{T().PANEL.name()}; border-radius:4px; }}"
                f"QScrollBar:horizontal {{ background:transparent; height:6px; margin:0; }}"
                f"QScrollBar::handle:horizontal {{ background:{T().BORDER.name()};"
                f"border-radius:3px; min-width:20px; }}"
                f"QScrollBar::add-line:horizontal,"
                f"QScrollBar::sub-line:horizontal {{ width:0px; }}"
                f"QScrollBar:vertical {{ background:transparent; width:6px; margin:0; }}"
                f"QScrollBar::handle:vertical {{ background:{T().BORDER.name()};"
                f"border-radius:3px; min-height:20px; }}"
                f"QScrollBar::add-line:vertical,"
                f"QScrollBar::sub-line:vertical {{ height:0px; }}"
            )
            # NOTE: setAutoFillBackground(True) はテーマ切替時には呼ばない。
            # 初期化時に True 設定済みで、その状態は維持される。
            # 再呼び出しすると続けて発火する右パネル開閉アニメの
            # QGraphicsOpacityEffect と相互作用して、分岐ツリー領域に黒い
            # オーバーレイが乗る現象が発生するため。
            from PyQt6.QtGui import QPalette as _QPalette
            _ts_pal = _QPalette()
            _ts_pal.setColor(_QPalette.ColorRole.Window, T().PANEL)
            _ts_pal.setColor(_QPalette.ColorRole.Base, T().PANEL)
            self._tree_scroll.viewport().setPalette(_ts_pal)
        # BranchTreeWidget 自体
        if hasattr(self, "_inner_tree") and self._inner_tree:
            self._inner_tree.setStyleSheet(f"background:{T().PANEL.name()};")
            self._inner_tree.update()
        # メトリクスラベルの補助テキスト色
        # NOTE: _move_lbl(「黒/白 N手目」)は TEXT 色を使うため、このループの
        # 対象から除外する。除外しないと findChildren(QLabel) で拾われ、
        # color:#... + background:transparent の条件にマッチして TEXT2 に
        # 上書きされてしまう(_move_lbl は色文字列が解決済みのため
        # "TEXT2" not in styleSheet() の判定では弾けない)。
        for lbl in self.findChildren(QLabel):
            if lbl is self._move_lbl:
                continue
            if lbl.styleSheet() and "TEXT2" not in lbl.styleSheet():
                ss = lbl.styleSheet()
                if "color:" in ss and "background:transparent" in ss:
                    lbl.setStyleSheet(f"color:{T().TEXT2.name()}; background:transparent;")
        # _move_lbl はループで除外したので、ここで TEXT 色に明示再適用して
        # テーマ切替に追従させる。
        if hasattr(self, "_move_lbl") and self._move_lbl is not None:
            self._move_lbl.setStyleSheet(
                f"color:{T().TEXT.name()}; background:transparent;"
            )
        # 勝率変化・目差変化の値ラベル(MetricLabel)の色をテーマ追従させる
        # (ハイフン表示中のみ。blunder色等はそのまま維持)
        if hasattr(self, "_wr_card") and self._wr_card.get("val"):
            self._wr_card["val"].update_theme()
        if hasattr(self, "_sl_card") and self._sl_card.get("val"):
            self._sl_card["val"].update_theme()
        # 勝率変化・目差変化の縦区切り線
        if hasattr(self, "_metric_sep"):
            self._metric_sep.setStyleSheet(f"background:{T().BORDER.name()};")
        # フェードオーバーレイ（パネル色が変わるので再描画）
        if hasattr(self, "_fade_left"):
            self._fade_left.update()
            self._fade_right.update()
            self._fade_top.update()
            self._fade_bottom.update()
        self.update()

# ── Branch tree widget ───────────────────────────────────────────────────────
class _TreeEdgeFadeOverlay(QWidget):
    """
    分岐ツリー QScrollArea の viewport に被せる端フェードオーバーレイ。
    パネル色→透明のグラデーションで「続きがある」ことを視覚的に示す。

    - side: "left" / "right" / "top" / "bottom"
    - fade_px: グラデーションの幅(縦/横方向。左右なら横幅、上下なら高さ)
    - クリックは親ウィジェットに貫通する(WA_TransparentForMouseEvents)
    """
    def __init__(self, parent: QWidget, side: str):
        super().__init__(parent)
        self._side = side
        self._fade_px = 14  # デフォルト幅（set_fade_width で更新される）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_fade_width(self, px: int):
        if px != self._fade_px:
            self._fade_px = max(2, int(px))
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        panel = QColor(T().PANEL)
        opaque = QColor(panel); opaque.setAlpha(255)
        transparent = QColor(panel); transparent.setAlpha(0)

        if self._side == "left":
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, opaque)
            grad.setColorAt(1.0, transparent)
        elif self._side == "right":
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, transparent)
            grad.setColorAt(1.0, opaque)
        elif self._side == "top":
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, opaque)
            grad.setColorAt(1.0, transparent)
        else:  # "bottom"
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, transparent)
            grad.setColorAt(1.0, opaque)

        p.fillRect(0, 0, w, h, QBrush(grad))
        p.end()


class BranchTreeWidget(QWidget):
    """
    分岐ツリーを視覚的に表示するウィジェット。
    現在のノードをハイライト、クリックで移動可能。
    """
    node_clicked          = pyqtSignal(object)  # SGFNode
    node_delete_requested = pyqtSignal(object)  # SGFNode
    node_comment_requested = pyqtSignal(object)  # SGFNode（コメント編集要求）
    move_number_anchor_requested = pyqtSignal(object)  # SGFNode (手順番号起点指定)

    # ノードフォント（クラス定数）— pt指定でDPIに追従
    # _font_name はクラス定数の時点では呼べないため __init__ で設定する
    NODE_FONT = None

    # 現在ノードハイライトの移動アニメ時間 (ms)
    _CUR_MARKER_ANIM_DURATION_MS = 280
    # 新ノード出現アニメ時間 (ms)
    _NEW_NODE_ANIM_DURATION_MS = 250
    # 新ノード出現アニメをスキップする閾値: 一度に追加されたノード数がこれを
    # 超える場合(例: SGF 一括読み込み)はアニメせず即座に表示する
    _NEW_NODE_ANIM_MAX_COUNT = 4

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background:{T().PANEL.name()};")
        self.setMinimumHeight(28)
        self._nodes: list[tuple[int,int,object,bool]] = []  # (x,y,node,is_current)
        # NODE_FONT をインスタンス初期化時に設定（登録済みフォントを使用）
        if BranchTreeWidget.NODE_FONT is None:
            BranchTreeWidget.NODE_FONT = FontMono_XS()
        # node_r を「3桁テキストの半幅・半高＋余白」から算出してDPIに追従させる。
        # 3桁（例: "199"）が収まる最小半径を基準にすることで、手数が増えても崩れない。
        # 円は中心から遠いほど内幅が狭くなるため、適度に余白を取る。
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self.NODE_FONT)
        max_text_w = fm.horizontalAdvance("199")  # 3桁の最大幅
        max_text_h = fm.height()                  # フォント高さ
        # 幅・高さの大きい方の半分 + 余白3px を採用
        self._node_r = max(10, int(max(max_text_w, max_text_h) / 2) + 3)
        self._node_analyses: dict = {}
        self.setMouseTracking(True)
        self._hovered = None
        # ── 現在ノードマーカーの移動アニメ用 ──
        # 通常ノードの青リングだけアニメ対象。ルートノードのハイライトは
        # ノード本体描画と一体化しているため瞬時切替(=リング描画とは独立)。
        self._cur_marker_xy = None      # 表示位置 (x, y) | None=未確定 or ルート
        self._cur_marker_anim = None    # QVariantAnimation
        self._cur_marker_start = None
        self._cur_marker_end = None
        # ── 新ノード出現アニメ用 ──
        # 直前の update_tree 後の SGFNode id 集合を保持し、次の update_tree で
        # 「新規追加されたノード」を検出する。新規ノードはフェードイン+スケールイン
        # で 250ms / OutCubic 表示する。
        self._prev_node_ids: set = set()
        # ノード id → 出現アニメ進捗 (0.0..1.0)。1.0 で削除する(=定常)。
        self._new_node_progress: dict = {}
        # ノード id → QVariantAnimation。各新ノードに対して独立したアニメを
        # 起動することで、連続クリック時に既存の進行中ノードのアニメが
        # リセットされず、各ノードが追加された時点から個別に 250ms 進む。
        self._new_node_anims: dict = {}
        # ── 静的レイヤの pixmap キャッシュ ──
        # エッジ・ノード本体・ホバーリングを焼いた本体 pixmap。
        # バッジは別 pixmap (_tree_badges_pm) に分離することで、
        # 「バッジ category 確定」のたびに本体まで再描画する重コストを回避する。
        # 動的な現在ノードマーカー(青リング)は毎回インライン描画する。
        # 新ノード出現アニメ進行中はキャッシュ無効化(全インライン描画にフォールバック)。
        self._tree_pm = None       # type: Optional[QPixmap]
        self._tree_pm_key = None   # type: Optional[tuple]
        # ── バッジ専用 pixmap キャッシュ ──
        # 評価バッジ・コメントバッジだけを透明背景に描いた pixmap。
        # 本体 pixmap と分離することで、 バッジ確定時の再生成コストが
        # 「全 static layer (~60ms)」 → 「バッジのみ (~2ms)」 に圧縮される。
        # paintEvent は本体 → バッジ → 動的描画の順で重ね描き。
        # バッジアニメ進行中はこの pixmap を使わず、 全バッジを動的描画する。
        self._tree_badges_pm = None      # type: Optional[QPixmap]
        self._tree_badges_pm_key = None  # type: Optional[tuple]
        # update_tree でキャッシュするシグネチャ(paintEvent では id 比較のみで済む)。
        # _node_only_sig: 本体描画用 = (x, y, move_num, move_color, id)
        # _badges_sig:    バッジ描画用 = (x, y, category, has_comment, id)
        # 両者を分けることで「category 変化がバッジ pixmap だけ無効化」のように、
        # キャッシュ無効化を局所化できる。
        self._cached_node_only_sig: tuple = ()
        self._cached_badges_sig: tuple = ()
        # ── 「直前の update_tree が同じ行の移動だったか」フラグ ──
        # _refresh_board がリングアニメ起動方法を選択するために参照する。
        # True なら統合アニメ(_animate_unified)でスクロールとリング絶対座標を
        # 1つのアニメで同期駆動、 False なら絶対座標アニメ(_start_cur_marker_anim)
        # または瞬時切替。
        self._last_move_same_row: bool = False
        self._last_move_new_xy = None  # 同じ行の移動時の新ノード絶対座標

        # ── 評価バッジのフェードイン+スケールアニメ ──
        # 「新たに blunder が付与されたノード」のバッジに対して 1 回だけ
        # フェードイン(opacity 0→1) + スケールイン(0.6→1.0) アニメを表示する。
        # 仕組み:
        # ・update_tree で新規 blunder 検出時に開始時刻を _badge_anim_start に登録
        # ・paintEvent では「進行中アニメがあれば」バッジ pixmap を使わず、
        #   全バッジを動的描画する(アニメ対象は scale + opacity 適用、
        #   非対象は等倍・不透明で通常描画)
        # ・本体 pixmap (_tree_pm) には影響しない=キャッシュ再生成発生なし
        self._badge_anim_start: dict = {}     # id(node) → 開始時刻(monotonic 秒)
        self._badge_anim_driver = None        # QVariantAnimation(駆動用)
        # 既知 blunder ノード id 集合(=次回 update_tree で新規検出する基準)
        self._known_blunder_node_ids: set = set()
        # アニメ時間。 フェードとスケールに同じ時間を適用する。
        self._BADGE_ANIM_MS = 250

    @_profile_method("BranchTree.update_tree")
    def update_tree(self, game_state: "GameState", node_analyses: dict = None):
        """ツリーを再構築して再描画。
        現在ノードのハイライトリング(通常ノード)は、旧位置 → 新位置を
        QVariantAnimation で滑らかに補間する(ルートノードはアニメ対象外)。
        """
        self._nodes = []
        self._node_analyses = node_analyses or {}
        if not game_state:
            self.update()
            return

        current = game_state.current_node
        root = game_state.game.root
        self._layout_tree(root, current)

        # 現在ノードの新しい位置を取得
        # ルートノードも通常ノードと同じ円形+円リングで選択中表現するため、
        # ルートかどうかの特殊扱いは行わない。
        new_xy = None
        for x, y, node, is_cur, move_num in self._nodes:
            if is_cur:
                new_xy = (x, y)
                break

        # 通常ノード→通常ノード の移動だけアニメ補間する。
        # 旧位置(self._cur_marker_xy) と新位置の両方が確定していれば補間。
        # 「同じ行(同じ y 座標)」の移動の場合は、 _refresh_board が
        # 「リング絶対座標」と「スクロール」を1つの統合アニメで駆動する。
        # ここではアニメを起動せず、 _last_move_same_row でその状況を通知する。
        same_row = (
            new_xy is not None
            and self._cur_marker_xy is not None
            and self._cur_marker_xy[1] == new_xy[1]
        )
        # 同じ行フラグ・新ノード絶対座標を _refresh_board に公開
        self._last_move_same_row = bool(same_row)
        self._last_move_new_xy = new_xy if same_row else None
        if same_row:
            # _refresh_board の統合アニメに任せる(ここでは何もしない、
            # _cur_marker_xy は旧値のまま、進行中アニメもそのまま)。
            pass
        elif (new_xy is not None
                and self._cur_marker_xy is not None
                and self._cur_marker_xy != new_xy):
            self._start_cur_marker_anim(self._cur_marker_xy, new_xy)
        else:
            # 初回 or 同一位置 → 瞬時
            if self._cur_marker_anim is not None:
                # disconnect してから stop することで finished の発火を防ぐ
                try:
                    self._cur_marker_anim.finished.disconnect()
                except Exception:
                    pass
                try:
                    self._cur_marker_anim.valueChanged.disconnect()
                except Exception:
                    pass
                self._cur_marker_anim.stop()
            self._cur_marker_xy = new_xy

        # ── 新ノード出現アニメ ──
        # 現在の SGFNode id 集合と前回の集合を比較し、新規追加ノードを抽出。
        # 初回(_prev_node_ids が空) と一括追加(閾値超過)はアニメせず即座表示。
        # 各新ノードに対して独立したアニメを起動するので、連続クリック時でも
        # 既存の進行中アニメには影響せず、各ノードが追加時点から個別に進む。
        cur_ids = {id(n) for _, _, n, _, _ in self._nodes}
        new_ids = cur_ids - self._prev_node_ids
        if self._prev_node_ids and 0 < len(new_ids) <= self._NEW_NODE_ANIM_MAX_COUNT:
            for nid in new_ids:
                self._new_node_progress[nid] = 0.0
                self._start_new_node_anim_for(nid)
        # 削除されたノードの進捗とアニメは破棄(描画対象外なので)
        for nid in list(self._new_node_progress.keys()):
            if nid not in cur_ids:
                self._stop_new_node_anim_for(nid)
                self._new_node_progress.pop(nid, None)
        self._prev_node_ids = cur_ids

        # ── 2 種類のシグネチャをここで計算してキャッシュ ──
        # paintEvent の各 pixmap キャッシュキーは N に比例した tuple。
        # paintEvent ごとに O(N) で計算 + 比較するのは無駄なので、 ここで
        # 1 回だけ計算してキャッシュする。 _node_analyses は update_tree でのみ
        # 切替わるため、 次の update_tree まで有効。
        # _node_only_sig: 本体描画(エッジ+ノード+ホバーリング)に影響する要素のみ
        # _badges_sig:    バッジ描画(評価+コメント)に影響する要素のみ
        # 分割することで「category 確定でバッジ pixmap だけ無効化、 本体は維持」
        # のような局所的なキャッシュ無効化が可能になる。
        node_only_items = []
        badges_items = []
        cur_blunder_ids = set()
        for x, y, n, _is_cur, move_num in self._nodes:
            ma = self._node_analyses.get(id(n))
            cat = ma.blunder.category if (ma and ma.blunder) else None
            has_cmt = bool(n.comment)
            # 本体描画用: ノード位置・色・id (バッジ情報は含まない)
            node_only_items.append(
                (x, y, move_num, n.move_color or "", id(n))
            )
            # バッジ描画用: ノード位置・category・comment 有無・id
            badges_items.append(
                (x, y, cat, has_cmt, id(n))
            )
            # 表示対象 blunder ノードを集合化(アニメ起動判定用)
            if cat is not None:
                badge_color = T().BLUNDER.get(cat)
                if badge_color:
                    cur_blunder_ids.add(id(n))
        self._cached_node_only_sig = tuple(node_only_items)
        self._cached_badges_sig = tuple(badges_items)

        # ── 新規 blunder バッジに対するアニメ起動 ──
        # 既知集合との差分が新規。 初回(_known_blunder_node_ids が空)は
        # 既存解析結果の一括反映なのでアニメせず即時表示。
        # 「新ノード出現アニメ」と被るケース(新規ノード+blunder付き)では、
        # 新ノード出現アニメで一緒に出てくるためバッジアニメ対象から除外。
        first_seen = not self._known_blunder_node_ids
        new_blunder_ids = cur_blunder_ids - self._known_blunder_node_ids
        if not first_seen and new_blunder_ids:
            now_t = time.monotonic()
            any_started = False
            for nid in new_blunder_ids:
                if nid in self._new_node_progress:
                    continue  # 新ノード出現アニメで一体表示
                self._badge_anim_start[nid] = now_t
                any_started = True
            if any_started:
                self._start_badge_anim_driver()
        # 削除/blunder解除されたノードはアニメ状態からも除去
        for nid in list(self._badge_anim_start.keys()):
            if nid not in cur_blunder_ids:
                self._badge_anim_start.pop(nid, None)
        self._known_blunder_node_ids = cur_blunder_ids

        self.update()

    def _start_new_node_anim_for(self, nid: int):
        """指定ノード id 用の独立した出現アニメを起動。
        既に他のノード用にアニメが進行中でも、このノードだけ 0.0 から 250ms で
        進む。複数の新ノードがある場合は同時並行で複数のアニメが走る。"""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        # 既に同 nid のアニメがあれば停止して再起動(通常は来ないが安全策)
        old = self._new_node_anims.pop(nid, None)
        if old is not None:
            old.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(self._NEW_NODE_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        # クロージャで nid を捕捉して、このアニメは指定ノードの progress のみ更新
        def _on_changed(v, _nid=nid):
            with _profile("BranchTree.anim.new_node"):
                try:
                    t = float(v)
                except (TypeError, ValueError):
                    return
                # ノードが既に削除されていれば何もしない
                if _nid in self._new_node_progress:
                    self._new_node_progress[_nid] = t
                    self.update()
        def _on_finished(_nid=nid):
            # アニメ完了: 該当ノードの progress を片付け、anim 参照も破棄
            self._new_node_progress.pop(_nid, None)
            self._new_node_anims.pop(_nid, None)
            self.update()
        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._new_node_anims[nid] = anim
        anim.start()

    def _stop_new_node_anim_for(self, nid: int):
        """指定ノード id 用のアニメがあれば停止して破棄する。
        ノードが削除された(分岐削除等)際に呼ばれる。"""
        anim = self._new_node_anims.pop(nid, None)
        if anim is not None:
            anim.stop()

    def _start_badge_anim_driver(self):
        """評価バッジのフェード+スケールアニメ駆動(既に走っていれば何もしない)。
        個々のバッジの進捗は paintEvent 側で
            t = (now - _badge_anim_start[nid]) / _BADGE_ANIM_MS
        で計算する(=このアニメ自体は単に「定期的に update() を呼ぶ」役割)。
        全バッジが完了(_badge_anim_start が空)になったら自動停止。

        重要: アニメ完了時に _tree_pm のリセットは行わない。
        本体 pixmap (_tree_pm) はアニメと無関係に維持され、 アニメは「バッジを
        動的描画するかどうか」だけで切り替えるため、 pixmap 再生成は発生しない。
        """
        from PyQt6.QtCore import QVariantAnimation
        if self._badge_anim_driver is not None:
            return  # 既に走行中
        anim = QVariantAnimation(self)
        # 駆動だけが目的なので duration は十分長く、 valueChanged で update() のみ
        # 呼ぶ。 進捗判定は描画時に monotonic 時刻で行うため、 アニメの値は使わない。
        # _badge_anim_start が空になった時点で停止する。
        anim.setDuration(60_000)  # 60秒(早期終了するので実値は気にしない)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        def _on_changed(_v):
            now_t = time.monotonic()
            done = []
            for nid, start in self._badge_anim_start.items():
                if (now_t - start) * 1000.0 >= self._BADGE_ANIM_MS:
                    done.append(nid)
            for nid in done:
                self._badge_anim_start.pop(nid, None)
            if not self._badge_anim_start:
                # アニメ停止: 参照を破棄(再起動可能に)
                a = self._badge_anim_driver
                self._badge_anim_driver = None
                if a is not None:
                    try:
                        a.valueChanged.disconnect()
                    except Exception:
                        pass
                    a.stop()
                self.update()  # 通常パスに戻すための最終再描画
                return
            self.update()
        anim.valueChanged.connect(_on_changed)
        self._badge_anim_driver = anim
        anim.start()

    def _start_cur_marker_anim(self, start_xy, end_xy):
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        # 前回のアニメインスタンスがあれば disconnect → stop → 破棄
        # currentValue() の状態が残らないよう、 毎回新インスタンスを作る。
        old_anim = self._cur_marker_anim
        if old_anim is not None:
            try:
                old_anim.valueChanged.disconnect()
            except Exception:
                pass
            try:
                old_anim.finished.disconnect()
            except Exception:
                pass
            try:
                old_anim.stop()
            except Exception:
                pass
            try:
                old_anim.deleteLater()
            except Exception:
                pass
        anim = QVariantAnimation(self)
        anim.setDuration(self._CUR_MARKER_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        self._cur_marker_anim = anim
        # 起点は現在の表示位置(進行中アニメも途中値で正しく繋ぐ)
        sx, sy = self._cur_marker_xy if self._cur_marker_xy else start_xy
        self._cur_marker_start = (float(sx), float(sy))
        self._cur_marker_end   = (float(end_xy[0]), float(end_xy[1]))
        anim.valueChanged.connect(self._on_cur_marker_anim_changed)
        anim.start()

    @_profile_method("BranchTree.anim.cur_marker")
    def _on_cur_marker_anim_changed(self, t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            return
        sx, sy = self._cur_marker_start
        ex, ey = self._cur_marker_end
        self._cur_marker_xy = (sx + (ex - sx) * t, sy + (ey - sy) * t)
        self.update()

    def current_node_xy(self) -> tuple:
        """現在ノードの描画座標 (x, y) を返す。なければ None。"""
        for x, y, node, is_cur, _ in self._nodes:
            if is_cur:
                return (x, y)
        return None

    @_profile_method("BranchTree.layout")
    def _layout_tree(self, root, current):
        """
        全ノードを表示する(横スクロール対応)。

        行配置の方針(シンプルな単純積み下げ):
          - 行0 はメインライン専用 (root から children[0] を辿る道)。
          - 第1子は親と同じ行を継続。
          - 第2子以降の分岐は、それ以前に既に使われた最大行 + 1 に配置。
            つまり「分岐は常に下に追加」され、分岐の分岐もネストするほど
            さらに下に積まれる。区間パッキングは行わないため、横方向に
            重ならない場合でも常に新行が確保される。
        """
        r0     = self._node_r          # フォントサイズ連動の基準半径
        badge_r = max(4, r0 // 2)     # バッジ半径（paintEventのBADGE_Rと同値）
        MARGIN = r0 + 8               # 外周余白（バッジが上端に収まるよう余裕を確保）
        STEP_X = r0 * 2 + r0 // 2 + badge_r + 2  # バッジがはみ出す分の余白＋微調整
        STEP_Y = r0 * 2 + r0 // 2 + badge_r + 2

        def traverse(node, depth, row) -> int:
            """ノードを (depth, row) に配置し、自身のサブツリーが使った
            最大の行番号を返す。親側ではこの戻り値を使って次の分岐の
            配置行(= last_used + 1)を決める。"""
            x = MARGIN + depth * STEP_X
            y = MARGIN + row * STEP_Y
            is_cur = node is current
            self._nodes.append((x, y, node, is_cur, depth))

            if not node.children:
                return row

            # 第1子: 親と同じ行を継続
            last_used = traverse(node.children[0], depth + 1, row)

            # 第2子以降: 既に使われた最大行 + 1 に配置(常に下に積み下げる)
            for child in node.children[1:]:
                child_row = last_used + 1
                last_used = traverse(child, depth + 1, child_row)

            return last_used

        traverse(root, 0, 0)

        max_y = max((y for _,y,_,_,_ in self._nodes), default=0) if self._nodes else 0
        max_x = max((x for x,_,_,_,_ in self._nodes), default=0) if self._nodes else 0
        content_h = max(36, max_y + MARGIN + self._node_r * 2)
        content_w = max_x + MARGIN + self._node_r * 2
        self.setMinimumHeight(content_h)
        # QScrollArea に横スクロールさせるため setMinimumWidth は 0 のまま
        # ウィジェットサイズをコンテンツに合わせて設定する
        self.setMinimumWidth(0)
        self.resize(content_w, content_h)

    @_profile_method("BranchTree.paint")
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self._node_r
        node_map = {id(n): (x,y) for x,y,n,_,_ in self._nodes}

        # ── ビューポート外クリッピング ──
        # BranchTreeWidget は QScrollArea 内に置かれ、コンテンツは数百ノードに
        # なり得る。paintEvent は ev.rect() の領域だけ更新すればよいので、
        # その矩形外にあるノード/エッジは描画をスキップする。手数 N に比例した
        # 描画コストが、表示中のノード数に比例するコストに削減される。
        clip_rect = ev.rect()
        # ノード半径 + バッジ半径 + 線幅余裕。エッジは隣接ノード間しか走らない
        # ので、ノード基準で margin を取れば取りこぼしなく判定できる。
        BADGE_R_CLIP = max(4, self._node_r // 2)
        clip_margin = self._node_r + BADGE_R_CLIP + 4
        clip_x1 = clip_rect.left() - clip_margin
        clip_y1 = clip_rect.top() - clip_margin
        clip_x2 = clip_rect.right() + clip_margin
        clip_y2 = clip_rect.bottom() + clip_margin

        def _in_clip(x: float, y: float) -> bool:
            return clip_x1 <= x <= clip_x2 and clip_y1 <= y <= clip_y2

        # ── 静的レイヤキャッシュ判定(本体 pixmap + バッジ pixmap の 2 種) ──
        # 新ノード出現アニメ進行中はキャッシュ無効化(全インライン描画にフォールバック)。
        # それ以外は 2 種類の pixmap キャッシュをそれぞれ独立にヒット判定する:
        # ・_tree_pm        本体: エッジ + ノード本体 + ホバーリング (バッジ含まず)
        #                   キー: _cached_node_only_sig (category 等を含まない)
        #                   → ホイール中・ponder 中とも変化しないため再生成稀
        # ・_tree_badges_pm バッジ: 評価バッジ + コメントバッジ
        #                   キー: _cached_badges_sig (category, has_comment 含む)
        #                   → ponder で blunder 確定時に再生成 (~2ms と軽量)
        # 加えて、 バッジアニメ進行中はバッジ pixmap を使わず、 全バッジを動的描画。
        anim_in_progress = any(t < 1.0 for t in self._new_node_progress.values()) \
            if self._new_node_progress else False
        BADGE_R = max(4, self._node_r // 2)
        node_r = r

        if not anim_in_progress and self._nodes:
            # 静的レイヤのシグネチャ生成(update_tree でキャッシュ済み、 id 比較のみ)
            # テーマインスタンスは set_mode で内部状態が変わるだけで id() は
            # 不変なので、is_dark を含めてキーにする(モード切替時に再生成)。
            theme_id = (id(T()), T().is_dark)
            hover_id = id(self._hovered) if self._hovered is not None else None
            node_only_sig = self._cached_node_only_sig
            badges_sig = self._cached_badges_sig
            w = self.width()
            h = self.height()
            dpr = self.devicePixelRatioF()
            # 本体 pixmap キー: ノード位置・色・id・ホバー・テーマのみ
            tree_key = (
                w, h, round(dpr, 3), r, hover_id, theme_id, node_only_sig,
            )
            # バッジ pixmap キー: バッジ位置・category・comment・テーマのみ
            badges_key = (
                w, h, round(dpr, 3), BADGE_R, theme_id, badges_sig,
            )

            from PyQt6.QtGui import QPixmap

            # ── 本体 pixmap の生成/再利用 ──
            if self._tree_pm is None or self._tree_pm_key != tree_key:
                if w > 0 and h > 0:
                    pix_w = int(w * dpr)
                    pix_h = int(h * dpr)
                    pix = QPixmap(pix_w, pix_h)
                    pix.setDevicePixelRatio(dpr)
                    pix.fill(Qt.GlobalColor.transparent)
                    pp = QPainter(pix)
                    pp.setRenderHint(QPainter.RenderHint.Antialiasing)
                    self._draw_static_layer(pp, r, BADGE_R, node_r, node_map,
                                            include_all=True)
                    pp.end()
                    self._tree_pm = pix
                    self._tree_pm_key = tree_key

            # ── バッジ pixmap の生成/再利用 ──
            if self._tree_badges_pm is None or self._tree_badges_pm_key != badges_key:
                if w > 0 and h > 0:
                    pix_w = int(w * dpr)
                    pix_h = int(h * dpr)
                    bpix = QPixmap(pix_w, pix_h)
                    bpix.setDevicePixelRatio(dpr)
                    bpix.fill(Qt.GlobalColor.transparent)
                    bpp = QPainter(bpix)
                    bpp.setRenderHint(QPainter.RenderHint.Antialiasing)
                    self._draw_badges_layer(bpp, BADGE_R, node_r)
                    bpp.end()
                    self._tree_badges_pm = bpix
                    self._tree_badges_pm_key = badges_key

            # ── 描画順 ──
            # 1. 本体 pixmap (エッジ + ノード本体 + ホバー)
            # 2. 現在ノード本体を動的に上書き(static の古い is_cur 状態を更新)
            # 3. バッジ pixmap を drawPixmap (アニメ非中)
            #    OR バッジを動的描画(アニメ中)
            # 4. 青リング動的
            # 5. 現在ノードバッジ動的(青リングに被さる切れを防ぐため再描画)
            #    アニメ中は不要(3 で全バッジ描画済み + 描画順は青リングより後で OK)

            # 1. 本体 pixmap
            if self._tree_pm is not None:
                p.drawPixmap(0, 0, self._tree_pm)

            # 2. 現在ノード本体を動的に上書き
            cur_x = cur_y = cur_node = cur_move_num = None
            for _x, _y, _n, _is_cur, _mn in self._nodes:
                if _is_cur:
                    cur_x, cur_y, cur_node, cur_move_num = _x, _y, _n, _mn
                    break
            if cur_node is not None:
                color = cur_node.move_color
                if color == "B":
                    p.setBrush(QBrush(T().STONE_BLACK))
                    p.setPen(QPen(T().STONE_BORDER_BLACK, 1))
                elif color == "W":
                    p.setBrush(QBrush(QColor("#ffffff")))
                    p.setPen(QPen(T().STONE_BORDER_WHITE, 1))
                else:
                    root_bg = QColor(EVAL_COLORS[None]["main"])
                    p.setBrush(QBrush(root_bg))
                    p.setPen(QPen(QColor(136, 136, 136), 1))
                p.drawEllipse(QPointF(cur_x, cur_y), node_r, node_r)
                text_color = QColor(17, 17, 17) if color == "W" else QColor(255, 255, 255)
                p.setPen(QPen(text_color))
                p.setFont(self.NODE_FONT)
                fm = p.fontMetrics()
                label = str(cur_move_num)
                tw = fm.horizontalAdvance(label)
                cap_h = fm.capHeight()
                p.drawText(QPointF(cur_x - tw / 2, cur_y + cap_h / 2), label)

            # 3. バッジ: アニメ中は動的描画、 アニメ非中は pixmap drawPixmap
            badge_anim_active = bool(self._badge_anim_start)
            if badge_anim_active:
                # 全バッジを動的描画(アニメ対象は scale + opacity 適用)
                self._draw_badges_dynamic(p, BADGE_R, node_r,
                                         anim_in_progress=True)
            else:
                # 通常: バッジ pixmap を一発描画
                if self._tree_badges_pm is not None:
                    p.drawPixmap(0, 0, self._tree_badges_pm)

            # 4. 青リング(現在ノードマーカー)
            # 移動アニメ中は補間位置に、定常時は現在ノード位置に描画する。
            # ルートが現在ノードのときは _cur_marker_xy = None なのでスキップ。
            # 半径は r+3(整数)。r+2.5(小数)だと中心整数 + pen=2.0 の組み合わせで
            # 12時/3時/6時/9時方向のピクセルが 3 ピクセルに分散して薄く滲むため、
            # 整数半径にして 2 ピクセルに集中させる(実証済み)。
            if self._cur_marker_xy is not None:
                cx, cy = self._cur_marker_xy
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(T().ACCENT, 2.0))
                p.drawEllipse(QPointF(float(cx), float(cy)), r + 3, r + 3)

            # 5. 現在ノードバッジを青リングの **上** に再描画(切れ防止)
            #    アニメ中は 3 で動的描画済み + アニメ進捗が反映されているので、
            #    ここで再描画すると 2 重になる。 アニメ中はスキップ。
            if not badge_anim_active and cur_node is not None:
                self._draw_badges_dynamic(p, BADGE_R, node_r,
                                         anim_in_progress=False,
                                         current_node_id=id(cur_node))

            p.end()
            return

        # ── アニメ進行中 or ノードなし: 既存のインライン描画パス ──
        # アニメ中は static cache を無効化して全部描き直す(挙動互換のため)。

        # エッジを先に描画（親の右端 → 子の左端）
        # 子ノードが「出現アニメ中」の新ノードである場合、その線も同じ進捗 t で
        # 不透明度を補間する(ノードと線で違和感なく出現)。
        with _profile("BranchTree.paint.edges"):
            p.setPen(QPen(T().BORDER, 1))
            for x,y,node,_,_ in self._nodes:
                # 親が clip 外で、かつどの子も clip 外ならエッジ描画自体スキップ
                parent_in = _in_clip(x, y)
                for child in node.children:
                    cpos = node_map.get(id(child))
                    if cpos:
                        cx, cy = cpos
                        # 親も子もクリップ外 → エッジは画面外なのでスキップ
                        if not parent_in and not _in_clip(cx, cy):
                            continue
                        path = QPainterPath()
                        path.moveTo(x + r, y)
                        if cy == y:
                            path.lineTo(cx - r, cy)
                        else:
                            mid_x = x + r + (cx - r - x - r) * 0.5
                            path.lineTo(mid_x, y)
                            path.lineTo(mid_x, cy)
                            path.lineTo(cx - r, cy)
                        edge_t = self._new_node_progress.get(id(child))
                        if edge_t is not None and edge_t < 1.0:
                            prev_op = p.opacity()
                            p.setOpacity(prev_op * edge_t)
                            p.drawPath(path)
                            p.setOpacity(prev_op)
                        else:
                            p.drawPath(path)

        # ノードを描画

        with _profile("BranchTree.paint.nodes"):
            for x,y,node,is_cur,move_num in self._nodes:
                # クリップ外のノードはスキップ
                if not _in_clip(x, y):
                    continue
                # 新ノード出現アニメ: 進捗 t に応じて中心固定スケール (0.6→1.0) と
                # 不透明度 (0.0→1.0) を補間する。t=1.0 (=エントリ無し) のときは
                # save/restore も行わない(既存挙動と同じ)。
                new_t = self._new_node_progress.get(id(node))
                anim_node = (new_t is not None and new_t < 1.0)
                if anim_node:
                    p.save()
                    # 中心固定スケール: translate(x,y) → scale(s,s) → translate(-x,-y)
                    scale_s = 0.6 + 0.4 * new_t
                    p.translate(x, y)
                    p.scale(scale_s, scale_s)
                    p.translate(-x, -y)
                    p.setOpacity(p.opacity() * new_t)

                color = node.move_color
                hov = self._hovered and self._hovered is node

                # 石の色は常に本来の色（黒/白）で描画。
                # 白石は ScoreBoard・_StoneIcon と同じ純白 #ffffff を使用してアプリ全体で統一。
                # ルートノード(move_num==0)は手番がまだ無いので「不明」カテゴリ色 #6a7888 を使う。
                if color == "B":
                    p.setBrush(QBrush(T().STONE_BLACK)); p.setPen(QPen(T().STONE_BORDER_BLACK, 1))
                elif color == "W":
                    p.setBrush(QBrush(QColor("#ffffff"))); p.setPen(QPen(T().STONE_BORDER_WHITE, 1))
                else:
                    # ルート(move_num==0)のみここに来る想定。BadgeWidget の不明ピルと
                    # ビジュアルを揃えるため #6a7888 背景 + 細い枠で統一。
                    root_bg = QColor(EVAL_COLORS[None]["main"])  # #6a7888
                    p.setBrush(QBrush(root_bg)); p.setPen(QPen(QColor(136,136,136), 1))

                # 全ノードを基本半径 r で統一
                node_r = r

                # ── ノード本体: 全て円形(ルート含む) ──
                # ルートも他のノードと同じ円形で描画する。選択中表現(青リング)も
                # _cur_marker_xy 経由で他のノードと共通処理されるため、特殊扱い不要。
                p.drawEllipse(QPointF(x, y), node_r, node_r)

                # 手数をノード内に表示（描画矩形を石と同サイズにして正確に中央揃え）
                # ルート: 白文字、 黒石: 白文字、白石: 黒文字
                if color == "B":
                    text_color = QColor(255,255,255)
                elif color == "W":
                    text_color = QColor(17,17,17)
                else:
                    # ルート: #6a7888 背景に対して白文字
                    text_color = QColor(255,255,255)
                p.setPen(QPen(text_color))
                p.setFont(self.NODE_FONT)
                fm = p.fontMetrics()
                label = str(move_num)
                tw = fm.horizontalAdvance(label)
                cap_h = fm.capHeight()
                p.drawText(QPointF(x - tw / 2, y + cap_h / 2), label)

                # 選択中の青リングはループ外で _cur_marker_xy 位置に描画する
                # ことで移動アニメ可能にしているため、ここでは描画しない。

                # ホバー時にリング
                if hov and not is_cur:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(T().ACCENT, 2))
                    p.drawEllipse(QPointF(x, y), r, r)

                # 評価バッジ・コメントバッジは描画順の都合でループ外(青リングの後)
                # に分離して描画する。ここでは何もしない。

                # 新ノード出現アニメ: イテレーション開始時の save に対応する restore
                if anim_node:
                    p.restore()

            # ── 現在ノードマーカー(通常ノード用の青リング) ──
            # 移動アニメ中は補間位置に、定常時は現在ノード位置に描画する。
            # ルートが現在ノードのときは _cur_marker_xy = None なのでここはスキップ。
            # 半径は r+3(整数)。理由は static パス側のコメント参照。
            if self._cur_marker_xy is not None:
                cx, cy = self._cur_marker_xy
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(T().ACCENT, 2.0))
                p.drawEllipse(QPointF(float(cx), float(cy)), r + 3, r + 3)

        # ── バッジ描画パス(青リングより後に描画して上に重ねる) ──
        # 評価バッジが青リングに隠れないよう、ノード本体・青リングの後で描画する。
        # 新ノード出現アニメの save/restore はノード本体ループと同様に適用する。
        node_r = r
        with _profile("BranchTree.paint.badges"):
            for x, y, node, is_cur, move_num in self._nodes:
                # クリップ外のノードはバッジ描画もスキップ
                if not _in_clip(x, y):
                    continue
                new_t = self._new_node_progress.get(id(node))
                anim_node = (new_t is not None and new_t < 1.0)
                if anim_node:
                    p.save()
                    scale_s = 0.6 + 0.4 * new_t
                    p.translate(x, y)
                    p.scale(scale_s, scale_s)
                    p.translate(-x, -y)
                    p.setOpacity(p.opacity() * new_t)

                # ── 右上: 評価バッジ(best以上・緩手以上) ──
                ma = self._node_analyses.get(id(node))
                if ma and ma.blunder:
                    badge_color = T().BLUNDER.get(ma.blunder.category)
                    if badge_color:
                        bx = x + node_r * 0.90
                        by = y - node_r * 0.90
                        p.setBrush(QBrush(badge_color))
                        p.setPen(QPen(T().PANEL2, 1))
                        p.drawEllipse(QPointF(bx, by), BADGE_R, BADGE_R)

                # ── 左上: コメントバッジ(共通ヘルパー) ──
                if node.comment:
                    bx = x - node_r * 0.90
                    by = y - node_r * 0.90
                    self._draw_comment_badge(p, bx, by, BADGE_R)

                if anim_node:
                    p.restore()

        p.end()

    def _draw_static_layer(self, p, r, BADGE_R, node_r, node_map, include_all=True):
        """エッジ・ノード本体・ホバーリングを描画する(本体 pixmap 用)。
        バッジは別 pixmap (_tree_badges_pm) に分離した(_draw_badges_layer)。
        この pixmap はバッジ category 確定の影響を受けないため、 ホイール連続中
        の頻繁な再生成を回避できる。
        現在ノードマーカー(青リング、_cur_marker_xy)は動的なのでここでは描画しない。
        新ノード出現アニメ中はこの関数は呼ばれない(既存のインライン描画にフォールバック)。
        """
        # エッジ描画
        with _profile("BranchTree.paint.edges"):
            p.setPen(QPen(T().BORDER, 1))
            for x,y,node,_,_ in self._nodes:
                for child in node.children:
                    cpos = node_map.get(id(child))
                    if cpos:
                        cx, cy = cpos
                        path = QPainterPath()
                        path.moveTo(x + r, y)
                        if cy == y:
                            path.lineTo(cx - r, cy)
                        else:
                            mid_x = x + r + (cx - r - x - r) * 0.5
                            path.lineTo(mid_x, y)
                            path.lineTo(mid_x, cy)
                            path.lineTo(cx - r, cy)
                        # アニメ非中なので edge_t は無視(キャッシュ条件で除外済み)
                        p.drawPath(path)

        # ノード本体描画(ホバーリングを含む)
        # 全ノードを描画する(is_cur のノードも含む)。 paintEvent 側で
        # 現在ノードの本体を再度動的描画して上書きする方式により、
        # 現在ノードが変わっても static layer のキャッシュは再利用できる。
        with _profile("BranchTree.paint.nodes"):
            for x,y,node,is_cur,move_num in self._nodes:
                color = node.move_color
                hov = self._hovered and self._hovered is node

                if color == "B":
                    p.setBrush(QBrush(T().STONE_BLACK)); p.setPen(QPen(T().STONE_BORDER_BLACK, 1))
                elif color == "W":
                    p.setBrush(QBrush(QColor("#ffffff"))); p.setPen(QPen(T().STONE_BORDER_WHITE, 1))
                else:
                    # ルート(move_num==0): 「不明」バッジと同じ #6a7888 背景 + 細い枠
                    root_bg = QColor(EVAL_COLORS[None]["main"])
                    p.setBrush(QBrush(root_bg)); p.setPen(QPen(QColor(136,136,136), 1))

                # ノード本体: 全て円形(ルート含む)
                p.drawEllipse(QPointF(x, y), node_r, node_r)

                # ノード内テキスト: ルート/黒石は白文字、白石は黒文字
                if color == "W":
                    text_color = QColor(17,17,17)
                else:
                    text_color = QColor(255,255,255)
                p.setPen(QPen(text_color))
                p.setFont(self.NODE_FONT)
                fm = p.fontMetrics()
                label = str(move_num)
                tw = fm.horizontalAdvance(label)
                cap_h = fm.capHeight()
                p.drawText(QPointF(x - tw / 2, y + cap_h / 2), label)

                # ホバー時にリング(現在ノードでない場合)
                if hov and not is_cur:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(T().ACCENT, 2))
                    p.drawEllipse(QPointF(x, y), r, r)

    def _draw_comment_badge(self, p, bx, by, BADGE_R):
        """コメントバッジ(正円 + ドット 3 つ)を描画。
        中心 (bx, by)、半径 BADGE_R の正円。評価バッジと同じ仕様(枠線 PANEL2)
        で揃え、塗り色とドット色はスライダー横コメントアイコンと統一。
        - 円: 半径 BADGE_R、塗り = T().ICON_DIM(本体色)、枠 = PANEL2(太さ 1)
          → ダーク: 白塗り、ライト: 少し薄めのグレー(#444444)塗り。
            スライダー横コメントアイコンの本体色と一致。
        - 内部: ドット 3 つ(T().BG 色)を中央水平線に等間隔配置
          スライダー横コメントアイコンは透過処理(背景透け)で、通常時は
          T().BG 色になる。それと色を揃えるため T().BG をドット色に採用。
        """
        # 本体の正円(塗り = T().ICON_DIM、テーマ追従)
        p.setBrush(QBrush(T().ICON_DIM))
        p.setPen(QPen(T().PANEL2, 1.0))
        p.drawEllipse(QPointF(bx, by), BADGE_R, BADGE_R)

        # 内部ドット 3 つ(中央水平、等間隔)
        # ドット間隔 = BADGE_R * 0.55(中央 dot から左右に展開)
        # ドット半径 = BADGE_R * 0.18(BADGE_R=6 のとき r ≒ 1.08)
        dot_r = max(0.8, BADGE_R * 0.18)
        gap = BADGE_R * 0.55
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(T().BG))
        for i in range(3):
            dx = bx + (i - 1) * gap
            p.drawEllipse(QPointF(dx, by), dot_r, dot_r)

    def _draw_badges_layer(self, p, BADGE_R, node_r):
        """バッジ専用 pixmap への描画(評価バッジ + コメントバッジ)。
        本体 pixmap (_draw_static_layer) と分離することで、 バッジ category
        確定時の再生成コストを「全 static layer (~60ms)」から
        「バッジのみ (~2ms)」に圧縮する。
        透明背景の pixmap に描画され、 paintEvent で本体 pixmap の上に重ねられる。
        """
        with _profile("BranchTree.paint.badges"):
            for x, y, node, is_cur, move_num in self._nodes:
                # 評価バッジ
                ma = self._node_analyses.get(id(node))
                if ma and ma.blunder:
                    badge_color = T().BLUNDER.get(ma.blunder.category)
                    if badge_color:
                        bx = x + node_r * 0.90
                        by = y - node_r * 0.90
                        p.setBrush(QBrush(badge_color))
                        p.setPen(QPen(T().PANEL2, 1))
                        p.drawEllipse(QPointF(bx, by), BADGE_R, BADGE_R)
                # コメントバッジ
                if node.comment:
                    bx = x - node_r * 0.90
                    by = y - node_r * 0.90
                    self._draw_comment_badge(p, bx, by, BADGE_R)

    def _draw_badges_dynamic(self, p, BADGE_R, node_r, anim_in_progress, current_node_id=None):
        """バッジを動的描画する(アニメ進行中、 または最前面再描画用)。
        anim_in_progress=True の場合: 全バッジを描画。 _badge_anim_start に
          含まれるノードは scale + opacity を適用してアニメ表現する。
        anim_in_progress=False の場合: current_node_id のノードだけ再描画する
          (青リングの上に上書きしてバッジ切れを防ぐ)。

        重要: スケール変換は中心固定で適用するため、 (translate, scale, translate)
        の流儀で一時的に座標系を変える。 opacity は p.setOpacity で適用。
        いずれも変更後は元の状態に戻す。
        """
        now_t = time.monotonic() if self._badge_anim_start else 0.0
        ANIM_MS = float(self._BADGE_ANIM_MS)

        for x, y, node, is_cur, move_num in self._nodes:
            nid = id(node)
            if anim_in_progress:
                # 全バッジ描画モード(=アニメ中) - 全ノードが対象
                pass
            else:
                # 上書きモード - current_node のみ
                if current_node_id is None or nid != current_node_id:
                    continue

            # アニメ進捗 t (0..1)。 アニメ対象でなければ 1.0(等倍・不透明)
            anim_start = self._badge_anim_start.get(nid)
            if anim_start is not None:
                t = (now_t - anim_start) * 1000.0 / ANIM_MS
                t = 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else t)
                # OutCubic イージング(終わりに向けて減速)
                t_ease = 1.0 - (1.0 - t) ** 3
                scale_s = 0.6 + 0.4 * t_ease
                opacity_s = t_ease
            else:
                scale_s = 1.0
                opacity_s = 1.0

            need_transform = (scale_s != 1.0 or opacity_s != 1.0)
            if need_transform:
                p.save()
                # 中心固定スケール: ノード中心 (x, y) を基準に scale + opacity 適用
                # バッジは右上 (x + node_r*0.90, y - node_r*0.90) と左上 (x - ...) に
                # あるので、 ノード中心からスケールするとバッジ位置もそれに合わせて変わる。
                # 視覚的には「ノード中心からバッジ位置にぴゅっと出てくる」感じになる。
                p.translate(x, y)
                p.scale(scale_s, scale_s)
                p.translate(-x, -y)
                p.setOpacity(p.opacity() * opacity_s)

            # 評価バッジ
            ma = self._node_analyses.get(nid)
            if ma and ma.blunder:
                badge_color = T().BLUNDER.get(ma.blunder.category)
                if badge_color:
                    bx = x + node_r * 0.90
                    by = y - node_r * 0.90
                    p.setBrush(QBrush(badge_color))
                    p.setPen(QPen(T().PANEL2, 1))
                    p.drawEllipse(QPointF(bx, by), BADGE_R, BADGE_R)
            # コメントバッジ
            if node.comment:
                bx = x - node_r * 0.90
                by = y - node_r * 0.90
                self._draw_comment_badge(p, bx, by, BADGE_R)

            if need_transform:
                p.restore()

    @_profile_method("BranchTree.mouse_move")
    def mouseMoveEvent(self, ev):
        pos = ev.position()
        hit = self._node_at(pos.x(), pos.y())
        if hit is not self._hovered:
            self._hovered = hit
            self.update()
        if hit:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    @_profile_method("BranchTree.mouse_press")
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.position()
            hit = self._node_at(pos.x(), pos.y())
            if hit:
                self.node_clicked.emit(hit)
        elif ev.button() == Qt.MouseButton.RightButton:
            pos = ev.position()
            hit = self._node_at(pos.x(), pos.y())
            if hit:
                self._show_node_context_menu(hit, ev.globalPosition().toPoint())

    def _is_branch_node(self, node) -> bool:
        """メインライン（行0）以外のノードかどうかを判定する。"""
        # _nodes の行情報から判定: row=0 がメインライン
        for x, y, n, is_cur, depth in self._nodes:
            if n is node:
                # row は y 座標から逆算: MARGIN + row * STEP_Y
                # メインラインは必ず row=0 (y が最小) なので、
                # 同じ depth の row=0 ノードと y 座標を比較する
                main_y = self._main_y_at_depth(depth)
                return y != main_y
        return False

    def _main_y_at_depth(self, depth: int) -> float:
        """指定 depth におけるメインライン（row=0）の y 座標を返す。"""
        # row=0 は最初に traverse された各 depth のノード
        # _nodes はルートから順に追加されるため、
        # 同一 depth の最初に登録されたノードが row=0
        seen_depths = set()
        for x, y, n, is_cur, d in self._nodes:
            if d == depth and d not in seen_depths:
                seen_depths.add(d)
                return y
        return -1

    def _show_node_context_menu(self, node, global_pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        # ノード右クリックメニューはサブメニューを持たないリーフ
        style_qmenu(menu, leaf=True)
        # 全ノード共通: このノードを起点として以降の手順番号を表示
        # ルートノード(0手目)は着手ではないので、専用のラベルにする
        is_root = (node.parent is None)
        anchor_label = "全手の手順を表示" if is_root else "この手以降の手順を表示"
        anchor_act = menu.addAction(anchor_label)
        # 全ノード共通: コメントを追加/編集
        has_comment = bool(node.comment)
        comment_label = "コメントを編集" if has_comment else "コメントを追加"
        menu.addSeparator()
        comment_act = menu.addAction(comment_label)
        # 分岐ノードのみ: このノードを削除（メインラインと root は不可）
        delete_act = None
        if self._is_branch_node(node):
            menu.addSeparator()
            delete_act = menu.addAction("このノードを削除")
        act = menu.exec(global_pos)
        if act is None:
            return
        if act == anchor_act:
            self.move_number_anchor_requested.emit(node)
        elif act == comment_act:
            self.node_comment_requested.emit(node)
        elif delete_act is not None and act == delete_act:
            self.node_delete_requested.emit(node)

    @_profile_method("BranchTree.node_at")
    def _node_at(self, mx, my):
        r = self._node_r + 3
        for x,y,node,_,_ in self._nodes:
            if (mx-x)**2 + (my-y)**2 <= r**2:
                return node
        return None

    def leaveEvent(self, ev):
        self._hovered = None
        self.update()



class _HelpPopover(QFrame):
    """
    クリックで開閉するポップオーバー。
    - Qt.WindowType.Popup フラグにより、外側クリックで自動的に閉じる
    - テキストはリッチテキスト対応（将来のリンク埋め込みにも対応可）
    - 閉じた時刻を記録することで、? アイコン自身をクリックして閉じた直後に
      再度開いてしまう既知の Qt Popup 動作を回避する
    """
    def __init__(self, text: str, parent: QWidget = None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setObjectName("help_popover")
        self._closed_at_ms: int = 0
        self._label = QLabel(text, self)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setOpenExternalLinks(True)
        self._label.setWordWrap(False)  # 1行表示
        self._label.setFont(Font_XS())
        # QLabel 側で border:none を明示（親 QFrame のスタイル継承を防ぐ）
        self._label.setStyleSheet(
            f"QLabel {{"
            f"  color:{T().TEXT.name()};"
            f"  background:transparent;"
            f"  border:none;"
            f"  padding:0;"
            f"}}"
        )

        # スタイルは objectName に紐付けて子ウィジェットへの継承を防ぐ
        self.setStyleSheet(
            f"QFrame#help_popover {{"
            f"  background:{T().PANEL.name()};"
            f"  border:1px solid {T().BORDER2.name()};"
            f"  border-radius:8px;"
            f"}}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAD_CARD)
        lay.setSpacing(0)
        lay.addWidget(self._label)

        # 内容に合わせてサイズを調整
        self.adjustSize()

    def hideEvent(self, ev):
        from PyQt6.QtCore import QDateTime
        self._closed_at_ms = QDateTime.currentMSecsSinceEpoch()
        super().hideEvent(ev)


class ToggleSwitch(QWidget):
    """iOS風トグルスイッチ。"""
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = True):
        super().__init__()
        self._checked = checked
        self._anim_pos = 1.0 if checked else 0.0  # 0.0=OFF, 1.0=ON
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = None

    def isChecked(self): return self._checked

    def setChecked(self, val: bool):
        """値を変更し、 変化があれば toggled シグナルを発火する。
        Qt 標準の QCheckBox/QRadioButton 等の setChecked と同じ挙動に合わせて
        いる。 値変化なしの場合は発火しない(冗長な再描画/再処理を避ける)。"""
        if self._checked == val:
            return
        self._checked = val
        self._anim_pos = 1.0 if val else 0.0
        self.update()
        self.toggled.emit(val)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self._start_anim()
            self.toggled.emit(self._checked)

    def _start_anim(self):
        from PyQt6.QtCore import QTimer
        target = 1.0 if self._checked else 0.0
        step = 0.15 if self._checked else -0.15

        def _tick():
            self._anim_pos += step
            if (step > 0 and self._anim_pos >= target) or \
               (step < 0 and self._anim_pos <= target):
                self._anim_pos = target
                if self._timer:
                    self._timer.stop()
            self.update()

        from PyQt6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(_tick)
        self._timer.start(16)  # ~60fps

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        r = H / 2

        # トラック
        track_color = QColor(
            int(T().BORDER.red()   + (T().ACCENT.red()   - T().BORDER.red())   * self._anim_pos),
            int(T().BORDER.green() + (T().ACCENT.green() - T().BORDER.green()) * self._anim_pos),
            int(T().BORDER.blue()  + (T().ACCENT.blue()  - T().BORDER.blue())  * self._anim_pos),
        )
        p.setBrush(QBrush(track_color)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, W, H, r, r)

        # つまみ
        thumb_x = 2 + self._anim_pos * (W - H)
        p.setBrush(QBrush(QColor(255,255,255)))
        p.drawEllipse(QPointF(thumb_x + r - 2, H / 2), r - 2, r - 2)
        p.end()


# ── Toggle bar (解析 / 形勢 / 棋力 を1行統合) ──────────────────────────────
class ToggleBar(QWidget):
    """AI解析 / 形勢判断トグルスイッチ＋棋力設定を1行カードで表示。

    レイアウト: 解析[T] | 形勢[T] | あなたの棋力 [ボタン] (stretch)
    """
    ai_toggled        = pyqtSignal(bool)
    ownership_toggled = pyqtSignal(bool)
    move_numbers_toggled = pyqtSignal(bool)
    # 棋力選択UI はメニューバー(MainWindow側)に移動済み。
    # 棋力定数は MainWindow.RANK_OPTIONS / DEFAULT_RANK を参照すること。

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 1行統合カード: 解析[T] | 形勢[T] | あなたの棋力 [ボタン] (stretch) ──
        card = QWidget()
        card.setObjectName("toggle_card")
        card.setStyleSheet(
            f"QWidget#toggle_card {{"
            f"  background:{T().PANEL.name()};"
            f"  border:1px solid {T().BORDER2.name()};"
            f"  border-radius:12px;"
            f"}}"
        )
        outer.addWidget(card)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(*PAD_CARD)
        layout.setSpacing(0)

        # 前回保存したトグル状態を復元（QSettings）。デフォルトは
        # 解析=ON / 形勢=OFF（初回起動時の従来挙動）。
        from PyQt6.QtCore import QSettings
        _qs = QSettings("Kizuki", "Kizuki")
        saved_ai = _qs.value("analysis_enabled", True, type=bool)
        saved_ow = _qs.value("ownership_enabled", False, type=bool)

        # ── AI解析トグル ──
        self._sw_ai = ToggleSwitch(checked=saved_ai)
        self._sw_ai.toggled.connect(self.ai_toggled)
        lbl_ai = QLabel("解析")
        lbl_ai.setFont(Font_MD())
        lbl_ai.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        layout.addWidget(lbl_ai)
        layout.addSpacing(6)
        layout.addWidget(self._sw_ai)

        # セパレーター(トグル間)
        sep1 = QWidget()
        sep1.setFixedWidth(1)
        sep1.setFixedHeight(20)
        sep1.setStyleSheet(f"background:{T().BORDER.name()};")
        layout.addSpacing(12)
        layout.addWidget(sep1)
        layout.addSpacing(12)

        # ── 形勢判断トグル ──
        self._sw_ow = ToggleSwitch(checked=saved_ow)
        self._sw_ow.toggled.connect(self.ownership_toggled)
        lbl_ow = QLabel("形勢")
        lbl_ow.setFont(Font_MD())
        lbl_ow.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        layout.addWidget(lbl_ow)
        layout.addSpacing(6)
        layout.addWidget(self._sw_ow)

        # セパレーター(形勢↔手順)
        sep2 = QWidget()
        sep2.setFixedWidth(1)
        sep2.setFixedHeight(20)
        sep2.setStyleSheet(f"background:{T().BORDER.name()};")
        layout.addSpacing(12)
        layout.addWidget(sep2)
        layout.addSpacing(12)

        # ── 手順番号トグル ──
        # 前回値を復元（デフォルトOFF）。値はメニュー時代と同じキー。
        saved_mn = _qs.value("move_numbers_enabled", False, type=bool)
        self._sw_mn = ToggleSwitch(checked=saved_mn)
        self._sw_mn.toggled.connect(self.move_numbers_toggled)
        lbl_mn = QLabel("手順")
        lbl_mn.setFont(Font_MD())
        lbl_mn.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        layout.addWidget(lbl_mn)
        layout.addSpacing(6)
        layout.addWidget(self._sw_mn)

        # 末尾ストレッチ: 左寄せにして右側に余白を作る
        layout.addStretch()

    def apply_theme(self):
        """テーマ切り替え時にカード背景・ラベル色を再適用する。"""
        # 統合カード
        toggle_card = self.findChild(QWidget, "toggle_card")
        if toggle_card:
            toggle_card.setStyleSheet(
                f"QWidget#toggle_card {{"
                f"  background:{T().PANEL.name()};"
                f"  border:1px solid {T().BORDER2.name()};"
                f"  border-radius:8px;"
                f"}}"
            )
        # ラベル色
        for lbl in self.findChildren(QLabel):
            lbl.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        # セパレータ（QWidget 幅1px）
        for w in self.findChildren(QWidget):
            if w.maximumWidth() == 1 and w.maximumHeight() <= 20 and w.minimumWidth() == 1:
                w.setStyleSheet(f"background:{T().BORDER.name()};")


class WelcomePane(QWidget):
    """
    解析モード起動時に碁盤の代わりに表示するウェルカム画面。
    「SGFを開く」「新規作成」の2枚の DropZone カードを横並びで表示する。
    _DropZoneWidget と同じ点線枠スタイル（ホバーで青枠）を採用。
    """
    open_sgf_requested  = pyqtSignal()   # SGFを開くカードがクリックされた
    new_game_requested  = pyqtSignal()   # 新規作成カードがクリックされた（19路で作成）
    new_game_with_size_requested = pyqtSignal(int)  # カード本体クリック時に発火（選択中サイズ 9/13/19）
    paste_requested     = pyqtSignal()   # 右クリックメニュー「SGFを貼り付け」が選ばれた

    # ── SVG アイコン定義（make_icon ではなく paintEvent 内で直描き） ────

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 縦方向の中央揃え用スペーサー
        # 比率は上:下 = 7:8 で、完全中央(1:1)よりわずかに上寄り。
        # メニューバーが上にあるため、視覚的中心をほんの少し上に置くと
        # 全体のバランスが良い(光学的中心の調整)。
        outer.addStretch(7)

        # ── カード行 ────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(40)  # = SPACING_LARGE。resizeEvent で fluid に変動する
        row.addStretch(1)

        self._card_open = _WelcomeCard(**_WelcomeCard.OPEN_CARD_PARAMS)
        self._card_open.clicked.connect(self.open_sgf_requested)
        row.addWidget(self._card_open)

        self._card_new = _NewGameCard(
            title="新規作成",
            hint="",
        )
        # カード本体クリック: 現在選択されているサイズで新規作成
        # （サイズボタンクリックでは画面遷移しない設計のため、
        #   ここで selected_size() を取って emit する）
        self._card_new.clicked.connect(
            lambda: self.new_game_with_size_requested.emit(
                self._card_new.selected_size()))
        row.addWidget(self._card_new)

        row.addStretch(1)
        # レスポンシブ用に row への参照を保持(resizeEvent で spacing を切替)
        self._row = row

        outer.addLayout(row)
        outer.addStretch(8)

    # ── レスポンシブ: 完全fluidでウィンドウ幅に追従 ────────────────────
    # カード幅が連続的に MAX_W → MIN_W へ縮小し、同期して以下も変化:
    # ・カード高さ: 幅の縮小率に合わせて MAX_H → MIN_H へ線形補間
    # ・カード間スペーシング: 幅と同じ係数で SPACING_LARGE → SPACING_SMALL
    # ・左右マージン: SIDE_MARGIN(最低限)を確保
    SIDE_MARGIN     = 16   # カード行の左右に最低限確保するマージン
    SPACING_LARGE   = 40   # カード幅が MAX のときのカード間スペーシング
    SPACING_SMALL   = 16   # カード幅が MIN のときのカード間スペーシング

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_fluid_layout()

    def _apply_fluid_layout(self):
        """ウィンドウ幅に応じてカードサイズ・スペーシングを連続的に計算して適用する。"""
        Card = _WelcomeCard
        W = self.width()
        # 利用可能幅 = 全幅 - 左右マージン
        avail = max(0, W - self.SIDE_MARGIN * 2)
        # この avail に「カード幅×2 + spacing」が収まる必要がある。
        # spacing もカード幅に応じて変動するため連立を解く必要があるが、
        # 補間係数 t を「カード幅が MIN/MAX 間のどこにあるか」(0..1)とすると、
        #   card_w   = MIN_W + t × (MAX_W - MIN_W)
        #   spacing  = SPACING_SMALL + t × (SPACING_LARGE - SPACING_SMALL)
        # 必要幅 = 2 × card_w + spacing が avail に収まる最大の t を求める。
        # card_w(t) と spacing(t) は t について線形なので、
        # 必要幅(t) = 2×MIN_W + SPACING_SMALL + t × (2×(MAX_W-MIN_W) + (SPACING_LARGE-SPACING_SMALL))
        base   = 2 * Card.MIN_W + self.SPACING_SMALL
        slope  = 2 * (Card.MAX_W - Card.MIN_W) + (self.SPACING_LARGE - self.SPACING_SMALL)
        if slope <= 0:
            t = 1.0
        else:
            t = (avail - base) / slope
        # t を [0, 1] にクランプ
        t = max(0.0, min(1.0, t))
        # カード幅・高さ・スペーシングを補間で確定
        card_w  = int(round(Card.MIN_W + t * (Card.MAX_W - Card.MIN_W)))
        card_h  = int(round(Card.MIN_H + t * (Card.MAX_H - Card.MIN_H)))
        spacing = int(round(self.SPACING_SMALL + t * (self.SPACING_LARGE - self.SPACING_SMALL)))
        # 適用
        for card in (self._card_open, self._card_new):
            card.set_card_size(card_w, card_h)
        self._row.setSpacing(spacing)

    # ── D&D: ドラッグされた棋譜ファイル(SGF/NGF/GIB/UGF)をカード①に転送 ──
    _SUPPORTED_KIFU_EXTS = (".sgf",)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in ev.mimeData().urls()]
            if any(p.lower().endswith(self._SUPPORTED_KIFU_EXTS) for p in paths):
                ev.acceptProposedAction()
                self._card_open.set_drag_hovered(True)
                return
        ev.ignore()

    def dragLeaveEvent(self, ev):
        self._card_open.set_drag_hovered(False)

    def dropEvent(self, ev):
        self._card_open.set_drag_hovered(False)
        paths = [u.toLocalFile() for u in ev.mimeData().urls()
                 if u.toLocalFile().lower().endswith(self._SUPPORTED_KIFU_EXTS)]
        if paths:
            ev.acceptProposedAction()
            # 最初のファイルを開く（パスをシグナルで渡す）
            self.sgf_drop_requested.emit(paths[0])

    sgf_drop_requested = pyqtSignal(str)   # ドロップされた棋譜パス

    def contextMenuEvent(self, ev):
        """ウェルカム画面の右クリックメニュー。
        新規作成（サブメニュー: 19路 / 13路 / 9路）/ 開く / 貼り付け。
        メニューバーの「ファイル」と同じショートカットキーを併記する。
        """
        from PyQt6.QtWidgets import QMenu, QApplication
        from PyQt6.QtGui import QKeySequence, QAction
        menu = QMenu(self)
        # 親メニューは「新規作成」サブメニューを持つので非リーフ
        style_qmenu(menu)
        # ── 新規作成（サブメニュー: 19路盤 / 13路盤 / 9路盤）──────────
        # サブメニューにも同じスタイル+透過化を適用(これはリーフ)
        new_menu = menu.addMenu("新規作成")
        style_qmenu(new_menu, leaf=True)
        new_19 = QAction("19路盤", self)
        new_19.setShortcut(QKeySequence.StandardKey.New)  # Ctrl+N は19路に割当て
        new_menu.addAction(new_19)
        new_13 = QAction("13路盤", self)
        new_menu.addAction(new_13)
        new_9 = QAction("9路盤", self)
        new_menu.addAction(new_9)

        open_act = menu.addAction("開く...")
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        menu.addSeparator()
        paste_act = menu.addAction("貼り付け")
        paste_act.setShortcut(QKeySequence.StandardKey.Paste)
        # クリップボードに何か入っているときだけ有効化（空なら disabled）
        clipboard = QApplication.clipboard()
        if not clipboard or not clipboard.text().strip():
            paste_act.setEnabled(False)

        chosen = menu.exec(ev.globalPos())
        if chosen is new_19:
            self.new_game_with_size_requested.emit(19)
        elif chosen is new_13:
            self.new_game_with_size_requested.emit(13)
        elif chosen is new_9:
            self.new_game_with_size_requested.emit(9)
        elif chosen is open_act:
            self.open_sgf_requested.emit()
        elif chosen is paste_act:
            self.paste_requested.emit()

    def apply_theme(self):
        for card in (self._card_open, self._card_new):
            # カード自身がテーマ追従処理を持っていれば呼ぶ(_NewGameCard等)
            if hasattr(card, "apply_theme"):
                card.apply_theme()
            else:
                card.update()


class _WelcomeCard(QWidget):
    """
    WelcomePane 内の1枚のカード。
    _DropZoneWidget と同じ点線枠スタイルを採用。
    """
    clicked = pyqtSignal()

    # アイコン種別
    _ICON_OPEN = "open"
    _ICON_NEW  = "new"

    # ── レスポンシブ用のサイズ範囲(完全fluid) ──────────────────────────
    # ウィンドウ幅に応じて連続的に変化する。WelcomePane.resizeEvent から
    # set_card_size(w, h) で適用される。
    MAX_W, MAX_H = 300, 290   # 広いウィンドウ時(現状寸法)
    MIN_W, MIN_H = 260, 260   # 狭いウィンドウ時の下限

    # ── 「棋譜を開く」カードの共通生成パラメータ ───────────────────────
    # WelcomePane と _BoardContainer のドロップオーバーレイで同じ見た目に
    # するため、両者ともこの辞書を **kwargs で展開して生成する。
    # 文言を変更する際はここ1箇所を直すだけで両方に反映される。
    OPEN_CARD_PARAMS = dict(
        icon_type="open",
        title="棋譜を開く",
        sub="",
        hint="ファイルをドロップしても開けます",
    )

    def __init__(self, icon_type: str, title: str, sub: str, hint: str):
        super().__init__()
        self._icon_type = icon_type
        self._title = title
        self._sub   = sub
        self._hint  = hint
        self._hovered      = False
        self._drag_hovered = False
        # アクティブ時(ホバー or ドラッグホバー)に使用する色。
        # None なら T().ACCENT（既定の青）を使う。
        # _BoardContainer のドロップオーバーレイなど、暗い背景上で視認性を
        # 確保したい場面で set_active_color() で白などに上書きする。
        self._active_color: Optional[QColor] = None
        # アクティブ時の背景塗り(PANEL色)を抑制するフラグ。
        # ドロップオーバーレイなど、すでに半透明背景上に置かれていて
        # 塗りが見えにくい場面で True にする(枠と文字だけで表現)。
        self._disable_hover_panel: bool = False
        # 配色をテーマに依らずダークモード基準で固定するフラグ。
        # D&D オーバーレイ(暗背景固定)の上に置く _drop_card など、
        # ライトモード時にも文字/アイコンを白系で描画したい場面で True にする。
        # True 時は paintEvent 内の色取得を T() ではなくハードコード
        # (#ffffff / #b0b0b0 など)に切り替える。
        self._force_dark_palette: bool = False

        self.setFixedSize(self.MAX_W, self.MAX_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def set_disable_hover_panel(self, disabled: bool):
        """ホバー/ドラッグホバー時の背景塗りを抑制する設定。
        オーバーレイ上に置く場合など、塗りを出したくない時に使う。"""
        if self._disable_hover_panel == disabled:
            return
        self._disable_hover_panel = disabled
        self.update()

    def set_force_dark_palette(self, force: bool):
        """配色をテーマに依らずダークモード基準で固定する設定。
        D&D オーバーレイの暗背景上では、ライトモード時でも白系の文字・
        アイコンで描画したいので、_drop_card 生成時に True にする。
        """
        if self._force_dark_palette == force:
            return
        self._force_dark_palette = force
        self.update()

    def set_card_size(self, w: int, h: int):
        """カードサイズを fluid に設定する。
        WelcomePane のレスポンシブ機構から呼ばれる。
        w, h は MIN〜MAX の範囲内に clamp 済みの値が渡される前提。"""
        if self.width() == w and self.height() == h:
            return
        self.setFixedSize(w, h)
        self.update()

    def set_active_color(self, color: Optional[QColor]):
        """アクティブ時の色（点線枠・アイコン・タイトル・サブテキスト共通）を上書きする。
        None を渡すと既定 (T().ACCENT) に戻る。"""
        self._active_color = color
        self.update()

    def set_drag_hovered(self, v: bool):
        if self._drag_hovered != v:
            self._drag_hovered = v
            self.update()

    def enterEvent(self, ev):
        self._hovered = True
        self.update()

    def leaveEvent(self, ev):
        self._hovered = False
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        active = self._hovered or self._drag_hovered

        # ── 配色解決 ────────────────────────────────────────────────────
        # 通常はテーマ T() から色を取るが、_force_dark_palette=True のときは
        # T() に依らずダーク基準色をハードコードで使う(D&D オーバーレイの
        # 暗背景上で、ライトモード時にも視認性を確保するため)。
        if self._force_dark_palette:
            _C_TEXT   = QColor("#ffffff")
            _C_TEXT2  = QColor("#b0b0b0")
            _C_BORDER = QColor("#b0b0b0")  # 通常時の点線枠(active=False 用)
            _C_PANEL  = None               # 暗背景上では塗りを出さない
        else:
            _C_TEXT   = T().TEXT
            _C_TEXT2  = T().TEXT2
            _C_BORDER = T().BORDER
            _C_PANEL  = T().PANEL

        # ── ホバー時の表現方針 ──────────────────────────────────────────
        # 案2: 背景塗り + 枠色を明るく(BORDER → TEXT)
        # アイコン/タイトル/サブ/ヒントの色はホバーしても変化しない(静的)。
        # → 「枠は明るくなる/中身は暗くなる」という方向の不一致を解消し、
        #    『面でアクティブ感を表現する』モダンなホバー応答に。
        #
        # ただし _active_color が外部から指定されている場合(D&Dオーバーレイの
        # 暗い背景上で視認性確保が必要なケースなど)は、従来どおり全要素を
        # _active_color に染める挙動を維持する。
        if self._active_color is not None and active:
            # 旧挙動: 全要素を _active_color で染める
            ac = self._active_color
            border_c = ac
            icon_c   = ac
            text_c   = ac
            title_c  = ac
            hint_c   = ac
            panel_c  = None  # 背景塗りはしない
        else:
            # 新挙動(案2): 背景塗り + 枠色変化のみ
            border_c = _C_TEXT2 if active else _C_BORDER
            icon_c   = _C_TEXT2  # 静的
            text_c   = _C_TEXT2  # 静的
            title_c  = _C_TEXT   # 静的
            hint_c   = _C_TEXT2  # 静的
            # _disable_hover_panel=True の場合は塗りを出さない(枠だけで反応)
            if active and not self._disable_hover_panel and _C_PANEL is not None:
                panel_c = _C_PANEL
            else:
                panel_c = None

        # ── 背景塗り(ホバー時のみ) ─────────────────────────────────────
        # 点線枠の内側にうっすらパネル色で塗る。点線が背景塗りの上に
        # 載るように、塗り → 枠 の順で描画する。
        if panel_c is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(panel_c))
            p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 10, 10)

        # ── 点線枠 ─────────────────────────────────────────────────────
        pen = QPen(border_c, 2, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 10, 10)

        # ── アイコン ───────────────────────────────────────────────────
        icon_pen = QPen(icon_c, 2, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(icon_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx = W / 2
        # アイコン位置はカード上半分やや下、サイズはカードを大きくしたぶん
        # スケールアップしてバランスを取る。タイトル位置は icon_top + icon_size + 24
        # で計算されるため、これらを変えればタイトルも自動的に追従する。
        # 共通bbox: (cx-28, icon_top, 56, 58)
        # 両アイコンとも実描画寸法は異なるが、この共通bbox内に中央配置することで
        # 「アイコンを包括する領域のサイズ」を揃え、カード間で位置感を統一する。
        # ・フォルダ(56x48): bbox内に縦中央配置 → 上下に5pxずつ余白
        # ・ファイル(本体36x52 + バッジ右上突出): 横46x縦58 → 左に5px寄せ
        #
        # 完全fluid対応: カード高さ H に応じて icon_top を線形補間する。
        # ・H = MAX_H(290): icon_top = 70
        # ・H = MIN_H(260): icon_top = 55
        # ヒント位置 (H-53) とサイズボタン (H-70) は下端基準なので自動追従。
        # icon_top だけ追従させれば全体バランスが保たれる。
        icon_top  = 55.0 + (H - self.MIN_H) * (70.0 - 55.0) / (self.MAX_H - self.MIN_H)
        icon_size = 58.0  # アイコン領域高さ = bbox 高さ(固定)

        if self._icon_type == self._ICON_OPEN:
            # ── 案B: 角丸フォルダ(前ブタの段差つき) ─────────────────────
            # 実描画寸法 56x48 を bbox(56x58) の縦中央に配置(top=+5, bottom=+53)
            p.setPen(icon_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # 後ろの本体(角丸): 56x38、上端 icon_top+15、下端 icon_top+53
            body = QPainterPath()
            body.addRoundedRect(QRectF(cx - 28, icon_top + 15, 56, 38), 6, 6)
            p.drawPath(body)
            # 前ブタ(タブ含む輪郭): 上端 icon_top+5
            # 左端から立ち上がり → 角丸 → 上辺 → 角丸 → 段差で本体上端へ
            tab = QPainterPath()
            tab.moveTo(cx - 28, icon_top + 17)
            tab.lineTo(cx - 28, icon_top + 9)
            tab.quadTo(cx - 28, icon_top + 5, cx - 24, icon_top + 5)
            tab.lineTo(cx - 6, icon_top + 5)
            tab.quadTo(cx - 3, icon_top + 5, cx - 2, icon_top + 8)
            tab.lineTo(cx, icon_top + 15)
            p.drawPath(tab)

        else:  # _ICON_NEW
            # ── 案A: 角丸ファイル＋内線＋右上バッジ ─────────────────────
            # 実描画寸法 46x58(本体36x52 + バッジr10、本体右上にバッジ中心)
            # bbox(56x58)の左寄せで配置: 本体左端 cx-23、バッジ中心 cx+13
            #
            # 視覚的中心の補正: 右上のバッジは色塗りで視覚的に重みがあるため、
            # 数学的中心で配置すると全体が左寄りに見える。
            # 本体・内線・バッジすべてを icon_x_offset ぶん右にシフトして
            # 視覚的中心がカード中央(cx)に来るように補正する。
            icon_x_offset = 6.0
            p.setPen(icon_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # 角丸ファイル本体: 36x52、上端 icon_top+6、下端 icon_top+58
            body = QPainterPath()
            body.addRoundedRect(QRectF(cx - 23 + icon_x_offset, icon_top + 6, 36, 52), 6, 6)
            p.drawPath(body)
            # 内線(書類のイメージ): 他のパーツ(枠・+バッジ)と同じ色で統一
            inner_pen = QPen(icon_c, 2, Qt.PenStyle.SolidLine,
                             Qt.PenCapStyle.RoundCap)
            p.setPen(inner_pen)
            p.drawLine(QPointF(cx - 13 + icon_x_offset, icon_top + 34),
                       QPointF(cx + 5  + icon_x_offset, icon_top + 34))
            p.drawLine(QPointF(cx - 13 + icon_x_offset, icon_top + 42),
                       QPointF(cx - 1  + icon_x_offset, icon_top + 42))
            p.setPen(icon_pen)
            # ＋バッジ（右上）: 中心 (cx+13, icon_top+10)、半径 10
            # 案Aのまま「バッジ中心が本体右上の角に重なる」位置関係を保持
            bx = cx + 13 + icon_x_offset
            by = icon_top + 10
            br = 10.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(icon_c))
            p.drawEllipse(QPointF(bx, by), br, br)
            plus_color = T().BG
            plus_pen = QPen(plus_color, 2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap)
            p.setPen(plus_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            pl = 5
            p.drawLine(QPointF(bx, by - pl), QPointF(bx, by + pl))
            p.drawLine(QPointF(bx - pl, by), QPointF(bx + pl, by))
            p.setPen(icon_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # ── タイトル ──────────────────────────────────────────────────
        # 健全な余白指定: gap_icon_to_title はコード値=見た目のpx数。
        # bbox下端からタイトルの「文字の見た目の上端」までの実距離を表す。
        # drawText の y は baseline 指定なので、ascent ぶん足してから渡す。
        p.setFont(Font_LG(True))
        fm = p.fontMetrics()
        gap_icon_to_title = 20  # bbox下端 → タイトル上端 の実距離
        title_top = icon_top + icon_size + gap_icon_to_title
        title_y   = title_top + fm.ascent()  # baselineに変換
        p.setPen(QPen(title_c))
        tw = fm.horizontalAdvance(self._title)
        p.drawText(QPointF((W - tw) / 2, title_y), self._title)

        # ── サブテキスト ──────────────────────────────────────────────
        # タイトル下端 → サブテキスト上端 の実距離を直接指定する。
        # タイトル下端 = title_y(baseline) + title_descent
        if self._sub:
            title_bottom = title_y + fm.descent()
            p.setFont(Font_XS())
            fm2 = p.fontMetrics()
            gap_title_to_sub = 6   # タイトル下端 → サブ上端 の実距離
            sub_top = title_bottom + gap_title_to_sub
            sub_y   = sub_top + fm2.ascent()
            p.setPen(QPen(text_c))
            sw = fm2.horizontalAdvance(self._sub)
            p.drawText(QPointF((W - sw) / 2, sub_y), self._sub)

        # ── ヒント（_NewGameCard のサイズボタンと同じ縦位置に揃える）─────
        # ボタン: 高さ 34、上端 y = H - 34 - 36 = H - 70、垂直中心 = H - 53
        # ヒントもこの中心 (H - 53) に「文字の視覚中心」を合わせる。
        # 視覚中心 → baseline までの距離 = (ascent - descent) / 2
        if self._hint:
            p.setFont(Font_SM())
            fm3 = p.fontMetrics()
            hint_center_y = H - 53   # カード下端 → ヒント視覚中心 の実距離
            hint_y = hint_center_y + (fm3.ascent() - fm3.descent()) / 2
            p.setPen(QPen(hint_c))
            hw = fm3.horizontalAdvance(self._hint)
            p.drawText(QPointF((W - hw) / 2, hint_y), self._hint)

        p.end()


class _NewGameCard(_WelcomeCard):
    """新規作成カード。基本デザインは _WelcomeCard と同じだが、
    カード下部に盤面サイズ選択ボタン（9路/13路/19路）を持つ。
    サイズボタンは「選択」のみを行い、画面遷移はしない。
    実際の新規作成はカード本体クリック（clicked シグナル）で行い、
    その時点で選択されているサイズが新規作成される。
    """
    # サイズボタンの選択肢（左→右）
    SIZES: list[int] = [9, 13, 19]
    DEFAULT_SIZE: int = 19

    def __init__(self, title: str, hint: str = ""):
        super().__init__(icon_type="new", title=title, sub="", hint=hint)
        # 現在選択中のサイズ（デフォルト19路）
        self._selected_size: int = self.DEFAULT_SIZE
        # サイズボタン3つを子 widget として絶対配置。
        # ボタンクリックは clicked シグナルに伝播しない（QPushButtonがイベント消費）。
        self._size_buttons: list[QPushButton] = []
        for sz in self.SIZES:
            btn = QPushButton(f"{sz}路", self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedSize(64, 34)
            btn.clicked.connect(lambda _checked, s=sz: self._on_size_button_clicked(s))
            self._size_buttons.append(btn)
        self._refresh_button_styles()
        self._layout_buttons()

    def selected_size(self) -> int:
        """現在選択されている盤面サイズを返す。"""
        return self._selected_size

    def apply_theme(self):
        """テーマ切替時に呼ばれる。サイズボタンの色をテーマに追従させる。"""
        # 親 _WelcomeCard 側でカード本体の再描画
        self.update()
        # サイズボタンのスタイルを現テーマで再計算
        self._refresh_button_styles()

    def _on_size_button_clicked(self, size: int):
        """サイズボタンクリック: 選択状態を切り替えるだけ。画面遷移はしない。"""
        if self._selected_size == size:
            return
        self._selected_size = size
        self._refresh_button_styles()

    def _refresh_button_styles(self):
        """ボタンのスタイルを現在の選択状態に応じて更新する。
        選択中のサイズは薄い背景色＋ボーダー強調、それ以外はアウトライン。"""
        for btn, sz in zip(self._size_buttons, self.SIZES):
            if sz == self._selected_size:
                # 選択中: 薄い背景色＋ボーダー強調
                btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background:{T().PANEL2.name()};"
                    f"  color:{T().TEXT.name()};"
                    f"  border:1px solid {T().TEXT2.name()};"
                    f"  border-radius:4px;"
                    f"  font-size:16px;"
                    f"  font-weight:500;"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  border-color:{T().TEXT.name()};"
                    f"}}"
                )
            else:
                # 未選択: アウトライン
                btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background:transparent;"
                    f"  color:{T().TEXT2.name()};"
                    f"  border:1px solid {T().BORDER.name()};"
                    f"  border-radius:4px;"
                    f"  font-size:16px;"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  color:{T().TEXT.name()};"
                    f"  border-color:{T().TEXT2.name()};"
                    f"}}"
                )

    def _layout_buttons(self):
        """サイズボタンをカード下部中央に横並びで配置する。"""
        gap = 6
        total_w = sum(b.width() for b in self._size_buttons) + gap * (len(self._size_buttons) - 1)
        start_x = (self.width() - total_w) // 2
        # ヒントテキスト(y = H - 16)の上に余裕を持って配置。
        # ボタンの高さに依存させて、サイズ変更時も追従させる。
        btn_h = self._size_buttons[0].height() if self._size_buttons else 26
        y = self.height() - btn_h - 36
        x = start_x
        for btn in self._size_buttons:
            btn.move(x, y)
            x += btn.width() + gap

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout_buttons()


class _AlwaysAcceptWheelTextEdit(QTextEdit):
    """通常の QTextEdit はスクロール限界(先頭/末尾)に達したホイールイベントを
    accept しないため、親ウィジェットに伝播してしまう。コメントオーバーレイで
    使うと、テキストの末尾でホイールを回したときに背後のグラフ/盤面の
    手送りが発火してしまう。本クラスは wheelEvent を必ず accept() し、
    親への伝播を遮断する。

    加えて、縦スクロールバーは非表示にする(代わりに上下端のフェードで
    スクロール余地を伝える設計だが、フェード描画は外部の
    _ScrollFadeOverlay が担当する)。
    viewport の上下に余白を確保することで、テキスト本文がフェード領域
    (上下 16px)に直接重ならないようにする。
    """
    # viewport の上下に確保するマージン (px)
    # FADE_HEIGHT (16) より小さくして、テキスト本文の頭/尾がフェードに少し
    # 食い込む(=フェード越しに薄く見え始める)挙動にする。
    _VIEWPORT_VPAD = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        # スクロールバーは非表示。スクロール自体(ホイール/キー操作)は機能する。
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # viewport の上下に余白を確保。これによりテキスト本文の最初/最後が
        # フェード領域(高さ 16px)の内側に直接来るのを防ぐ。
        # 左右は既存のレイアウト/documentMargin の余白を活かすので 0。
        self.setViewportMargins(0, self._VIEWPORT_VPAD, 0, self._VIEWPORT_VPAD)

        # 選択ハイライトをフォーカス状態に依存させない。
        # 右クリックメニュー表示中などフォーカスが外れた時、Qt 標準では
        # Inactive グループの Highlight 色が適用されて選択範囲のハイライトが
        # 消えたように見える。Inactive/Disabled の Highlight 色を Active と
        # 同じに揃えることで、コメント欄では常に同じ青ハイライトを維持する。
        from PyQt6.QtGui import QPalette
        pal = self.palette()
        active_hl   = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
        active_htxt = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText)
        for grp in (QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
            pal.setColor(grp, QPalette.ColorRole.Highlight,       active_hl)
            pal.setColor(grp, QPalette.ColorRole.HighlightedText, active_htxt)
        self.setPalette(pal)

    def wheelEvent(self, ev):
        super().wheelEvent(ev)  # 通常のスクロール処理
        ev.accept()             # 限界到達時も親に流さない

    def contextMenuEvent(self, ev):
        """コメント入力欄の右クリックメニュー。
        Qt 標準の QTextEdit メニューを廃し、他のポップアップメニューと
        スタイル(テーマ追従・フォント・余白)を統一したカスタムメニューを
        表示する。項目は「切り取り / コピー / 貼り付け / すべて選択」。
        選択ハイライトの保持は __init__ で palette を設定済み。
        """
        from PyQt6.QtWidgets import QMenu, QApplication
        from PyQt6.QtGui import QKeySequence

        menu = QMenu(self)
        style_qmenu(menu, leaf=True)

        cursor = self.textCursor()
        has_selection = cursor.hasSelection()

        clipboard = QApplication.clipboard()
        has_clip_text = bool(clipboard and clipboard.text())

        cut_act = menu.addAction("切り取り")
        cut_act.setShortcut(QKeySequence.StandardKey.Cut)
        cut_act.setEnabled(has_selection and not self.isReadOnly())
        cut_act.triggered.connect(self.cut)

        copy_act = menu.addAction("コピー")
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.setEnabled(has_selection)
        copy_act.triggered.connect(self.copy)

        paste_act = menu.addAction("貼り付け")
        paste_act.setShortcut(QKeySequence.StandardKey.Paste)
        paste_act.setEnabled(has_clip_text and not self.isReadOnly())
        paste_act.triggered.connect(self.paste)

        menu.addSeparator()

        select_all_act = menu.addAction("すべて選択")
        select_all_act.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_act.triggered.connect(self.selectAll)

        menu.exec(ev.globalPos())


# ── コミ「その他」用インラインウィジェット ──────────────────────────────
class _KomiCustomWidget(QWidget):
    """コミメニューの「その他」項目用のインラインウィジェット。
    [−] [値ラベル] [+] の3要素を横並びにして、QWidgetAction.setDefaultWidget
    経由でメニュー項目内に埋め込む。

    挙動:
      - − / + ボタン:
        ・通常時(チェック未付与=プリセット選択中): ウィジェット内の数値を
          0.5 刻みで増減するだけ(値は確定しない、メニューも開いたまま)。
        ・「その他」チェック付与中: 数値変更と同時に valueChangedRealtime を
          発火し、メニューを開いたままコミをリアルタイム反映する。
      - 値ラベル「その他:」やラベル部分のクリック: 現在のウィジェット数値で
        コミを確定 → confirmRequested(value) シグナル発火 → メニュー閉じる
      - メニュー外クリックで閉じた時: ウィジェット内の数値は保持される
        (次にメニューを開いた時、最後に調整した値がそのまま残る)
      - 範囲: 0.0 〜 30.0、ステップ 0.5
      - チェックマーク表示は外部から set_checked() で制御
        (= 現在値が「その他」経由で確定された時に True)
    """
    # 値ラベルやラベル部分がクリックされた時のシグナル(現在のウィジェット値)
    confirmRequested = pyqtSignal(float)
    # 「その他」選択中に ± ボタンで数値が変わった時のシグナル(リアルタイム反映用)
    valueChangedRealtime = pyqtSignal(float)
    # ± ボタンで数値が変わった時のシグナル(チェック状態に関わらず常に発火)。
    # 「その他」ウィジェット値の永続化(QSettings 保存)用に MainWindow が受信する。
    valueAdjusted = pyqtSignal(float)

    STEP    = 0.5
    MIN_VAL = -99.5
    MAX_VAL = 99.5

    def __init__(self, value: float = 0.0, parent=None):
        super().__init__(parent)
        self._value = self._clamp(value)
        # チェックマーク表示状態(プリセット外現在値の時だけ True)
        self._checked: bool = False
        # ホバー状態(他のメニュー項目と同様に背景色を変えるため)
        self._hovered: bool = False

        # 半透明背景や hover 効果はメニュー項目側 QSS に任せ、
        # 自身は背景透過 + 内側の各要素だけスタイリングする。
        # ホバー時の背景色は paintEvent で自前描画(menu_qss の
        # QMenu::item:selected と同じ PANEL2 + 角丸 4px)。
        self.setStyleSheet("background:transparent;")
        # ボタン押下なしでも enterEvent/leaveEvent を受けるため
        self.setMouseTracking(True)

        hl = QHBoxLayout(self)
        # メニュー項目の他のチェック付きアクションと indicator 位置を揃える。
        # menu_qss では QMenu::indicator が width:12, margin-left:8 で
        # 描かれる(つまり項目左端から 8px の位置に 12px 幅の領域)。
        # 同等の見た目になるよう、左マージン 8 + チェック領域 12 + 内側余白
        # で左端を構成する。上下マージンは menu_qss の QMenu::item の
        # padding(8px)と揃えて、他のメニュー項目と同じ高さ感にする。
        hl.setContentsMargins(SP_SM, SP_SM, SP_MD, SP_SM)
        hl.setSpacing(SP_SM)

        # ── チェックマーク領域 ────────────────────────────────────
        # 12×12 の QLabel を indicator 位置に配置。表示/非表示で
        # チェック状態を表現する(他のチェック付きアクションと見た目統一)。
        self._check_lbl = QLabel("")
        self._check_lbl.setFixedSize(12, 12)
        self._check_lbl.setStyleSheet("background:transparent;border:none;")
        hl.addWidget(self._check_lbl)

        # ラベル「その他:」(全角コロン)
        # フォントは menu_qss の QMenu 設定(font-size:14px)と揃える。
        self._lbl_prefix = QLabel("その他：")
        self._lbl_prefix.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        self._lbl_prefix.setFont(Font_SM())
        hl.addWidget(self._lbl_prefix)
        hl.addStretch(1)

        # ── − ボタン ──────────────────────────────────────────
        # ── − ボタン(長押し連射対応) ─────────────────────────
        # setAutoRepeat: 長押し中に clicked シグナルを連続発火させる。
        # ・AutoRepeatDelay: 押下から最初の連射までの遅延 (ms)
        # ・AutoRepeatInterval: 連射の間隔 (ms)
        # 30 程度の大きな値変更でもストレスなく到達できる速度に設定。
        self._btn_dec = QPushButton("−")
        self._btn_dec.setFixedSize(20, 20)
        self._btn_dec.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_dec.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_dec.setStyleSheet(self._button_qss())
        self._btn_dec.setAutoRepeat(True)
        self._btn_dec.setAutoRepeatDelay(400)     # 押下後 400ms で連射開始
        self._btn_dec.setAutoRepeatInterval(60)   # 60ms 間隔(=毎秒約16ステップ)
        self._btn_dec.clicked.connect(self._on_dec)
        hl.addWidget(self._btn_dec)

        # ── 値ラベル ───────────────────────────────────────────
        # メニュー項目フォントと同じ 14px、値であることを示すため太字。
        self._val_lbl = QLabel(self._format(self._value))
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_lbl.setMinimumWidth(56)  # "-99.5" が収まる幅
        self._val_lbl.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        self._val_lbl.setFont(Font_SM(True))
        hl.addWidget(self._val_lbl)

        # ── + ボタン(長押し連射対応) ─────────────────────────
        self._btn_inc = QPushButton("+")
        self._btn_inc.setFixedSize(20, 20)
        self._btn_inc.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_inc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_inc.setStyleSheet(self._button_qss())
        self._btn_inc.setAutoRepeat(True)
        self._btn_inc.setAutoRepeatDelay(400)
        self._btn_inc.setAutoRepeatInterval(60)
        self._btn_inc.clicked.connect(self._on_inc)
        hl.addWidget(self._btn_inc)

        self._update_button_enabled()
        self._refresh_check_icon()

    @staticmethod
    def _format(v: float) -> str:
        """0.5 刻み前提で整形(常に小数1桁)。"""
        return f"{v:.1f}"

    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(cls.MIN_VAL, min(cls.MAX_VAL, float(v)))

    @staticmethod
    def _button_qss() -> str:
        """− / + ボタンの QSS。タイトルバーのアイコンボタンと同様、
        透明背景 + ホバーで PANEL2 を敷く控えめなスタイル。"""
        t = T()
        return (
            f"QPushButton{{"
            f"  background:transparent;"
            f"  color:{t.TEXT.name()};"
            f"  border:none;"
            f"  border-radius:4px;"
            f"  font-size:14px;"
            f"  font-weight:bold;"
            f"  padding:0;"
            f"}}"
            f"QPushButton:hover{{background:{t.PANEL2.name()};}}"
            f"QPushButton:disabled{{color:{t.TEXT2.name()};}}"
        )

    def _update_button_enabled(self):
        """境界に達したら −/+ ボタンを disable。"""
        self._btn_dec.setEnabled(self._value > self.MIN_VAL + 1e-6)
        self._btn_inc.setEnabled(self._value < self.MAX_VAL - 1e-6)

    def _on_dec(self):
        """− ボタン: ウィジェット内の数値を減らす。
        「その他」選択中(_checked=True)ならリアルタイム反映シグナルも発火する。
        値変更は常に valueAdjusted を発火し、永続化(MainWindow 側で QSettings 保存)。"""
        new_v = self._clamp(round((self._value - self.STEP) * 2) / 2.0)
        if abs(new_v - self._value) < 1e-6:
            return
        self._value = new_v
        self._val_lbl.setText(self._format(new_v))
        self._update_button_enabled()
        self.valueAdjusted.emit(new_v)
        if self._checked:
            self.valueChangedRealtime.emit(new_v)

    def _on_inc(self):
        """+ ボタン: ウィジェット内の数値を増やす。
        「その他」選択中(_checked=True)ならリアルタイム反映シグナルも発火する。
        値変更は常に valueAdjusted を発火し、永続化(MainWindow 側で QSettings 保存)。"""
        new_v = self._clamp(round((self._value + self.STEP) * 2) / 2.0)
        if abs(new_v - self._value) < 1e-6:
            return
        self._value = new_v
        self._val_lbl.setText(self._format(new_v))
        self._update_button_enabled()
        self.valueAdjusted.emit(new_v)
        if self._checked:
            self.valueChangedRealtime.emit(new_v)

    def value(self) -> float:
        return self._value

    def set_value(self, v: float):
        """外部からの値同期(プリセット選択時など)。シグナルは発火しない。"""
        new_v = self._clamp(round(float(v) * 2) / 2.0)
        self._value = new_v
        self._val_lbl.setText(self._format(new_v))
        self._update_button_enabled()

    def set_checked(self, checked: bool):
        """チェックマーク表示の ON/OFF を切り替える。
        プリセット外の値が現在値の時に True を渡す想定。"""
        self._checked = bool(checked)
        self._refresh_check_icon()

    def _refresh_check_icon(self):
        """チェック状態に応じてチェック画像を再描画。
        他のメニュー項目と同じ SVG (TEXT 色) を使用する。"""
        if self._checked:
            from PyQt6.QtGui import QPixmap
            path = _get_check_mark_path(T().TEXT.name())
            pix = QPixmap(path)
            if not pix.isNull():
                # SVG は 12x12 で生成されているのでそのまま使う
                self._check_lbl.setPixmap(pix)
                return
        # 未チェック or 画像読み込み失敗 → 何も表示しない
        self._check_lbl.clear()

    def apply_theme(self):
        """テーマ切替時にスタイルを再適用する。"""
        self.setStyleSheet("background:transparent;")
        self._btn_dec.setStyleSheet(self._button_qss())
        self._btn_inc.setStyleSheet(self._button_qss())
        self._val_lbl.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        self._lbl_prefix.setStyleSheet(f"color:{T().TEXT.name()}; background:transparent;")
        # チェック画像の色もテーマに追従
        self._refresh_check_icon()
        # ホバー背景色も変わる(次の paintEvent で反映)
        self.update()

    def enterEvent(self, ev):
        """ホバー開始: 他のメニュー項目と同様に背景色を変える。"""
        self._hovered = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        """ホバー終了: 背景色を元に戻す。"""
        self._hovered = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        """ホバー時の背景を自前描画する。
        menu_qss の QMenu::item:selected{background:PANEL2;border-radius:4px;}
        と同じ見た目になるよう、ホバー時のみ角丸 4px の PANEL2 矩形を敷く。
        子ウィジェット(ボタン・ラベル)は背景透過なので、親の背景描画が
        透けて見える。super().paintEvent より前に描く。"""
        if self._hovered:
            from PyQt6.QtGui import QPainter, QBrush
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(T().PANEL2))
            # menu_qss の QMenu::item:selected は border-radius:4px なので揃える。
            p.drawRoundedRect(self.rect(), 4, 4)
            p.end()
        super().paintEvent(ev)

    def mousePressEvent(self, ev):
        """ウィジェット領域のクリックで現在値を確定する。
        −/+ ボタン上のクリックはボタン側で消費されてここには届かないため、
        ボタン以外のクリック(ラベル・値表示・チェック領域・余白)で
        confirmRequested を emit する。"""
        try:
            if ev.button() == Qt.MouseButton.LeftButton:
                # ボタン領域内なら何もしない(ボタン側に処理を任せる)。
                # 通常はイベントがボタンに先に届くのでここには来ないが、
                # 念のためのガード。
                btn_dec_geom = self._btn_dec.geometry()
                btn_inc_geom = self._btn_inc.geometry()
                pos = ev.pos()
                if btn_dec_geom.contains(pos) or btn_inc_geom.contains(pos):
                    super().mousePressEvent(ev)
                    return
                # それ以外のクリック → 現在値で確定
                self.confirmRequested.emit(self._value)
                ev.accept()
                return
        except Exception:
            pass
        super().mousePressEvent(ev)


# ── Navigation bar (under board) ────────────────────────────────────────────
class NavBar(QWidget):
    NB_HEIGHT = 36
    NB_RADIUS = 0
    NB_MARGIN = 8   # 碁盤直下の余白
    NB_OVERLAP = 8  # 碁盤への食い込み量(碁盤下マージンに重ねる)
    NB_WIDTH_RATIO = 1.0  # 全幅

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.NB_HEIGHT)
        self.setObjectName("nav_card")
        self.setStyleSheet(
            f"QWidget#nav_card {{ background:transparent; border:none; }}"
        )
        # palette を BG に合わせて初期化(autoFillBackground=False のままでも、
        # Qt がリサイズ最適化等で palette を一瞬使うことがあり、デフォルトの
        # 黒が残ると明色テーマで黒い領域が一瞬見える原因となる)。
        from PyQt6.QtGui import QPalette as _QPalette
        _pal = _QPalette()
        _pal.setColor(_QPalette.ColorRole.Window, T().BG)
        _pal.setColor(_QPalette.ColorRole.Base, T().BG)
        self.setPalette(_pal)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SP_LG, SP_XS, SP_LG, SP_XS)
        layout.setSpacing(SP_SM)

        self.btn_prev  = self._nav("◀")
        self.btn_next = self._nav("▶")

        self._slider = FlatSlider()
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._slider.wheelEvent = lambda ev: ev.ignore()
        # クリックした位置に直接ジャンプする
        self._slider.setPageStep(1)
        self._slider.mousePressEvent = self._slider_mouse_press

        layout.addWidget(self.btn_prev)
        layout.addWidget(self._slider, stretch=1)
        layout.addWidget(self.btn_next)

        # コメントボタン（スライダー右端）
        self.btn_comment = QPushButton()
        self.btn_comment.setFixedSize(28, 28)
        self.btn_comment.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_comment_btn_style()
        layout.addWidget(self.btn_comment)

        # ボタン群の palette を BG に揃える(明色テーマで一瞬黒が見える対策)
        self._apply_bg_palette_to_children()

    def _apply_bg_palette_to_children(self):
        """NavBar 内の子ボタン(コメント/戻る/進む)に BG 色の palette を設定する。
        QPushButton はデフォルトで palette.Window が黒になるため、明色テーマで
        リサイズ時に一瞬黒が見える原因となる。autoFillBackground=False のままで
        palette だけを揃えることで、その露出を防ぐ。"""
        from PyQt6.QtGui import QPalette as _QPalette
        _pal = _QPalette()
        _pal.setColor(_QPalette.ColorRole.Window, T().BG)
        _pal.setColor(_QPalette.ColorRole.Base, T().BG)
        for attr in ("btn_prev", "btn_next", "btn_comment"):
            b = getattr(self, attr, None)
            if b is not None:
                b.setPalette(_pal)

    def _slider_mouse_press(self, ev):
        """クリック位置に直接ジャンプし、その後はドラッグ追従できるようにする。
        ハンドル上のクリックは通常のドラッグ動作に委譲。
        """
        if ev.button() == Qt.MouseButton.LeftButton:
            slider = self._slider
            handle_half = SLIDER_HANDLE // 2
            # 現在のハンドル位置を計算
            available = slider.width() - handle_half * 2
            ratio = ((slider.value() - slider.minimum()) /
                     max(1, slider.maximum() - slider.minimum()))
            handle_x = handle_half + ratio * available
            # ハンドル上のクリックは通常のドラッグに委譲
            if abs(ev.position().x() - handle_x) <= handle_half + 2:
                QSlider.mousePressEvent(slider, ev)
                return
            # ハンドル以外のクリック: クリック位置に直接ジャンプ
            x = ev.position().x() - handle_half
            ratio = max(0.0, min(1.0, x / available if available > 0 else 0))
            value = round(slider.minimum() + ratio * (slider.maximum() - slider.minimum()))
            slider.setValue(value)
            # ジャンプ後にドラッグ追従できるよう通常の press イベントも転送
            QSlider.mousePressEvent(slider, ev)
            ev.accept()
        else:
            QSlider.mousePressEvent(self._slider, ev)

    def paintEvent(self, ev):
        super().paintEvent(ev)

    def _apply_comment_btn_style(self):
        """コメントボタンのSVGアイコンを描画する。
        アイコン色は常に TEXT(メイン)で固定。ホバー時も色は変えず、
        背景のみ icon_button_qss で変化する仕様。"""
        from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon
        from PyQt6.QtCore import QSize
        from PyQt6.QtSvg import QSvgRenderer

        def _build_icon(color_hex: str) -> QIcon:
            # コメントアイコン(円形吹き出し全塗り + ドット 3 つ切り抜き):
            # ・viewBox=20×20、stroke-width=1
            # ・吹き出しは「正円(中心 10, 9.5、半径 7)+ 左下しっぽ三角形」
            # ・しっぽは円周接続点 140° (4.638, 14.000) と 90° (10.000, 16.500) を
            #   結ぶ三角形、先端 (2.5, 16.5)。
            # ・しっぽの「下の縁」が水平(先端 y = 接続点 2 の y = 16.5)で、
            #   先端が円の最下点と同じ高さで真左に伸びる(添付画像の形状)。
            # ・正円を保証するため arc 始点・終点が両方とも中心から正確に半径 7
            #   の円周上の点。
            # ・arc は sweep-flag=1(時計回り)で接続点1 → 円の上 → 接続点2 を結ぶ。
            # ・本体を fill=color_hex で全塗りし、内部のドット 3 つは
            #   CompositionMode_DestinationOut で「穴」を開けて透過。
            # ・ドットは中心 y=9.5、x=6.5/10/13.5、r=0.9。
            from PyQt6.QtGui import QPen, QBrush
            from PyQt6.QtCore import QPointF, Qt as _Qt

            # ステージ 1: 吹き出し本体(全塗り)を SVG で描画
            svg_data = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20">'
                f'<path d="M 4.638 14.000 '
                f'A 7 7 0 1 1 10.000 16.500 '
                f'L 2.5 16.5 L 4.638 14.000 Z" '
                f'stroke="{color_hex}" stroke-width="1" fill="{color_hex}" '
                f'stroke-linejoin="round"/>'
                f'</svg>'
            ).encode()
            renderer = QSvgRenderer(svg_data)
            # サイズ 22×22 で描画(setIconSize と同じ)。viewBox=20×20 を
            # 22×22 にスケールアップ → 1.1 倍。
            ICON_PX = 22
            pix = QPixmap(QSize(ICON_PX, ICON_PX))
            pix.fill(QColor(0, 0, 0, 0))
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(p)

            # ステージ 2: ドット 3 つを CompositionMode_DestinationOut で透過に
            # ドット座標は viewBox(20×20)系なので、pixmap(22×22)に合わせて
            # スケール変換する(p.scale で QPainter の座標系を変換)。
            scale = ICON_PX / 20.0
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            p.scale(scale, scale)
            p.setPen(_Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 255)))  # α だけ使われる
            for cx_dot in (6.5, 10.0, 13.5):
                p.drawEllipse(QPointF(cx_dot, 9.5), 0.9, 0.9)
            p.end()
            return QIcon(pix)

        # 通常時もホバー時も同じ ICON_DIM 色にして、ホバーで色が変わらないようにする
        ic = _build_icon(T().ICON_DIM.name())
        # 既存の hover/normal フックを置き換える(両方同じアイコンなので
        # 視覚的には変化しないが、フックの構造は残す)
        install_icon_hover_color_swap(self.btn_comment, ic, ic)
        self.btn_comment.setIcon(ic)
        self.btn_comment.setIconSize(QSize(22, 22))
        # 透明背景 + ホバーで PANEL2 を敷く統一スタイル(アイコン色は QIcon
        # で個別管理するため、QSS の color は使わない = hover_color_swap=False)。
        self.btn_comment.setStyleSheet(icon_button_qss(hover_color_swap=False))

    def _nav(self, icon):
        """ナビゲーション用ボタン (戻る/進む)。
        テキスト '◀'/'▶' の代わりに角丸三角の SVG アイコンを表示する。
        ホバー時にアイコン色を切り替えるため install_icon_hover_color_swap を
        使う。テーマ切替時は _apply_slider_style 内で再生成される。
        """
        from PyQt6.QtCore import QSize
        btn = QPushButton()
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIconSize(QSize(16, 16))
        # 透明背景 + ホバーで PANEL2(共通スタイル)。アイコン色は QIcon で
        # 個別管理するため、QSS の color は使わない。
        btn.setStyleSheet(icon_button_qss(hover_color_swap=False))
        # アイコン定義 (icon引数で ◀ / ▶ を識別)
        svg = _SVG_NAV_PREV if icon == "◀" else _SVG_NAV_NEXT
        # アイコン色は常に ICON_DIM(ライトモードで TEXT より少し薄め)で固定。
        # ホバー時も色は変えず、背景のみ icon_button_qss で変化する。
        btn._nav_svg = svg
        ic = make_icon(svg, size=16, color=T().ICON_DIM.name())
        install_icon_hover_color_swap(btn, ic, ic)
        return btn

    @property
    def slider(self): return self._slider

    def wheelEvent(self, ev):
        """ホイール上回転→前の手、下回転→次の手。"""
        delta = ev.angleDelta().y()
        if delta > 0:
            self._slider.setValue(self._slider.value() - 1)
        elif delta < 0:
            self._slider.setValue(self._slider.value() + 1)
        ev.accept()

    def _apply_slider_style(self):
        """テーマ切り替え時にスライダー・ボタン類を再スタイル。"""
        self.setStyleSheet(
            f"QWidget#nav_card {{ background:transparent; border:none; }}"
        )
        self._slider.update()
        # 戻る/進むボタン: 背景CSS と SVGアイコンの両方を再生成
        # アイコン色は常に ICON_DIM(ライトモードで TEXT より少し薄め)で固定。
        btn_ss = icon_button_qss(hover_color_swap=False)
        for btn in (self.btn_prev, self.btn_next):
            btn.setStyleSheet(btn_ss)
            # アイコンをテーマに合わせて作り直す
            svg = getattr(btn, "_nav_svg", None)
            if svg is not None:
                ic = make_icon(svg, size=16, color=T().ICON_DIM.name())
                btn._normal_icon = ic
                btn._hover_icon = ic
                btn.setIcon(ic)
        if hasattr(self, "btn_comment"):
            self._apply_comment_btn_style()
        # ボタン群の palette を新テーマの BG 色に更新する
        # (デフォルトの黒 #000000 が残ると明色テーマで一瞬黒が見える)
        self._apply_bg_palette_to_children()



# ── Analysis worker ─────────────────────────────────────────────────────────




# ─────────────────────────────────────────────────────────────────────
# 開発用: カラー調整ダイアログ
# 設定メニュー > カラー調整... から開く。EVAL_COLORS / LIGHT_BLUNDER_COLORS を
# 直接書き換え、Kizuki本体UIにリアルタイム反映する。
# 最終決定した値を「コードとしてコピー」して main_window.py に貼り付ける運用。
# ─────────────────────────────────────────────────────────────────────
class ColorAdjustmentDialog(QDialog):
    """悪手判定の色をリアルタイム調整するための開発用ダイアログ。"""

    # 編集対象の定義: (mode, category, role, label)
    # mode: "dark" or "light"
    # category: "best", "good", "inaccuracy", "mistake", "blunder", "None"
    # role: "main" or "text_dark_mode" (light は "main" のみ)
    CATEGORIES = ["best", "good", "inaccuracy", "mistake", "blunder", "None"]
    LIGHT_CATEGORIES = ["best", "good", "inaccuracy", "mistake", "blunder", "None"]
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("カラー調整 (開発用)")
        self.resize(820, 720)

        # アンドゥ/リドゥスタック: 各色変更時に snapshot を記録。
        # アンドゥで戻した状態をリドゥスタックに退避し、「進む」で復元できる。
        # 新規変更が発生したらリドゥスタックはクリア(分岐させない)。
        self._undo_stack: list = []
        self._redo_stack: list = []

        # UI 構築
        from PyQt6.QtWidgets import (
            QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
            QGroupBox, QFormLayout, QFrame, QScrollArea, QWidget,
            QColorDialog, QGridLayout, QSizePolicy
        )
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(12, 12, 12, 12)
        main_lay.setSpacing(8)

        # スクロール可能エリア
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        scroll_lay = QVBoxLayout(scroll_content)
        scroll_lay.setContentsMargins(0, 0, 0, 0)
        scroll_lay.setSpacing(12)

        # ── ダークモード セクション ──
        dark_group = QGroupBox("ダークモード (EVAL_COLORS)")
        dark_lay = QGridLayout(dark_group)
        dark_lay.setContentsMargins(8, 12, 8, 8)
        dark_lay.setSpacing(6)
        # ヘッダー行
        dark_lay.addWidget(QLabel("カテゴリ"), 0, 0)
        dark_lay.addWidget(QLabel("main (ピル背景)"), 0, 1, 1, 2)
        dark_lay.addWidget(QLabel("vs白"), 0, 3)
        dark_lay.addWidget(QLabel("text_dark_mode (数値)"), 0, 4, 1, 2)
        dark_lay.addWidget(QLabel("vs#252525"), 0, 6)
        # 各行
        self._dark_widgets: dict = {}  # (cat, role) → (line_edit, swatch_button, cr_label)
        for r, cat in enumerate(self.CATEGORIES, start=1):
            label_text = cat if cat != "None" else "不明"
            dark_lay.addWidget(QLabel(label_text), r, 0)
            # main
            sw_main, le_main, cr_main = self._make_color_row("dark", cat, "main")
            dark_lay.addWidget(sw_main, r, 1)
            dark_lay.addWidget(le_main, r, 2)
            dark_lay.addWidget(cr_main, r, 3)
            self._dark_widgets[(cat, "main")] = (le_main, sw_main, cr_main)
            # text_dark_mode
            sw_text, le_text, cr_text = self._make_color_row("dark", cat, "text_dark_mode")
            dark_lay.addWidget(sw_text, r, 4)
            dark_lay.addWidget(le_text, r, 5)
            dark_lay.addWidget(cr_text, r, 6)
            self._dark_widgets[(cat, "text_dark_mode")] = (le_text, sw_text, cr_text)
        scroll_lay.addWidget(dark_group)

        # ── ライトモード セクション ──
        light_group = QGroupBox("ライトモード (LIGHT_BLUNDER_COLORS)")
        light_lay = QGridLayout(light_group)
        light_lay.setContentsMargins(8, 12, 8, 8)
        light_lay.setSpacing(6)
        light_lay.addWidget(QLabel("カテゴリ"), 0, 0)
        light_lay.addWidget(QLabel("色 (ピル/数値共通)"), 0, 1, 1, 2)
        light_lay.addWidget(QLabel("vs白(#fff)"), 0, 3)
        self._light_widgets: dict = {}
        for r, cat in enumerate(self.LIGHT_CATEGORIES, start=1):
            label_text = cat if cat != "None" else "不明"
            light_lay.addWidget(QLabel(label_text), r, 0)
            sw, le, cr_lbl = self._make_color_row("light", cat, "main")
            light_lay.addWidget(sw, r, 1)
            light_lay.addWidget(le, r, 2)
            light_lay.addWidget(cr_lbl, r, 3)
            self._light_widgets[cat] = (le, sw, cr_lbl)
        scroll_lay.addWidget(light_group)

        scroll_lay.addStretch()
        scroll.setWidget(scroll_content)
        main_lay.addWidget(scroll, 1)

        # ── ボタン群 ──
        btn_lay = QHBoxLayout()
        self._btn_undo = QPushButton("← 戻る")
        self._btn_undo.clicked.connect(self._on_undo)
        self._btn_undo.setEnabled(False)
        btn_lay.addWidget(self._btn_undo)
        self._btn_redo = QPushButton("進む →")
        self._btn_redo.clicked.connect(self._on_redo)
        self._btn_redo.setEnabled(False)
        btn_lay.addWidget(self._btn_redo)
        btn_copy = QPushButton("コードをクリップボードにコピー")
        btn_copy.clicked.connect(self._on_copy)
        btn_lay.addWidget(btn_copy)
        btn_lay.addStretch()
        btn_close = QPushButton("閉じる")
        btn_close.clicked.connect(self.accept)
        btn_lay.addWidget(btn_close)
        main_lay.addLayout(btn_lay)

        # 初期値をUIに反映
        self._refresh_all_swatches()

    def _make_color_row(self, mode: str, cat: str, role: str):
        """1行分のUI(色見本ボタン + HEX入力欄 + コントラスト比ラベル)を生成。"""
        from PyQt6.QtWidgets import QPushButton, QLineEdit, QLabel
        # 色見本(クリックでQColorDialog)
        swatch = QPushButton()
        swatch.setFixedSize(40, 24)
        swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        swatch.clicked.connect(lambda: self._open_picker(mode, cat, role))
        # HEX入力欄
        le = QLineEdit()
        le.setMaxLength(7)
        le.setFixedWidth(80)
        le.editingFinished.connect(lambda: self._on_hex_changed(mode, cat, role, le.text()))
        # コントラスト比ラベル
        cr_label = QLabel("")
        cr_label.setMinimumWidth(80)
        cr_label.setStyleSheet("font-family:monospace; color:#888;")
        return swatch, le, cr_label

    @staticmethod
    def _contrast_ratio(hex1: str, hex2: str) -> float:
        """2色のWCAGコントラスト比を返す。"""
        def hex_to_rgb(h):
            h = h.lstrip('#')
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        def srgb_to_linear(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        def luminance(rgb):
            r, g, b = (srgb_to_linear(c) for c in rgb)
            return 0.2126*r + 0.7152*g + 0.0722*b
        l1 = luminance(hex_to_rgb(hex1))
        l2 = luminance(hex_to_rgb(hex2))
        return (max(l1,l2)+0.05) / (min(l1,l2)+0.05)

    def _format_cr(self, cr: float) -> tuple[str, str]:
        """コントラスト比を「4.58:1 ✓」形式の文字列とCSSカラーで返す。"""
        if cr >= 4.5:
            return f"{cr:.2f}:1 ✓ AA", "color:#22aa55;"
        elif cr >= 3.0:
            return f"{cr:.2f}:1 △ AA-大", "color:#cc8800;"
        else:
            return f"{cr:.2f}:1 ✗", "color:#cc3333;"

    def _get_color(self, mode: str, cat: str, role: str) -> str:
        """現在の dict から hex 値を取得。cat 'None' は dict キーの None に変換。"""
        key = None if cat == "None" else cat
        if mode == "dark":
            return EVAL_COLORS[key][role]
        else:
            return LIGHT_BLUNDER_COLORS[key]

    def _set_color(self, mode: str, cat: str, role: str, hex_str: str):
        """dict の値を更新。cat 'None' は dict キーの None に変換。"""
        key = None if cat == "None" else cat
        if mode == "dark":
            EVAL_COLORS[key][role] = hex_str
        else:
            LIGHT_BLUNDER_COLORS[key] = hex_str

    def _refresh_all_swatches(self):
        """全UI要素を最新の dict 値で更新。"""
        # ダーク: main は 白文字とのCR、text_dark_mode は PANEL #252525 とのCR
        for (cat, role), (le, sw, cr_lbl) in self._dark_widgets.items():
            hex_v = self._get_color("dark", cat, role)
            le.blockSignals(True)
            le.setText(hex_v)
            le.blockSignals(False)
            sw.setStyleSheet(f"background:{hex_v}; border:1px solid #888; border-radius:3px;")
            # コントラスト比計算
            if role == "main":
                cr = self._contrast_ratio(hex_v, "#ffffff")
            else:  # text_dark_mode
                cr = self._contrast_ratio(hex_v, "#252525")
            text, css = self._format_cr(cr)
            cr_lbl.setText(text)
            cr_lbl.setStyleSheet(f"font-family:monospace; {css}")
        # ライト: main は 白(#fff)とのCR(ピル白文字 と PANEL=#fff 兼用)
        for cat, (le, sw, cr_lbl) in self._light_widgets.items():
            hex_v = self._get_color("light", cat, "main")
            le.blockSignals(True)
            le.setText(hex_v)
            le.blockSignals(False)
            sw.setStyleSheet(f"background:{hex_v}; border:1px solid #888; border-radius:3px;")
            cr = self._contrast_ratio(hex_v, "#ffffff")
            text, css = self._format_cr(cr)
            cr_lbl.setText(text)
            cr_lbl.setStyleSheet(f"font-family:monospace; {css}")

    def _push_undo_snapshot(self):
        """現在の状態をアンドゥスタックに積む。新規変更時はリドゥスタックをクリア。"""
        snapshot = (
            {k: dict(v) for k, v in EVAL_COLORS.items()},
            dict(LIGHT_BLUNDER_COLORS),
        )
        self._undo_stack.append(snapshot)
        self._redo_stack.clear()  # 新規変更で分岐が生じるためリドゥ履歴は破棄
        self._update_history_buttons()

    def _take_snapshot(self):
        """現在の状態を snapshot タプルとして返す(リドゥ用)。"""
        return (
            {k: dict(v) for k, v in EVAL_COLORS.items()},
            dict(LIGHT_BLUNDER_COLORS),
        )

    def _restore_snapshot(self, snapshot):
        """snapshot を EVAL_COLORS / LIGHT_BLUNDER_COLORS に書き戻す。"""
        eval_snap, light_snap = snapshot
        EVAL_COLORS.clear()
        for k, v in eval_snap.items():
            EVAL_COLORS[k] = dict(v)
        LIGHT_BLUNDER_COLORS.clear()
        for k, v in light_snap.items():
            LIGHT_BLUNDER_COLORS[k] = v

    def _update_history_buttons(self):
        """戻る・進むボタンの有効/無効を更新。"""
        if hasattr(self, "_btn_undo"):
            self._btn_undo.setEnabled(len(self._undo_stack) > 0)
        if hasattr(self, "_btn_redo"):
            self._btn_redo.setEnabled(len(self._redo_stack) > 0)

    def _open_picker(self, mode: str, cat: str, role: str):
        """QColorDialog を開いて色選択。"""
        from PyQt6.QtWidgets import QColorDialog
        cur = self._get_color(mode, cat, role)
        col = QColorDialog.getColor(QColor(cur), self, "色を選択")
        if col.isValid():
            hex_v = col.name()  # "#rrggbb"
            if hex_v != cur:  # 実際に変わった場合のみ履歴に積む
                self._push_undo_snapshot()
                self._set_color(mode, cat, role, hex_v)
                self._apply_and_refresh()

    def _on_hex_changed(self, mode: str, cat: str, role: str, text: str):
        """HEX入力欄での変更ハンドラ。"""
        text = text.strip()
        if not text.startswith("#"):
            text = "#" + text
        col = QColor(text)
        if not col.isValid():
            # 無効なら元に戻す
            self._refresh_all_swatches()
            return
        hex_v = col.name()
        cur = self._get_color(mode, cat, role)
        if hex_v != cur:  # 実際に変わった場合のみ履歴に積む
            self._push_undo_snapshot()
            self._set_color(mode, cat, role, hex_v)
            self._apply_and_refresh()

    def _on_undo(self):
        """アンドゥスタックから1ステップ取り出して状態を復元。
        現状はリドゥスタックに退避する。"""
        if not self._undo_stack:
            return
        # 現状をリドゥに退避
        self._redo_stack.append(self._take_snapshot())
        # アンドゥから取り出して復元
        snap = self._undo_stack.pop()
        self._restore_snapshot(snap)
        self._update_history_buttons()
        self._apply_and_refresh()

    def _on_redo(self):
        """リドゥスタックから1ステップ取り出して状態を進める。
        現状はアンドゥスタックに退避する。"""
        if not self._redo_stack:
            return
        # 現状をアンドゥに退避(_push_undo_snapshot は redo をクリアするので使わず直接積む)
        self._undo_stack.append(self._take_snapshot())
        # リドゥから取り出して復元
        snap = self._redo_stack.pop()
        self._restore_snapshot(snap)
        self._update_history_buttons()
        self._apply_and_refresh()

    def _apply_and_refresh(self):
        """変更を Theme に反映し、Kizuki本体UIを再描画。"""
        self._refresh_all_swatches()
        # 親(MainWindow)経由でテーマ再適用
        win = self.parent()
        # MainWindow を見つけるまで遡る
        while win is not None and not hasattr(win, "_apply_theme_immediate"):
            win = win.parent()
        if win is not None:
            cur_mode = T().mode
            win._apply_theme_immediate(cur_mode)

    def _on_copy(self):
        """現在の値を Python コードとしてクリップボードにコピー。"""
        from PyQt6.QtWidgets import QApplication
        lines = []
        lines.append("# ─ EVAL_COLORS (ダークモード) ─")
        lines.append("EVAL_COLORS: dict = {")
        for cat in self.CATEGORIES:
            key = None if cat == "None" else cat
            v = EVAL_COLORS[key]
            cat_repr = "None" if cat == "None" else f'"{cat}"'
            lines.append(
                f'    {cat_repr + ":":<14}'
                f' {{"main": "{v["main"]}", "text": "{v["text"]}", '
                f'"text_dark_mode": "{v["text_dark_mode"]}"}},'
            )
        lines.append("}")
        lines.append("")
        lines.append("# ─ LIGHT_BLUNDER_COLORS (ライトモード) ─")
        lines.append("LIGHT_BLUNDER_COLORS: dict = {")
        for cat in self.LIGHT_CATEGORIES:
            key = None if cat == "None" else cat
            v = LIGHT_BLUNDER_COLORS[key]
            cat_repr = "None" if cat == "None" else f'"{cat}"'
            lines.append(f'    {cat_repr + ":":<14} "{v}",')
        lines.append("}")
        QApplication.clipboard().setText("\n".join(lines))


class _WarningIconWidget(QWidget):
    """「未保存の変更があります」を表す警告アイコン用ウィジェット。
    角丸の三角形(アウトライン)+ 中央に縦棒 + ドット で構成される
    「⚠」相当の意匠を、QPainter で直接描画する。
    emoji を使わない設計制約に沿うため、図形を直接描く方式を採る。

    意匠:
      - 背景なし(透過)。色はテーマの TEXT2 (グレー)。
      - 三角は線描き(アウトライン)、角は丸める。
      - 中央に縦棒(! の本体)+ 下にドット(! の点)。
    """
    def __init__(self, parent=None, size: int = 44):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, ev):
        from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath
        from PyQt6.QtCore import QPointF, QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = self.width()
        t = T()
        fg = QColor(t.TEXT2)  # アイコン本体色 (グレー)

        # 三角形は viewBox 64x64 の座標系で設計し、s に合わせてスケールする。
        # 角丸: 各頂点を r で丸めるため、ベジェ近似を使う。
        scale = s / 64.0
        p.save()
        p.scale(scale, scale)

        # 三角形の頂点:
        #   top   = (32,  6)
        #   right = (60, 54)
        #   left  = ( 4, 54)
        # 各頂点を半径 r で丸める。r は viewBox 64 単位での値。
        # r=4 ≈ ほんのり丸、r=8 ≈ しっかり丸、r=12 でかなり丸い。
        r = 8.0

        # 各辺の単位ベクトル
        # 左辺の方向: 左頂点(4,54) → 上頂点(32,6) は (28, -48)、長さ ≈ 55.57
        # 右辺の方向: 上頂点(32,6) → 右頂点(60,54) は (28, 48)、長さ ≈ 55.57
        # 底辺の方向: 右頂点(60,54) → 左頂点(4,54) は (-56, 0)、長さ = 56
        L_LEN = 55.57
        ulx, uly = 28.0 / L_LEN, -48.0 / L_LEN  # 左辺方向 (下→上)
        urx, ury = 28.0 / L_LEN, 48.0 / L_LEN   # 右辺方向 (上→下)
        # ubx, uby = -1.0, 0.0  # 底辺方向 (右→左) は使わずスカラで処理

        # 上頂点の前後 r 離れた点
        top_x, top_y = 32.0, 6.0
        top_in_x  = top_x - ulx * r
        top_in_y  = top_y - uly * r  # 上頂点の手前(左辺側)
        top_out_x = top_x + urx * r
        top_out_y = top_y + ury * r  # 上頂点の先(右辺側)

        # 右頂点の前後 r 離れた点
        right_x, right_y = 60.0, 54.0
        right_in_x  = right_x - urx * r
        right_in_y  = right_y - ury * r       # 右頂点の手前(右辺側、上から下)
        right_out_x = right_x - r
        right_out_y = right_y                  # 右頂点の先(底辺側、右から左)

        # 左頂点の前後 r 離れた点
        left_x, left_y = 4.0, 54.0
        left_in_x  = left_x + r
        left_in_y  = left_y                    # 左頂点の手前(底辺側、右から左)
        left_out_x = left_x + ulx * r
        left_out_y = left_y + uly * r          # 左頂点の先(左辺側、下から上)

        # cubicTo の制御点は「角を綺麗な円弧で丸める」ために、各頂点の手前/先から
        # 頂点方向に r * (1 - k) だけ進めた位置に置く。k = 0.5523 は四分円を
        # ベジェで近似する際の標準的なマジックナンバー。これにより r が大きい
        # ほど明確に円弧として見える。
        # 制御点は in 点 → 頂点方向に (1-k)*r、out 点 → 頂点方向に (1-k)*r。
        K = 0.5523
        cd = r * (1.0 - K)  # 制御点を in/out 点から頂点方向にずらす距離

        # 上頂点周りの制御点
        # in → 上頂点方向 (= ul の向き)
        top_c1x = top_in_x + ulx * cd
        top_c1y = top_in_y + uly * cd
        # out ← 上頂点方向 (= -ur の向き、つまり ur で戻る側)
        top_c2x = top_out_x - urx * cd
        top_c2y = top_out_y - ury * cd

        # 右頂点周りの制御点
        # in (右辺の途中、上→下) → 右頂点方向 (= ur の向き)
        right_c1x = right_in_x + urx * cd
        right_c1y = right_in_y + ury * cd
        # out (底辺の途中、右→左) ← 右頂点方向 (= +1, 0 = 戻る向き)
        right_c2x = right_out_x + cd
        right_c2y = right_out_y

        # 左頂点周りの制御点
        # in (底辺の途中、右→左) → 左頂点方向 (= -1, 0)
        left_c1x = left_in_x - cd
        left_c1y = left_in_y
        # out (左辺の途中、下→上) ← 左頂点方向 (= -ul の向き)
        left_c2x = left_out_x - ulx * cd
        left_c2y = left_out_y - uly * cd

        path = QPainterPath()
        path.moveTo(top_in_x, top_in_y)
        path.cubicTo(top_c1x, top_c1y, top_c2x, top_c2y, top_out_x, top_out_y)
        path.lineTo(right_in_x, right_in_y)
        path.cubicTo(right_c1x, right_c1y, right_c2x, right_c2y, right_out_x, right_out_y)
        path.lineTo(left_in_x, left_in_y)
        path.cubicTo(left_c1x, left_c1y, left_c2x, left_c2y, left_out_x, left_out_y)
        path.lineTo(top_in_x, top_in_y)
        path.closeSubpath()

        # ストローク (三角の輪郭線)
        # paint は scale 適用後の座標系で描かれるため、設計上の太さを
        # scale で割って指定すると、表示時には所定の太さで描画される。
        outline_w = max(1.4, s * 0.035)  # 表示時の実太さ (44px時 ≈ 1.54px)
        pen = QPen(fg, outline_w / scale)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # ! の縦棒(上が太く下に向かって細くなる楔形、下端も僅かに太さを残す)
        # 上端と下端の両方を「点に尖らせず」、わずかに太さを残してから丸める
        # ことで、自然なテーパー楔形になる。
        bar_path = QPainterPath()
        bar_top_y = 23.0       # 上端
        bar_shoulder_y = 25.5  # 肩(最大幅の位置)
        bar_bot_y = 37.0       # 下端
        cx = 32.0
        bar_half_w_top = 2.3   # 肩での半幅 (上の太さ)
        bar_half_w_bot = 0.65  # 下端の半幅 (細いが点ではない)
        # 上端の丸み付き始まり: 上端 → 肩へ
        bar_path.moveTo(cx, bar_top_y)
        # 左肩へ(上端から開く、丸み付き)
        bar_path.cubicTo(cx - bar_half_w_top * 0.6, bar_top_y + 0.7,
                         cx - bar_half_w_top, bar_shoulder_y - 0.7,
                         cx - bar_half_w_top, bar_shoulder_y)
        # 左肩から下端の手前へ直線(楔の左斜辺)
        bar_path.lineTo(cx - bar_half_w_bot, bar_bot_y)
        # 下端を丸める(半円的に)
        bar_path.quadTo(cx, bar_bot_y + 0.7, cx + bar_half_w_bot, bar_bot_y)
        # 下端の右側から右肩へ直線(楔の右斜辺)
        bar_path.lineTo(cx + bar_half_w_top, bar_shoulder_y)
        # 右肩から上端へ戻る(丸み付き)
        bar_path.cubicTo(cx + bar_half_w_top, bar_shoulder_y - 0.7,
                         cx + bar_half_w_top * 0.6, bar_top_y + 0.7,
                         cx, bar_top_y)
        bar_path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(fg))
        p.drawPath(bar_path)

        # ! のドット (下の点)
        dot_r = 2.3
        p.drawEllipse(QRectF(32 - dot_r, 44 - dot_r, dot_r * 2, dot_r * 2))

        p.restore()
        p.end()


class _UnsavedChangesDialog(QDialog):
    """棋譜に未保存の変更がある時に表示する3択確認ダイアログ。

    ボタン構成 (Windows 標準順、主アクション左):
      [保存] [保存しない] [キャンセル]
      Enter キーで「保存」(主アクション、Default)
      Esc キーで「キャンセル」

    左側に⚠アイコン(琥珀色)を配置し、右側に2段メッセージ
    (タイトル太字 + 本文薄め) を表示する。

    exec() の戻り値:
      RESULT_SAVE (0)    = 保存して続行
      RESULT_DISCARD (1) = 保存せず続行(変更を破棄)
      RESULT_CANCEL (2)  = 操作を取り消す
    """
    RESULT_SAVE = 0
    RESULT_DISCARD = 1
    RESULT_CANCEL = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget
        self.setWindowTitle("Kizuki")
        # フレームレス + 半透明背景でアプリ全体のスタイルに馴染ませる。
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._result_code: int = self.RESULT_CANCEL

        t = T()

        # 外側コンテナ(角丸 + テーマ追従背景)
        self._container = QWidget(self)
        self._container.setStyleSheet(
            f"background:{t.PANEL.name()};"
            f"border:1px solid {t.BORDER2.name()};"
            f"border-radius:12px;"
        )

        # ドロップシャドウ: Windows 11 / Fluent Design の dialog elevation に
        # 倣う設定。広めのぼかし + 控えめな下オフセット + 低めの不透明度で、
        # ふわっと浮く印象に。アプリのメインウィンドウは OS 標準シャドウ
        # (DWM) を使っているため、これに視覚的に揃う値を選ぶ。
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 90))
        self._container.setGraphicsEffect(shadow)

        # outer はシャドウが収まる余白を確保する。
        # blurRadius=32, offset=(0,4) なら、左右 ≈ 24px, 上 ≈ 20px,
        # 下 ≈ 28px ほど取れば影が切れない。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 28)
        outer.addWidget(self._container)

        # メイン縦レイアウト: 上段(アイコン+メッセージ) と 下段(ボタン行)
        # 上 24, 左右 24, 下 20。中央間隔(テキスト〜ボタン)は 24。
        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(24, 24, 24, 20)
        inner.setSpacing(24)

        # ── 上段: アイコン + メッセージ(2段) ─────────────────────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(16)
        top_row.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 警告アイコン(角丸三角、未保存の変更があることを示す)
        self._icon = _WarningIconWidget(self._container, size=44)
        top_row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        # メッセージ(タイトル + 本文の2段)
        msg_col = QVBoxLayout()
        msg_col.setContentsMargins(0, 2, 0, 0)  # アイコンと垂直中心が揃うよう微調整
        msg_col.setSpacing(6)
        title_lbl = QLabel("編集したコメントが保存されていません")
        title_lbl.setStyleSheet(
            f"color:{t.TEXT.name()};font-size:14px;font-weight:500;"
            f"background:transparent;border:none;"
        )
        title_lbl.setWordWrap(True)
        msg_col.addWidget(title_lbl)
        body_lbl = QLabel("変更内容を保存しますか?")
        body_lbl.setStyleSheet(
            f"color:{t.TEXT2.name() if hasattr(t, 'TEXT2') else t.TEXT.name()};"
            f"font-size:13px;background:transparent;border:none;"
        )
        body_lbl.setWordWrap(True)
        msg_col.addWidget(body_lbl)
        top_row.addLayout(msg_col, 1)

        inner.addLayout(top_row)

        # ── 下段: ボタン行(右寄せ、Windows 標準: 保存 / 保存しない / キャンセル) ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        # ボタンスタイル: 主アクション(保存)も副ボタン(保存しない/キャンセル)も
        # 同じスタイルに統一する。区別は順番(主アクションを左)とデフォルト
        # フォーカス(Enter で保存)で行う。
        btn_qss = (
            f"QPushButton{{background:transparent;color:{t.TEXT.name()};"
            f"border:1px solid {t.BORDER.name()};border-radius:6px;"
            f"padding:8px 20px;font-size:13px;min-width:88px;}}"
            f"QPushButton:hover{{background:{t.PANEL2.name()};"
            f"border:1px solid {t.ACCENT.name()};}}"
            f"QPushButton:pressed{{background:{t.BORDER2.name()};}}"
        )

        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(btn_qss)
        save_btn.setDefault(True)
        save_btn.setAutoDefault(True)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        discard_btn = QPushButton("保存しない")
        discard_btn.setStyleSheet(btn_qss)
        discard_btn.setAutoDefault(False)
        discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        discard_btn.clicked.connect(self._on_discard)
        btn_row.addWidget(discard_btn)

        cancel_btn = QPushButton("キャンセル")
        cancel_btn.setStyleSheet(btn_qss)
        cancel_btn.setAutoDefault(False)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)

        inner.addLayout(btn_row)

        # 右上の閉じるボタン (× アイコン)。コメント欄の閉じるボタンと
        # 同じ意匠 (Unicode 文字、20x20、透明 → PANEL2 ホバー、TEXT2 文字色)。
        # レイアウトには載せず、_container 上に絶対配置して resizeEvent で
        # 追従させる。クリック時はキャンセル相当。
        self._close_x_btn = QPushButton("✕", self._container)
        self._close_x_btn.setFixedSize(24, 24)
        self._close_x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 透明背景 + ホバーで PANEL2 + 文字色 TEXT2→TEXT(共通スタイル)
        self._close_x_btn.setStyleSheet(icon_button_qss(
            font_size=12, padding="0px 0px 2px 0px"))
        self._close_x_btn.clicked.connect(self._on_cancel)
        # 初期配置は adjustSize 後に reposition_close_x で行う
        # (adjustSize で _container の幅が決まる前は意味がないため)

        # サイズ: 幅は最小幅を確保、高さは内容に合わせて adjustSize に任せる
        # (170 等の最小高さを指定すると差分が空白として top_row とボタンの間に
        #  流れ込み、テキスト〜ボタン間が見かけ上膨らんでしまう)
        self.setMinimumWidth(440)
        self.adjustSize()
        self._reposition_close_x()

        # 表示・非表示アニメ用のフラグ。
        # 開始は showEvent で windowOpacity 0 → 1 + 95% → 100% スケール、
        # accept/reject 時は逆方向に再生してから super を呼ぶ。
        self._closing_anim_running = False
        self._show_anim = None
        self._show_anim_group = None
        self._close_anim = None
        self._close_anim_group = None
        # 初期不透明度 0 (showEvent で 1 にアニメ)
        self.setWindowOpacity(0.0)

    # ── 表示・非表示アニメ ────────────────────────────────────────
    SHOW_ANIM_MS = 180
    CLOSE_ANIM_MS = 140

    def showEvent(self, ev):
        super().showEvent(ev)
        # 多重起動防止
        if getattr(self, "_show_anim_group", None) is not None:
            return
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(self.SHOW_ANIM_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_anim = fade
        self._show_anim_group = fade   # 互換のため同じ参照を保持(再アニメ抑止用)
        fade.start()

    def _start_close_animation(self, then_call):
        """フェードアウトしてから then_call() を実行する。
        accept() / reject() の代替として使う。"""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        if self._closing_anim_running:
            return
        self._closing_anim_running = True

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(self.CLOSE_ANIM_MS)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(then_call)
        self._close_anim = fade
        self._close_anim_group = fade
        fade.start()

    def _reposition_close_x(self):
        """× ボタンを _container の右上 (上 4, 右 4) に配置する。"""
        if not hasattr(self, "_close_x_btn"):
            return
        cw = self._container.width()
        margin = 4
        bw = self._close_x_btn.width()
        self._close_x_btn.move(cw - bw - margin, margin)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._reposition_close_x()

    def _on_save(self):
        self._result_code = self.RESULT_SAVE
        self._start_close_animation(super().accept)

    def _on_discard(self):
        self._result_code = self.RESULT_DISCARD
        self._start_close_animation(super().accept)

    def _on_cancel(self):
        self._result_code = self.RESULT_CANCEL
        self._start_close_animation(super().reject)

    def keyPressEvent(self, ev):
        # Esc で「キャンセル」相当
        if ev.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(ev)

    def result_code(self) -> int:
        return self._result_code


class _FirstLaunchRankDialog(QDialog):
    """初回起動時に表示する「棋力選択」モーダルダイアログ。

    Kizuki は棋力に応じて悪手/疑問手判定の閾値を変えるため、ユーザの
    棋力を起動時に1度だけ確認する必要がある(以降は メニュー > 設定 >
    あなたの棋力 で変更可能)。

    UI:
      ・「Kizuki へようこそ」タイトル + 説明文
      ・棋力ドロップダウン (初期値「選択してください」、選ばないと OK 無効)
      ・OK ボタンのみ(キャンセル不可: × も ESC も無効)

    スタイル: _UnsavedChangesDialog と同じ frameless + 角丸 + シャドウ。

    使い方:
      ・MainWindow が show() された直後に exec() で開く。
      ・確定すると selected_rank() で選択値(int)が取れる。
      ・呼び出し側で QSettings の "player_rank" に保存する責任を持つ。
    """

    def __init__(self, rank_options: list[tuple[str, int]], parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import (
            QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget,
            QComboBox, QGraphicsDropShadowEffect,
        )
        self.setWindowTitle("Kizuki")
        # フレームレス + 半透明背景でアプリ全体のスタイルに馴染ませる。
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # モーダル: 親ウィンドウへの操作をブロック
        self.setModal(True)

        # 選択された棋力値 (初期値 None = 未選択)
        self._selected_rank: int | None = None
        self._rank_options = list(rank_options)

        t = T()

        # 外側コンテナ(角丸 + テーマ追従背景)
        self._container = QWidget(self)
        self._container.setStyleSheet(
            f"background:{t.PANEL.name()};"
            f"border:1px solid {t.BORDER2.name()};"
            f"border-radius:12px;"
        )

        # ドロップシャドウ(_UnsavedChangesDialog と同じ設定)
        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 90))
        self._container.setGraphicsEffect(shadow)

        # outer はシャドウが収まる余白を確保
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 28)
        outer.addWidget(self._container)

        inner = QVBoxLayout(self._container)
        inner.setContentsMargins(28, 24, 28, 20)
        inner.setSpacing(16)

        # タイトル行: アイコン + 「Kizuki へようこそ」テキスト
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)

        # アプリアイコン (assets/logo_mark_{theme}.svg をテーマに応じてロード)
        icon_lbl = QLabel(self._container)
        ICON_PX = 24
        from PyQt6.QtGui import QPixmap as _QPixmap, QPainter as _QPainter
        from PyQt6.QtSvg import QSvgRenderer as _QSvgRenderer
        from PyQt6.QtCore import QRectF as _QRectF_dlg
        from pathlib import Path as _Path
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm_size = max(1, int(round(ICON_PX * dpr)))
        pm = _QPixmap(pm_size, pm_size)
        pm.fill(QColor(0, 0, 0, 0))
        pm.setDevicePixelRatio(dpr)
        ipt = _QPainter(pm)
        ipt.setRenderHint(_QPainter.RenderHint.Antialiasing)
        theme_mode = "dark" if t.BG.lightness() < 128 else "light"
        svg_path = _Path(__file__).parent / "assets" / f"logo_mark_{theme_mode}.svg"
        if svg_path.exists():
            renderer = _QSvgRenderer(str(svg_path))
            if renderer.isValid():
                renderer.render(ipt, _QRectF_dlg(0, 0, ICON_PX, ICON_PX))
        ipt.end()
        icon_lbl.setPixmap(pm)
        icon_lbl.setFixedSize(ICON_PX, ICON_PX)
        icon_lbl.setStyleSheet("background:transparent;border:none;")
        title_row.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        # タイトル: 「Kizuki」だけ少し大きく(20px)、地の文「へようこそ」は
        # Font_LG (18px) で標準。 setFont でベースを Font_LG(bold) にし、
        # HTML <span style> で「Kizuki」部分のみ 20px に上書きする。
        # (Font_LG=18 と Font_XL=24 の中間値で、トークンには無いが微妙な
        #  強調表現として 20px を使用。)
        title = QLabel(self._container)
        title.setFont(Font_LG(bold=True))
        title.setText(
            f"<span style='font-size:20px;'>Kizuki</span>"
            f"<span> へようこそ</span>"
        )
        title.setStyleSheet(
            f"color:{t.TEXT.name()};"
            f"background:transparent;"
            f"border:none;"
        )
        title_row.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)

        inner.addLayout(title_row)

        # 説明文(本文) と 補足注 を別の QLabel に分割。
        # 本文と「※」注を分けることで、注を控えめに見せつつ本文を読みや
        # すくする。階層は フォントサイズ + 色 + 余白 で表現:
        #   ・本文: Font_SM (14px) / T().TEXT (主テキスト色)
        #   ・注  : Font_XS (12px) / T().TEXT2 (薄テキスト色)
        # 本文と注の間隔だけは 4px に詰めたいので、専用のサブレイアウトに
        # まとめ、その中で setSpacing(4) する(inner の spacing は他の要素
        # 間隔としてそのまま維持される)。
        desc_box = QVBoxLayout()
        desc_box.setContentsMargins(0, 0, 0, 0)
        desc_box.setSpacing(4)

        desc = QLabel(
            "Kizukiはあなたの棋力に合わせて悪手や良手を判定します。\n"
            "おおよその棋力を選択してください。",
            self._container,
        )
        desc.setFont(Font_SM())
        desc.setStyleSheet(
            f"color:{t.TEXT.name()};"
            f"background:transparent;"
            f"border:none;"
        )
        desc.setWordWrap(True)
        desc_box.addWidget(desc)

        note = QLabel(
            "※ 後から「設定 → あなたの棋力」で変更できます。",
            self._container,
        )
        note.setFont(Font_XS())
        note.setStyleSheet(
            f"color:{t.TEXT2.name()};"
            f"background:transparent;"
            f"border:none;"
        )
        note.setWordWrap(True)
        desc_box.addWidget(note)

        inner.addLayout(desc_box)

        # ドロップダウン
        self._combo = QComboBox(self._container)
        # maxVisibleItems は showPopup 時に親ウィンドウのサイズから動的計算
        # する (_compute_max_visible_items)。ここでは Qt 既定値のまま。
        # 1番目はプレースホルダ(無効化された項目: 選んでも OK は活性化しない)
        self._combo.addItem("選択してください", None)
        for label, val in self._rank_options:
            self._combo.addItem(label, val)
        self._combo.setCurrentIndex(0)
        # ドロップダウンの見た目をテーマ追従で
        # 「QComboBox { combobox-popup: 0; }」は Qt の設計上、 setStyleSheet
        # を使うと setMaxVisibleItems が効かなくなる挙動を回避するための
        # 公式設定。これを 0 にすると、ポップアップが native menu ではなく
        # itemView ベースで描画され、setMaxVisibleItems が尊重される。
        # ポップアップ内の見た目(背景/角丸/スクロールバー)は view.setStyleSheet
        # で直接設定するため、ここでは ComboBox 本体のみスタイル指定。
        chevron_path = _get_chevron_down_path(t.TEXT2.name())
        self._combo.setFont(Font_SM())
        self._combo.setStyleSheet(
            f"QComboBox{{background:{t.PANEL2.name()};color:{t.TEXT.name()};"
            f"border:1px solid {t.BORDER2.name()};border-radius:6px;"
            f"padding:6px 10px;min-width:160px;"
            f"combobox-popup:0;}}"
            f"QComboBox:hover{{border-color:{t.TEXT2.name()};}}"
            # drop-down ボタン (矢印領域) の枠を消し、幅を確保。
            # subcontrol-position と margin で矢印を右端中央に配置。
            f"QComboBox::drop-down{{subcontrol-origin:padding;"
            f"subcontrol-position:right center;"
            f"border:none;width:24px;}}"
            # ▼ 矢印画像。テーマの TEXT2 色で描画した塗りつぶし三角 SVG。
            # サイズは 10x10 で、視認性とコンパクトさのバランスを取る。
            f"QComboBox::down-arrow{{image:url('{chevron_path}');"
            f"width:10px;height:10px;}}"
        )
        # ── ドロップダウンポップアップの白帯対策 + 角丸 + スクロールバー対策 ──
        # ダイアログが WA_TranslucentBackground + FramelessWindowHint のため、
        # QComboBox のポップアップビュー (子ウィンドウ) の背景透明化が
        # 伝搬し、上下端に Qt 既定の白いフレーム/余白が露出する。
        # 対策の方針:
        #   ・popup_window (= QComboBoxPrivateContainer) は WA_TranslucentBackground
        #     を維持し、背景を「透明」のまま。これで角丸の外側も透けて見える。
        #   ・代わりに 子の QListView (view) 自身に背景色 + 角丸 + 内側 padding
        #     を直接適用。view の角丸と padding が popup の見た目を構成し、
        #     popup_window の余白(白帯) は WA_TranslucentBackground で透明に。
        #   ・スクロールバーポリシーを明示設定 (combobox-popup:0 モードでの対策)
        # ただし view.window() は最初に showPopup() されるまで正しい
        # window を返さないことがあるため、初回 showPopup 後にも
        # 再設定するヘルパーを用意する。
        view = self._combo.view()
        view.setFrameShape(QFrame.Shape.NoFrame)
        # スクロールバーを明示的に有効化 (combobox-popup:0 モードでの対策)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # view 自体に角丸 + 背景 + padding + スクロールバースタイルを直接適用。
        # これが表示される矩形領域そのものの見た目になる。
        view.setStyleSheet(
            f"QListView{{background:{t.PANEL.name()};border:none;"
            f"border-radius:6px;color:{t.TEXT.name()};outline:none;"
            f"padding:4px;}}"
            f"QListView::item{{padding:6px 12px;border-radius:4px;}}"
            f"QListView::item:selected{{background:{t.PANEL2.name()};"
            f"color:{t.TEXT.name()};}}"
            # スクロールバー: 棋力リスト/分岐ツリーと統一
            f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{t.BORDER.name()};"
            f"border-radius:3px;min-height:20px;}}"
            f"QScrollBar::add-line:vertical,"
            f"QScrollBar::sub-line:vertical{{height:0px;}}"
        )
        self._popup_styled = False  # 一度設定したら再設定しないフラグ

        def _apply_popup_style():
            """ポップアップ親ウィンドウを完全に透明化し、Qt 既定の枠線/影を消し、
            さらに setMask で物理的に角丸形状に切り抜く。
            QComboBox.showPopup の度に呼んでも安全(設定済みフラグでガード)

            重要ポイント:
              ・Qt はポップアップに既定で OS のドロップシャドウを描画する。
                Windows 環境では「ポップアップの右下に細い暗い縁」として
                見えるため、NoDropShadowWindowHint で明示的に無効化する。
              ・WA_TranslucentBackground だけでは Windows 環境で角丸の外側
                (角の三角形部分) が黒く残ることがある。これを完全に消す
                には setMask で popup_window を物理的に角丸ポリゴンに
                切り抜く必要がある。
              ・setMask はウィンドウサイズが変わると再計算が必要なので、
                resizeEvent をフックして自動追従させる。
              ・QSS は QComboBoxPrivateContainer (popup_window の実クラス名)
                セレクタに限定して、子の view (QListView) や QScrollBar に
                伝搬しないようにする。view 側のスタイル(角丸・スクロール
                バー等)は別途 view.setStyleSheet で適用済み。
            """
            if self._popup_styled:
                return
            v = self._combo.view()
            popup_window = v.window()
            if popup_window is not None and popup_window is not v:
                # Qt 既定のドロップシャドウを無効化
                popup_window.setWindowFlag(
                    Qt.WindowType.NoDropShadowWindowHint, True)
                popup_window.setAttribute(
                    Qt.WidgetAttribute.WA_TranslucentBackground, True)
                popup_window.setAutoFillBackground(False)
                popup_window.setStyleSheet(
                    f"QComboBoxPrivateContainer{{"
                    f"background:transparent;"
                    f"border:none;"
                    f"}}"
                )
                popup_window.setContentsMargins(0, 0, 0, 0)

                # ── setMask で角丸切り抜き ──
                # popup_window の resizeEvent をフックして、サイズ変更時に
                # 自動で QRegion を再計算して setMask する。
                from PyQt6.QtGui import QPainterPath, QRegion
                from PyQt6.QtCore import QRectF
                _orig_resize = popup_window.resizeEvent
                def _on_resize(ev):
                    if _orig_resize:
                        _orig_resize(ev)
                    sz = popup_window.size()
                    if sz.width() > 0 and sz.height() > 0:
                        path = QPainterPath()
                        path.addRoundedRect(
                            QRectF(0, 0, sz.width(), sz.height()), 6, 6)
                        region = QRegion(path.toFillPolygon().toPolygon())
                        popup_window.setMask(region)
                popup_window.resizeEvent = _on_resize
                # 初回 mask を即適用
                sz = popup_window.size()
                if sz.width() > 0 and sz.height() > 0:
                    path = QPainterPath()
                    path.addRoundedRect(
                        QRectF(0, 0, sz.width(), sz.height()), 6, 6)
                    region = QRegion(path.toFillPolygon().toPolygon())
                    popup_window.setMask(region)

                self._popup_styled = True

        # __init__ 時点で view.window() が取れる場合は今すぐ設定。
        _apply_popup_style()

        # showPopup を wrap して、ポップアップ表示直前に:
        #   1) setStyleSheet を強制適用 (白帯対策 + setMask 角丸)
        #   2) 親ウィンドウサイズから maxVisibleItems を動的計算
        #      (combobox の下端 → ウィンドウ下端の余白に収まる項目数を算出)
        # (Qt のバージョンによっては view.window() が初回 showPopup で
        #  確定するため、この保険が必要)
        _orig_showPopup = self._combo.showPopup
        def _showPopup_wrapped():
            _apply_popup_style()
            self._update_max_visible_items()
            _orig_showPopup()
            # 表示後に最終サイズで mask を再計算 (resizeEvent が走らない
            # ケースの保険)
            popup_window = self._combo.view().window()
            if popup_window is not None and popup_window is not self._combo.view():
                from PyQt6.QtGui import QPainterPath, QRegion
                from PyQt6.QtCore import QRectF
                sz = popup_window.size()
                if sz.width() > 0 and sz.height() > 0:
                    path = QPainterPath()
                    path.addRoundedRect(
                        QRectF(0, 0, sz.width(), sz.height()), 6, 6)
                    popup_window.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self._combo.showPopup = _showPopup_wrapped

        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        inner.addWidget(self._combo)

        # OKボタン(右下に配置)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch()
        self._ok_btn = QPushButton("OK", self._container)
        self._ok_btn.setEnabled(False)  # 初期状態: 未選択 → 無効
        self._ok_btn.setFixedHeight(32)
        self._ok_btn.setMinimumWidth(80)
        self._ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok_btn.setFont(Font_SM(bold=True))
        self._ok_btn.setStyleSheet(self._ok_button_qss(t))
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self._ok_btn)
        inner.addLayout(btn_row)

        # ── ウィンドウ初期サイズ ──
        # コンテンツに合わせて自動調整。説明文 3 行が改行されないよう
        # 最低幅を確保する。
        self.setMinimumWidth(440)

        # ── 開閉アニメ用の初期化 ────────────────────────────────────
        # showEvent で 0→1 にフェードインさせるため、初期透明度を 0 に。
        # _UnsavedChangesDialog と同じ流儀。
        self._show_anim = None
        self._show_anim_group = None
        self._close_anim = None
        self._closing_anim_running = False
        self.setWindowOpacity(0.0)

    # ── 表示・非表示アニメ(_UnsavedChangesDialog と同じ) ────────────
    SHOW_ANIM_MS = 180
    CLOSE_ANIM_MS = 140

    def showEvent(self, ev):
        super().showEvent(ev)
        # 多重起動防止
        if getattr(self, "_show_anim_group", None) is not None:
            return
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(self.SHOW_ANIM_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._show_anim = fade
        self._show_anim_group = fade
        fade.start()

    def _start_close_animation(self, then_call):
        """フェードアウトしてから then_call() を実行する。
        accept() の代替として使う(キャンセル不可ダイアログなので reject なし)。"""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        if self._closing_anim_running:
            return
        self._closing_anim_running = True
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(self.CLOSE_ANIM_MS)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(then_call)
        self._close_anim = fade
        fade.start()

    @staticmethod
    def _ok_button_qss(t) -> str:
        """OKボタンの QSS。有効・無効・ホバー で見た目を変える。
        フォントは setFont(Font_SM(bold=True)) で別途指定済み。"""
        return (
            # 有効: アクセント色背景
            f"QPushButton{{background:{t.TEXT.name()};color:{t.BG.name()};"
            f"border:none;border-radius:6px;padding:0 16px;}}"
            f"QPushButton:hover{{background:{t.TEXT2.name()};}}"
            # 無効: 暗くする
            f"QPushButton:disabled{{background:{t.BORDER2.name()};"
            f"color:{t.TEXT2.name()};}}"
        )

    def _update_max_visible_items(self):
        """親ウィンドウ (MainWindow) のサイズから、ポップアップに表示する
        最大項目数を計算して setMaxVisibleItems で設定する。

        計算ロジック:
          ・combobox の下端 → 親ウィンドウ下端の利用可能高さ (avail_h)
          ・1項目あたりの高さ ≒ row_h
          ・最大表示項目数 = avail_h / row_h
          ・ただし下限は 4 項目 (極端に小さなウィンドウでも UI として成立)
          ・上限は 全項目数 (RANK_OPTIONS + プレースホルダ = 24 個)

        QSS に combobox-popup:0 を設定済みなので setMaxVisibleItems は
        確実に尊重される。残りはポップアップ内蔵スクロールで操作可能。
        """
        # 親ウィンドウ取得
        pw = self.parentWidget()
        if pw is None:
            self._combo.setMaxVisibleItems(10)
            return
        top_window = pw.window()
        bound = top_window.geometry() if top_window else pw.geometry()

        # combobox 下端のスクリーン Y 座標
        combo_top_global = self._combo.mapToGlobal(self._combo.rect().topLeft())
        combo_bottom_y = combo_top_global.y() + self._combo.height()
        # 親ウィンドウ下端までの利用可能高さ (下に 16px 余白)
        avail_h = bound.bottom() - combo_bottom_y - 16

        # 1項目あたりの高さ: view.sizeHintForRow(0) があればそれを、
        # なければ font height ベースで概算 (≒ 26-28px)
        v = self._combo.view()
        row_h = v.sizeHintForRow(0) if self._combo.count() > 0 else 0
        if row_h <= 0:
            from PyQt6.QtGui import QFontMetrics as _QFM
            row_h = _QFM(self._combo.font()).height() + 12  # padding 6+6 ≈ 12

        # 表示可能項目数 = 利用可能高さ ÷ 行高
        max_items = max(4, avail_h // row_h)
        # 全項目数を上限にクランプ (項目数より多く指定しても無意味)
        max_items = min(max_items, self._combo.count())
        self._combo.setMaxVisibleItems(int(max_items))

    def _on_combo_changed(self, idx: int):
        """ドロップダウン選択変更時。プレースホルダ以外なら OK を活性化。"""
        # idx=0 はプレースホルダ ("選択してください")
        is_real = idx > 0
        self._ok_btn.setEnabled(is_real)
        if is_real:
            self._selected_rank = self._combo.itemData(idx)

    def _on_ok(self):
        """OK ボタン押下: フェードアウト後にダイアログを閉じる。"""
        if self._selected_rank is not None:
            self._start_close_animation(self.accept)

    # ── キャンセル不可化 ──
    def keyPressEvent(self, ev):
        # ESC を無視 (Qt 既定では ESC で reject される)
        if ev.key() == Qt.Key.Key_Escape:
            ev.ignore()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        # × ボタンや WM の閉じる動作も無視
        # (frameless なので × ボタンは無いが、Alt+F4 等の経路をブロック)
        ev.ignore()

    def reject(self):
        # ESC キーや × ボタンでの閉じる操作を無効化する。
        # このダイアログは棋力選択が必須のため、選択完了まで閉じられない設計。
        pass

    def selected_rank(self) -> int | None:
        """選択された棋力値を返す。未選択なら None。"""
        return self._selected_rank


class MainWindow(QMainWindow):
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

    def _show_first_launch_rank_dialog(self):
        """初回起動時の棋力選択ダイアログを表示し、選択値を保存する。
        QSettings.contains("player_rank") が False の時のみ呼ばれる想定。
        """
        dlg = _FirstLaunchRankDialog(self.RANK_OPTIONS, parent=self)
        # 親 (= MainWindow) の中央に配置
        dlg.adjustSize()
        parent_geo = self.geometry()
        x = parent_geo.x() + (parent_geo.width() - dlg.width()) // 2
        y = parent_geo.y() + (parent_geo.height() - dlg.height()) // 2
        dlg.move(x, y)
        # モーダル表示 (キャンセル不可なので必ず accept で抜ける)
        dlg.exec()
        rank = dlg.selected_rank()
        if rank is not None:
            from PyQt6.QtCore import QSettings as _QS_save
            _QS_save("Kizuki", "Kizuki").setValue("player_rank", int(rank))
            set_player_rank(int(rank))
            # 棋力リストの選択中アイテムも更新(通常は _on_rank_action 経由だが
            # ここはダイアログから直接設定するので手動で同期する)
            lw = getattr(self, "_rank_list_widget", None)
            if lw is not None:
                for i in range(lw.count()):
                    it = lw.item(i)
                    if it is None:
                        continue
                    v = it.data(Qt.ItemDataRole.UserRole)
                    it.setData(Qt.ItemDataRole.UserRole + 1, (v == rank))
                lw.viewport().update()
            # 解析キャッシュをクリアして再描画
            self._node_analyses = {}
            if self._game_state:
                self._refresh_board()
                self._update_graph()
            if self._ai_enabled:
                self._start_pondering_current()

    # ── 全画面 D&D ハンドラ ─────────────────────────────────────────
    # SGF 棋譜ファイルをウィンドウ全体のどこにドロップしても開けるようにする。
    # オーバーレイはタイトルバーを除外した root 領域全体を覆い、中央に
    # 「棋譜を開く」カードを表示する。
    _SUPPORTED_KIFU_EXTS = (".sgf",)

    def _has_supported_kifu(self, ev) -> bool:
        if not ev.mimeData().hasUrls():
            return False
        return any(
            u.toLocalFile().lower().endswith(self._SUPPORTED_KIFU_EXTS)
            for u in ev.mimeData().urls()
        )

    def _show_drop_overlay(self):
        """D&D オーバーレイを root 全体に広げて表示する。
        _root_widget の子にしているため、タイトルバーは覆わない。
        """
        if not hasattr(self, "_drop_overlay"):
            return
        rw, rh = self._root_widget.width(), self._root_widget.height()
        self._drop_overlay.setGeometry(0, 0, rw, rh)
        cw = self._drop_card.width()
        ch = self._drop_card.height()
        self._drop_card.move((rw - cw) // 2, (rh - ch) // 2)
        self._drop_overlay.raise_()
        self._drop_overlay.show()

    def _hide_drop_overlay(self):
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.hide()

    def _apply_comment_close_btn_qss(self):
        """コメントオーバーレイの ✕ ボタンに専用スタイルを適用する。
        色は T().TEXT(ダーク=#fff、ライト=#333)。ホバー時も背景は変えない。
        テーマ切替時にも再適用するため _apply_theme_immediate から呼ばれる。
        """
        if not hasattr(self, "_comment_close_btn"):
            return
        color = T().TEXT.name()
        self._comment_close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:4px;"
            f"color:{color};font-size:12px;padding:0px 0px 2px 0px;}}"
            f"QPushButton:hover{{background:transparent;color:{color};}}"
        )

    def dragEnterEvent(self, ev):
        if self._has_supported_kifu(ev):
            ev.acceptProposedAction()
            self._show_drop_overlay()
            return
        ev.ignore()

    def dragMoveEvent(self, ev):
        # dragEnter で acceptProposedAction しても、Qt によっては
        # dragMove で再評価されるためここでも継続的に受け取る
        if self._has_supported_kifu(ev):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev):
        self._hide_drop_overlay()

    def dropEvent(self, ev):
        self._hide_drop_overlay()
        paths = [u.toLocalFile() for u in ev.mimeData().urls()
                 if u.toLocalFile().lower().endswith(self._SUPPORTED_KIFU_EXTS)]
        if paths:
            ev.acceptProposedAction()
            # ウェルカム画面でも碁盤画面でも同じ _open_sgf_path に流す
            self._open_sgf_path(paths[0])

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

    def _apply_rules_komi_to_engine(self, *, restart_pondering: bool = True):
        """現在のルール・コミ・置き石を解析エンジンに反映する。

        SGF が読み込まれていれば、その AB/AW を置き石として渡す。
        restart_pondering=True なら現在表示中ノードのポンダリングを再起動する。
        """
        if not self._engine:
            return
        # 置き石を SGF から取得（読み込まれていなければ空リスト）
        initial_stones: list[tuple[str, str]] = []
        if self._game is not None:
            root = self._game.root
            bs = self._game.board_size
            cols = "ABCDEFGHJKLMNOPQRST"
            for color, key in [("B", "AB"), ("W", "AW")]:
                for coord in root.get_all(key):
                    pos = sgf_coord_to_pos(coord)
                    if pos is None:
                        continue
                    col, row = pos
                    gtp = f"{cols[col]}{bs - row}"
                    initial_stones.append((color, gtp))
        # ポンダリングを停止してから設定変更（次クエリから反映）
        if self._engine.is_running():
            try:
                self._engine.stop_pondering()
            except Exception:
                pass
            try:
                self._engine.set_game_info(
                    self._current_komi, self._current_rules, initial_stones)
            except Exception as e:
                logger.warning("Failed to apply game info to engine: %s", e)
        # 解析を再開
        if restart_pondering:
            try:
                self._start_pondering_current()
            except Exception:
                pass

    def _on_rules_changed(self, rules_key: str):
        """ルールメニュー変更時。
        ルールを更新すると同時にコミもそのルールの標準値にリセットする。
        例: 中国ルール→7.5、日本ルール→6.5、NZ→7.0。
        """
        if rules_key == self._current_rules:
            return
        self._current_rules = rules_key
        # ルール標準コミを取得（不明なルールは現状の DEFAULT_KOMI）
        new_komi = self.RULE_DEFAULT_KOMI.get(rules_key, self.DEFAULT_KOMI)
        komi_changed = abs(new_komi - self._current_komi) > 1e-6
        if komi_changed:
            self._current_komi = new_komi
            # 解析キャッシュをクリア（コミが変わると形勢が変わるため）
            self._node_analyses = {}
            # ルール由来のコミ変更はプリセット標準値(6.5/7.5など)に切替なので
            # 「その他」経由フラグはクリアし、プリセット側のチェックに戻す。
            self._komi_via_other = False
            self._sync_komi_menu_check(new_komi)
        # QSettings 保存
        from PyQt6.QtCore import QSettings
        qs = QSettings("Kizuki", "Kizuki")
        qs.setValue("katago_rules", rules_key)
        if komi_changed:
            qs.setValue("katago_komi", new_komi)
        # エンジンに反映
        self._apply_rules_komi_to_engine()
        # InfoPanel 表示を更新
        if hasattr(self, "_info") and self._info is not None:
            try:
                self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            except Exception:
                pass

    def _sync_komi_menu_check(self, komi: float):
        """コミメニューのチェック状態を現在値と「経路フラグ」に応じて同期する。

        ・_komi_via_other == False(プリセットボタン経由で確定した最新):
            → プリセット側のチェックを付与。「その他」側はチェックなし。
              「その他」ウィジェットの数値は変更しない(ユーザーが調整中の値を保持)。
        ・_komi_via_other == True(その他クリック経由で確定した最新):
            → プリセットチェック全解除、「その他」側にチェック付与。
              ウィジェットの数値も現在値に同期する。

        ルール変更による自動コミ調整(ルールメニューから来た時)は、プリセット
        値に切り替わるのが普通なので、プリセット経由扱いで _on_komi_changed を
        呼べばよい。
        """
        if not hasattr(self, "_action_komi"):
            return  # メニュー構築前
        via_other = getattr(self, "_komi_via_other", False)
        if via_other:
            # その他経由: プリセットの全チェックを外す
            for act in self._action_komi.values():
                act.setChecked(False)
        else:
            # プリセット経由: 該当プリセットにチェック、該当無しなら全外し
            match = None
            for v, act in self._action_komi.items():
                if abs(v - komi) < 1e-6:
                    match = act
                    break
            if match is not None:
                match.setChecked(True)
            else:
                # プリセット経由なのに該当なし → 想定外ケースとして全外し
                for act in self._action_komi.values():
                    act.setChecked(False)
        # 「その他」側のチェック表示
        if hasattr(self, "_komi_custom_widget") and self._komi_custom_widget is not None:
            self._komi_custom_widget.set_checked(via_other)
            # その他経由時はウィジェット数値も現在値に合わせる
            # (プリセット経由時はウィジェットの調整中値を保持するため変更しない)
            if via_other:
                self._komi_custom_widget.set_value(komi)

    def _on_komi_changed(self, komi: float):
        """プリセットボタン経由でのコミ変更。エンジン設定更新 + QSettings 保存 +
        メニュー側のチェック状態同期。プリセット経由なので _komi_via_other は False。"""
        if abs(komi - self._current_komi) < 1e-6:
            # 現在値と同じ → 「その他」経由フラグだけ解除して終了
            # (例: その他で 6.5 を確定した状態から、プリセット 6.5 を再クリック)
            if getattr(self, "_komi_via_other", False):
                self._komi_via_other = False
                self._sync_komi_menu_check(komi)
            return
        self._current_komi = komi
        self._komi_via_other = False
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("katago_komi", komi)
        # 解析キャッシュをクリア（コミ変更で形勢が変わるため）
        self._node_analyses = {}
        self._apply_rules_komi_to_engine()
        # メニューのチェック状態を同期(その他ウィジェットの値は変えない)
        self._sync_komi_menu_check(komi)
        if hasattr(self, "_info") and self._info is not None:
            try:
                self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            except Exception:
                pass

    def _on_komi_custom_confirmed(self, komi: float):
        """「その他」インラインウィジェットのクリックで現在値を確定する。
        値がプリセットと同値であっても、「その他」経由として扱い、チェック印は
        「その他」側に付与する。確定後はメニューを閉じる。"""
        # 値が変わらない場合でも「その他」経由フラグだけ立てる必要がある
        # (例: 現在 6.5 プリセット選択中、その他で 6.5 のままクリック →
        #  チェックを「その他」側に移したい)
        changed = abs(komi - self._current_komi) >= 1e-6
        self._current_komi = komi
        self._komi_via_other = True
        from PyQt6.QtCore import QSettings
        qs = QSettings("Kizuki", "Kizuki")
        qs.setValue("katago_komi", komi)
        # 「その他」値の永続化(次回起動時の初期表示用)
        qs.setValue("katago_komi_other_value", komi)
        if changed:
            # 解析キャッシュをクリア
            self._node_analyses = {}
            self._apply_rules_komi_to_engine()
            if hasattr(self, "_info") and self._info is not None:
                try:
                    self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
                except Exception:
                    pass
        # メニューのチェック状態を同期
        self._sync_komi_menu_check(komi)
        # メニューを閉じる
        if hasattr(self, "_komi_menu") and self._komi_menu is not None:
            self._komi_menu.close()

    def _on_komi_realtime_change(self, komi: float):
        """「その他」選択中の ± ボタンによる数値変更を即時反映する。
        _on_komi_custom_confirmed と違い、メニューは閉じない(連続調整のため)。
        - エンジン設定更新、解析キャッシュクリア、QSettings 保存
        - チェック表示は既に「その他」側に付いているため、_sync_komi_menu_check
          は呼ばずに最小限の状態更新で済ませる(再描画コストを抑える)。
        """
        if abs(komi - self._current_komi) < 1e-6:
            return
        self._current_komi = komi
        # _komi_via_other は True のまま維持(その他選択中の状態を保つ)
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("katago_komi", komi)
        # 解析キャッシュをクリア
        self._node_analyses = {}
        self._apply_rules_komi_to_engine()
        if hasattr(self, "_info") and self._info is not None:
            try:
                self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            except Exception:
                pass

    def _on_komi_other_value_adjusted(self, value: float):
        """「その他」ウィジェットの ± ボタンで値が変わった時の永続化。
        プリセット選択中の ± 操作(コミ確定なし)でも、ユーザーの調整値を
        次回起動時に復元できるよう QSettings に保存する。
        コミ自体の確定処理は別経路(_on_komi_realtime_change /
        _on_komi_custom_confirmed)で行うので、ここでは保存だけ。"""
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("katago_komi_other_value", value)

    def _on_volume_changed(self, value: int):
        """音量スライダー変更時。SoundPlayer に反映、ラベル更新、QSettings 保存。
        0% は実質ミュート（再生を抑制）として動作する。
        """
        vol = max(0.0, min(1.0, value / 100.0))
        self._sound.volume = vol
        if hasattr(self, "_volume_label"):
            # ラベルは固定中央配置(縦スライダー)。テキストだけ更新する。
            self._volume_label.setText(f"{value}%")
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("sound_volume", vol)
        # タイトルバー音量アイコンを 0% かどうかで切り替え
        if hasattr(self, "_titlebar") and hasattr(self._titlebar, "_btn_volume"):
            self._titlebar.update_volume_icon(muted=(value <= 0))

    def _on_volume_icon_clicked(self):
        """タイトルバー右端の音量アイコンクリック時に、音量メニューをトグル popup する。
        - 閉じている時 → popup
        - 開いている時に再クリック → 閉じる
          (Qt は popup 外クリック扱いで先に menu を閉じてから clicked を発火させるため、
           受信時 isVisible() == False になっている。set_menu と同様、直近の close 時刻を
           記録して 200ms 以内のクリックは「閉じる動作の続き」として再表示を抑制する)
        - 外側クリック → Qt 標準で自動的に閉じる(popup の挙動)
        位置はアイコンボタンの直下(VS Code/Win11 風)。
        """
        if not hasattr(self, "_volume_menu") or not hasattr(self, "_titlebar"):
            return
        btn = getattr(self._titlebar, "_btn_volume", None)
        if btn is None:
            return
        m = self._volume_menu

        # 初回呼び出し時に aboutToHide で close 時刻を記録するハンドラを接続
        # (set_menu と同じ仕組み)
        if not getattr(m, "_kizuki_volume_toggle_installed", False):
            self._volume_close_state = {"last_close_ms": 0}
            def _on_about_to_hide():
                from PyQt6.QtCore import QDateTime
                now_ms = QDateTime.currentMSecsSinceEpoch()
                self._volume_close_state["last_close_ms"] = now_ms
                # 他メニューと共通の「直近メニュー終了時刻」も更新
                # (タイトルバーのドラッグ判定で使われる)
                if hasattr(self, "_titlebar"):
                    self._titlebar._last_any_menu_close_ms = now_ms
            m.aboutToHide.connect(_on_about_to_hide)
            m._kizuki_volume_toggle_installed = True

        # トグル: 直近(200ms以内)に閉じたばかりなら再表示しない
        from PyQt6.QtCore import QDateTime
        st = getattr(self, "_volume_close_state", {"last_close_ms": 0})
        now = QDateTime.currentMSecsSinceEpoch()
        if now - st["last_close_ms"] < 200:
            return

        # ボタン下端の左端をグローバル座標に変換して popup
        from PyQt6.QtCore import QPoint
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        # メニュー幅がボタンより広い場合、画面右端をはみ出さないよう左にずらす
        # (アイコンが右端付近にあるため特に重要)
        screen_geom = self.screen().availableGeometry() if self.screen() else None
        menu_size_hint = m.sizeHint()
        if screen_geom is not None:
            x_max = screen_geom.right() - menu_size_hint.width() - 4
            if pos.x() > x_max:
                pos.setX(max(screen_geom.left() + 4, x_max))
        m.popup(pos)

    def _volume_slider_mouse_press(self, ev):
        """音量スライダーのグルーブクリック時に、クリック位置へハンドルを直接移動させる。
        ハンドル上のクリックは通常のドラッグ動作に委譲する。水平・垂直両対応。
        """
        if ev.button() == Qt.MouseButton.LeftButton:
            slider = self._volume_slider
            handle_half = SLIDER_HANDLE // 2
            is_vertical = slider.orientation() == Qt.Orientation.Vertical
            if is_vertical:
                # 縦: クリックの y 座標 → ハンドル位置(上=最大、下=最小)
                available = slider.height() - handle_half * 2
                ratio = ((slider.value() - slider.minimum()) /
                         max(1, slider.maximum() - slider.minimum()))
                # 現在のハンドルの y(上=最大なので 1-ratio)
                handle_pos = handle_half + (1.0 - ratio) * available
                click_pos = ev.position().y()
            else:
                available = slider.width() - handle_half * 2
                ratio = ((slider.value() - slider.minimum()) /
                         max(1, slider.maximum() - slider.minimum()))
                handle_pos = handle_half + ratio * available
                click_pos = ev.position().x()
            # ハンドル上のクリックは通常のドラッグに委譲
            if abs(click_pos - handle_pos) <= handle_half + 2:
                QSlider.mousePressEvent(slider, ev)
                return
            # ハンドル以外のクリック: クリック位置に直接ジャンプ
            pos = click_pos - handle_half
            r = max(0.0, min(1.0, pos / available if available > 0 else 0))
            if is_vertical:
                # 上が最大なので反転
                r = 1.0 - r
            value = round(slider.minimum() + r * (slider.maximum() - slider.minimum()))
            slider.setValue(value)
            # ジャンプ後、ドラッグ追従できるよう通常の press イベントも転送
            QSlider.mousePressEvent(slider, ev)
            ev.accept()
        else:
            QSlider.mousePressEvent(self._volume_slider, ev)

    @classmethod
    def _scan_models(cls) -> list[str]:
        """katago/models/ 直下の .bin.gz ファイル名一覧を返す。

        サブフォルダは見ない。アルファベット順でソートして返す。
        起動時のモデル数チェック (_check_models_or_exit) でも使われる。
        """
        from pathlib import Path
        models_dir = Path(cls.KATAGO_DIR) / "models"
        if not models_dir.is_dir():
            return []
        try:
            return sorted(
                p.name for p in models_dir.iterdir()
                if p.is_file() and p.name.endswith(".bin.gz")
            )
        except OSError:
            return []

    def _create_engine(self, model_file: str) -> "KataGoEngine":
        """指定モデルファイル名に対応する KataGoEngine インスタンスを生成して返す（起動はしない）。
        モデル数の事前チェックは main() 側の _check_models_or_exit() が担当しており、
        この関数に渡される model_file は katago/models/ 直下に存在することが保証されている。
        """
        from pathlib import Path
        models_dir = Path(self.KATAGO_DIR) / "models"
        model_path = models_dir / model_file

        return KataGoEngine(
            executable=str(Path(self.KATAGO_DIR) / "katago.exe"),
            model=str(model_path),
            config=str(Path(self.KATAGO_DIR) / "analysis.cfg"),
            human_model="",
            board_size=19, komi=6.5,
        )

    def _open_color_adjustment(self):
        """カラー調整ダイアログ(開発用)を開く。"""
        dlg = ColorAdjustmentDialog(self)
        dlg.setModal(False)  # 非モーダル: 開いた状態で本体UIを操作可能に
        dlg.show()
        # 参照を保持(GC防止)
        self._color_adj_dialog = dlg

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

    def apply_theme(self, mode: str):
        """テーマを即時切り替えする。mode = 'light' or 'dark'。

        切替演出: 旧テーマ画面を QPixmap でキャプチャしてオーバーレイし、
        裏で新テーマを適用、オーバーレイを 250ms かけてフェードアウトする
        ことでクロスフェードを実現する。
        """
        # フェード中に重複呼び出しが来た場合は、進行中のフェードを破棄して
        # 新しい mode で再開する(連打しても破綻しないように)。
        if getattr(self, "_theme_fade_running", False):
            self._cancel_theme_fade()

        # ── 旧テーマ画面のキャプチャ ──
        # centralWidget をキャプチャしてオーバーレイ表示する。
        # キャプチャはタイトルバー含む centralWidget 全体。
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtCore import Qt as _Qt
        cw = self.centralWidget()
        overlay = None
        if cw is not None and cw.width() > 0 and cw.height() > 0:
            try:
                pix = cw.grab()  # 旧テーマでの現状描画をキャプチャ
                overlay = QLabel(cw)
                overlay.setObjectName("theme_fade_overlay")
                overlay.setPixmap(pix)
                overlay.setGeometry(0, 0, cw.width(), cw.height())
                overlay.setAttribute(
                    _Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                overlay.raise_()
                overlay.show()
                self._theme_fade_overlay = overlay
                # オーバーレイを実際の描画パイプラインに反映させる。
                # processEvents を呼ばないと、後続の _apply_theme_immediate
                # (大量のスタイルシート更新で時間がかかる) の処理中、
                # オーバーレイがまだ描画されていない状態で中身の色が変わる
                # → ちらつきになる可能性がある。
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
            except Exception:
                # grab 失敗時はフェードなしで即時切替にフォールバック
                overlay = None
                self._theme_fade_overlay = None
        else:
            self._theme_fade_overlay = None

        # ── 新テーマを即時適用(オーバーレイで隠蔽されているため画面には出ない) ──
        self._apply_theme_immediate(mode)

        # オーバーレイがなければ通常の即時切替で終了
        if overlay is None:
            return

        # ── オーバーレイをフェードアウト ──
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        eff = QGraphicsOpacityEffect(overlay)
        eff.setOpacity(1.0)
        overlay.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity")
        anim.setDuration(250)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_theme_fade_done)
        self._theme_fade_anim = anim
        self._theme_fade_effect = eff
        self._theme_fade_running = True
        anim.start()

    def _on_theme_fade_done(self):
        """テーマフェード完了時の後処理: オーバーレイ破棄。"""
        ov = getattr(self, "_theme_fade_overlay", None)
        if ov is not None:
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
            ov.hide()
            ov.deleteLater()
        self._theme_fade_overlay = None
        self._theme_fade_anim = None
        self._theme_fade_effect = None
        self._theme_fade_running = False

    def _cancel_theme_fade(self):
        """進行中のテーマフェードを即座に終了する。"""
        anim = getattr(self, "_theme_fade_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
        self._on_theme_fade_done()

    def _apply_theme_immediate(self, mode: str):
        """テーマを即時切り替えする(フェードなし)。
        apply_theme から呼ばれる本体ロジック。
        """
        from PyQt6.QtCore import QSettings
        _theme.set_mode(mode)
        QSettings("Kizuki", "Kizuki").setValue("theme", mode)

        # ── メニューのチェック状態を同期 ──
        if hasattr(self, "_action_light"):
            self._action_light.setChecked(mode == "light")
            self._action_dark.setChecked(mode == "dark")

        # ── 1. MainWindow・共通スタイル ──────────────────────────────────
        self.setStyleSheet(
            f"QMainWindow{{background:{T().BG.name()};}}"
            + menu_qss()
            + statusbar_qss()
        )

        # ── 2. centralWidget ──────────────────────────────────────────────
        cw = self.centralWidget()
        if cw:
            cw.setStyleSheet(f"background:{T().BG.name()};")
            from PyQt6.QtGui import QPalette as _QPalette
            _cw_pal = _QPalette()
            _cw_pal.setColor(_QPalette.ColorRole.Window, T().BG)
            _cw_pal.setColor(_QPalette.ColorRole.Base, T().BG)
            cw.setPalette(_cw_pal)
            cw.setAutoFillBackground(True)
        # _root_widget(outer 内のコンテンツ領域) も同様に。setStyleSheet だけ
        # では子ウィジェットを越えてリフレッシュされず、テーマ切替直後に右
        # カラムを開閉した時に旧テーマ色が一瞬見えることがある。QPalette +
        # autoFillBackground を併用して確実に背景を更新する。
        rw = getattr(self, "_root_widget", None)
        if rw:
            rw.setStyleSheet(f"background:{T().BG.name()};")
            from PyQt6.QtGui import QPalette as _QPalette
            _rw_pal = _QPalette()
            _rw_pal.setColor(_QPalette.ColorRole.Window, T().BG)
            _rw_pal.setColor(_QPalette.ColorRole.Base, T().BG)
            rw.setPalette(_rw_pal)
            rw.setAutoFillBackground(True)
        # カスタムタイトルバーのテーマ追従
        if hasattr(self, "_titlebar"):
            self._titlebar.apply_theme()
        # タイトルバーに紐付けた各 QMenu(ポップアップ)に menu_qss を再適用。
        # QMenu は top-level popup として表示されるため、MainWindow の
        # setStyleSheet で書いた menu_qss は継承されない。個別に設定が必要。
        for menu_attr in ("_komi_menu", "_rank_menu", "_volume_menu"):
            m = getattr(self, menu_attr, None)
            if m is not None:
                m.setStyleSheet(menu_qss())
        # コミメニューの「その他」インラインウィジェットも apply_theme
        if hasattr(self, "_komi_custom_widget") and self._komi_custom_widget is not None:
            self._komi_custom_widget.apply_theme()
        # 棋力メニュー内の QListWidget にもテーマ追従の QSS を再適用
        # (menu_qss は QListWidget には効かないため別途必要)。
        # 描画は _RankItemDelegate が担当し、テーマ色は delegate 内部の
        # _cached_color と一致しなければ自動再生成されるため、ここでは
        # キャッシュ無効化と viewport 再描画のみ実行。
        rl = getattr(self, "_rank_list_widget", None)
        if rl is not None:
            rl.setStyleSheet(rank_list_qss())
            d = getattr(self, "_rank_item_delegate", None)
            if d is not None:
                d._cached_pix = None
                d._cached_color = ""
            rl.viewport().update()
        # その他のメニュー(ファイル/表示/設定とその配下のサブメニュー)は
        # findChildren(QMenu) で全部スキャンして適用する。
        from PyQt6.QtWidgets import QMenu
        for m in self.findChildren(QMenu):
            m.setStyleSheet(menu_qss())

        # ── 3. 左カラム・QStackedWidget ────────────────────────────────
        from PyQt6.QtGui import QPalette as _QPalette
        # palette を Window/Base 両方に設定 + setAutoFillBackground(True) を併用
        # することで、setStyleSheet だけでは取りこぼしがある領域も確実に塗られる。
        # 特に「ダーク → ライト切替直後に右カラム閉じる時」など、隠れていた
        # 親領域が突然見える瞬間に旧テーマ色 (黒) が露出する問題の対策。
        _bg_pal = _QPalette()
        _bg_pal.setColor(_QPalette.ColorRole.Window, T().BG)
        _bg_pal.setColor(_QPalette.ColorRole.Base, T().BG)
        for attr in ("_left_col", "_left_stack", "_board_container"):
            w = getattr(self, attr, None)
            if w is None:
                continue
            w.setStyleSheet(f"background:{T().BG.name()};")
            w.setPalette(_bg_pal)
            w.setAutoFillBackground(True)

        # ── 4. フローティングパネル背景 ──────────────────────────────────
        if hasattr(self, "_right_col"):
            self._right_col.setStyleSheet(
                f"QWidget#floating_panel {{"
                f"  background:{T().PANEL.name()};"
                f"  border-radius:16px;"
                f"}}"
            )
            # _right_col は border-radius:16px の角丸ウィジェット。QSS の
            # background は角丸の内側しか塗らないため、リサイズ中など再描画
            # タイミングで角丸の外(角の三角形領域)に古いフレームの色が残る。
            # そこで palette を BG 色に揃えておく。
            # NOTE: setAutoFillBackground(True) はテーマ切替時には呼ばない。
            # 初期化時に True 設定済みで、その状態は維持される。テーマ切替後に
            # 再呼び出しすると、続けて発火する右パネル開閉アニメの
            # QGraphicsOpacityEffect と相互作用して、右カラム全体に黒い
            # オーバーレイが乗る現象が発生するため(原因はおそらく
            # autoFillBackground 再設定が effect の合成バッファに影響する)。
            self._right_col.setPalette(_bg_pal)

        # NavBar: スライダースタイル更新 + palette を BG に揃える
        # (palette がデフォルト黒のまま残ると、ライトモードでリサイズ時に
        #  一瞬黒い領域が見える原因となる)
        if hasattr(self, "_navbar"):
            self._navbar._apply_slider_style()
            self._navbar.setPalette(_bg_pal)

        # 音量スライダー（メニュー内）
        if hasattr(self, "_volume_slider"):
            self._volume_slider.update()  # FlatSlider は paintEvent で描画
        if hasattr(self, "_volume_label"):
            self._volume_label.setStyleSheet(
                f"color:{T().TEXT.name()}; background:transparent; font-size:14px;"
            )

        # ── 6. スクロールエリア + カード背景 ──────────────────────────────
        if hasattr(self, "_cards_scroll"):
            self._cards_scroll.setStyleSheet(
                f"QScrollArea {{ border:none; background:{T().BG.name()}; }}"
                f"QScrollArea > QWidget > QWidget {{ background:{T().BG.name()}; }}"
            )
            # NOTE: setAutoFillBackground(True) はテーマ切替時には呼ばない。
            # 初期化時に True 設定済みで、その状態は維持される。
            # 詳細は _right_col の同様コメント参照(右パネル開閉アニメの
            # QGraphicsOpacityEffect と相互作用して黒オーバーレイが出る)。
            from PyQt6.QtGui import QPalette as _QPalette
            # viewport
            _vp = self._cards_scroll.viewport()
            _pal = _QPalette()
            _pal.setColor(_QPalette.ColorRole.Window, T().BG)
            _pal.setColor(_QPalette.ColorRole.Base, T().BG)
            _vp.setPalette(_pal)
            # cards_widget
            w = self._cards_scroll.widget()
            if w:
                w.setStyleSheet(f"background:{T().BG.name()};")
                _wpal = _QPalette()
                _wpal.setColor(_QPalette.ColorRole.Window, T().BG)
                _wpal.setColor(_QPalette.ColorRole.Base, T().BG)
                w.setPalette(_wpal)

        # ── 7. コメントカード ────────────────────────────────────────────
        if hasattr(self, "_comment"):
            # ObjectName ベースで親カードを探す
            card = self._comment.parent()
            if card:
                card.setStyleSheet(
                    f"QWidget#comment_card {{"
                    f"  background:{T().PANEL.name()};"
                    f"  border:1px solid {T().BORDER2.name()};"
                    f"  border-radius:12px;"
                    f"}}"
                )
            self._comment.setStyleSheet(
                f"QTextEdit {{ background:transparent; color:{T().TEXT.name()}; "
                f"border:none; font-size:16px;"
                f"font-family:'Yu Gothic UI','BIZ UDGothic'; }}"
            )

        # ── 8. セパレータ ─────────────────────────────────────────────────
        from PyQt6.QtWidgets import QFrame
        for frame in self.findChildren(QFrame):
            ss = frame.styleSheet()
            # BORDER2 色のセパレータを更新（高さ1pxの区切り線）
            if frame.maximumHeight() == 1 and frame.minimumHeight() == 1:
                frame.setStyleSheet(f"background:{T().BORDER2.name()};")

        # ── 9. paintEvent 系ウィジェット（update() で自動再描画） ─────────
        if hasattr(self, "_board"):
            self._board.update()
        if hasattr(self, "_branch_tree"):
            self._branch_tree.update()
        if hasattr(self, "_info"):
            self._info.apply_theme()

        # ── 10. pyqtgraph グラフ（InfoPanel.apply_theme 内で処理済み） ────

        # ── 11. ウェルカム画面 ───────────────────────────────────────────
        if hasattr(self, "_welcome_pane"):
            self._welcome_pane.apply_theme()

        # ── 12. D&D オーバーレイ内のカード ───────────────────────────────
        # 半透明背景はテーマに依らず固定だが、中央のカード(_WelcomeCard)は
        # T().TEXT/BORDER 等を paintEvent で参照するためテーマ追従が必要
        if hasattr(self, "_drop_card"):
            self._drop_card.update()

        # ── 13. 手の情報カード・トグルバー ───────────────────────────────
        if hasattr(self, "_move_card"):
            self._move_card.apply_theme()
        if hasattr(self, "_toggle_bar"):
            self._toggle_bar.apply_theme()

        # ── 14. スライダーマーカーオーバーレイ ───────────────────────────
        if hasattr(self, "_navbar") and hasattr(self._navbar, "_marker_overlay"):
            self._navbar._marker_overlay.update()

        # ── 15. コメントオーバーレイ ─────────────────────────────────────
        if hasattr(self, "_comment_textedit"):
            self._comment_textedit.setStyleSheet(
                f"QTextEdit {{ background:transparent; color:{T().TEXT.name()};"
                f" border:none; font-size:14px;"
                f" font-family:'Yu Gothic UI','BIZ UDGothic'; }}"
            )
            # フェードオーバーレイの色も追従させる(再描画で次の paintEvent に反映)
            if hasattr(self._comment_textedit, "_fade_overlay"):
                self._comment_textedit._fade_overlay.update()
        if hasattr(self, "_comment_overlay"):
            self._comment_overlay.update()
        # ✕ボタンの色(T().TEXT)もテーマ追従させる
        self._apply_comment_close_btn_qss()

        # 全体に強制 update を発行。setStyleSheet/setPalette だけでは
        # 子ウィジェットの再描画が起きない領域があり、テーマ切替直後に
        # アニメで「隠れていた領域が見える」場面で旧テーマ色が露出する
        # ことがあるため、_root_widget 配下を含めて明示的に update する。
        self.update()
        if hasattr(self, "_root_widget"):
            self._root_widget.update()
            for child in self._root_widget.findChildren(QWidget):
                child.update()

    @_profile_method("_set_welcome_mode")
    def _set_welcome_mode(self, welcome: bool):
        """ウェルカム画面 ↔ 碁盤画面の表示モードを切り替える。
        ウェルカム時は右カラム（情報パネル）も非表示にして、最初の選択
        （SGFを開く / 新規作成）に集中できるようにする。
        ウェルカム → 碁盤への遷移時はフェードインアニメで自然に切り替える。

        右パネルトグルボタン:
          ・ウェルカム時 → 非表示(押せない)
          ・碁盤時      → 表示。表示状態は _right_panel_collapsed に従う
            (前回の開閉状態を保持したまま遷移する)
        """
        from PyQt6.QtCore import QTimer
        # 切り替え方向を判定(現在ウェルカム表示中か?)
        was_welcome = (self._left_stack.currentIndex() == 1)
        is_transition_to_board = was_welcome and not welcome
        # ウェルカム → 碁盤遷移時は、setCurrentIndex で碁盤画面に切り替わる
        # 「前」に対象ウィジェットを opacity=0 にしておく。これにより、
        # 切替直後の 1 フレームで碁盤画面が完全表示されてからフェードイン
        # アニメで再び見えてくる、という「明滅」を防ぐ。
        if is_transition_to_board:
            self._prepare_welcome_to_board_fade()
        # 左カラム: ウェルカム=index 1、碁盤=index 0
        self._left_stack.setCurrentIndex(1 if welcome else 0)
        # ナビバー（碁盤の下のスライダー＋進む/戻る）
        self._navbar.setVisible(not welcome)
        # 右カラム（情報パネル）の表示制御
        # ウェルカム時: 強制非表示
        # 碁盤時:       _right_panel_collapsed に従う(前回状態を尊重)
        if hasattr(self, "_right_col"):
            if welcome:
                self._right_col.setVisible(False)
            else:
                collapsed = getattr(self, "_right_panel_collapsed", False)
                self._right_col.setVisible(not collapsed)
        # 右パネルトグルボタン: ウェルカム時は非アクティブ(レイアウト幅は維持)
        # setVisible(False) を使うとレイアウトが詰まって解析画面遷移時にボタン群が
        # 左へ流れる動きが目立つので、アイコン透明化 + クリック無効化で代替する。
        if hasattr(self, "_titlebar") and hasattr(self._titlebar, "set_panel_toggle_active"):
            self._titlebar.set_panel_toggle_active(not welcome)
        # スクリーンショット・保存・コピー: ウェルカム時は無効化
        if hasattr(self, "_ss_act"):
            self._ss_act.setEnabled(not welcome)
        if hasattr(self, "_save_act"):
            self._save_act.setEnabled(not welcome)
        if hasattr(self, "_copy_act"):
            self._copy_act.setEnabled(not welcome)
        # 表示モード変更後にパネル配置を再計算
        # setStyleSheet 後に Qt がレイアウトを再計算するため 1 フレーム遅らせる
        QTimer.singleShot(0, self._place_panels)
        # ウェルカム → 碁盤遷移時はフェードインアニメを実行
        if is_transition_to_board:
            # _place_panels が完了してから(レイアウトが確定してから)アニメ開始
            QTimer.singleShot(10, self._animate_welcome_to_board)

    def _prepare_welcome_to_board_fade(self):
        """ウェルカム → 碁盤遷移時のフェードイン事前準備。
        対象ウィジェット (碁盤コンテナ / ナビバー / 右パネル) に
        QGraphicsOpacityEffect を opacity=0 で適用する。

        この関数は _set_welcome_mode 内で setCurrentIndex(0) を呼ぶ
        「前」に呼び出される。これにより、QStackedWidget の表示が
        碁盤画面に切り替わった瞬間、対象ウィジェットは既に opacity=0
        の透明状態になっており、「一瞬完全表示されてからフェードイン
        が始まる」というチラつきが防げる。

        実際のアニメーション開始は _animate_welcome_to_board が
        QTimer.singleShot(10, ...) 経由で後から行う。両方の関数が
        同じ effect インスタンスに対して動作するよう、self に
        _welcome_fade_effects として保持する。

        備考: スタイル制約 (QGraphicsOpacityEffect 禁止) は通常 UI 用で、
        ウェルカム → 碁盤の一回限りの遷移演出は既に存在する例外的な
        使用 (windowOpacity ではウィンドウ全体になってしまうため)。
        既存の _animate_welcome_to_board と同じ方針を踏襲している。
        """
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        targets = []
        if hasattr(self, "_board_container"):
            targets.append(self._board_container)
        if hasattr(self, "_navbar"):
            targets.append(self._navbar)
        if hasattr(self, "_right_col"):
            targets.append(self._right_col)

        # 各ウィジェットに opacity=0 の effect を即時適用しておく。
        # アニメ開始時 (_animate_welcome_to_board) でこの effect を
        # そのまま使ってフェードイン実行する。
        effects = []
        for w in targets:
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
            effects.append((w, eff))
        # アニメ実行側に渡すための保持
        self._welcome_fade_effects = effects

    def _animate_welcome_to_board(self):
        """ウェルカム画面から碁盤画面への遷移アニメーション。
        碁盤(_board_container)、ナビバー、右パネルをまとめて opacity 0 → 1 で
        フェードインさせる。

        opacity effect の適用は事前に _prepare_welcome_to_board_fade で
        行われており、ここではアニメ起動と完了時クリーンアップだけを行う。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QParallelAnimationGroup

        # 事前準備で適用した effects を取得。なければ後方互換として
        # ここで作成する(直接呼ばれた場合の保険)。
        effects = getattr(self, "_welcome_fade_effects", None)
        if not effects:
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            targets = []
            if hasattr(self, "_board_container"):
                targets.append(self._board_container)
            if hasattr(self, "_navbar"):
                targets.append(self._navbar)
            if hasattr(self, "_right_col"):
                targets.append(self._right_col)
            if not targets:
                return
            effects = []
            for w in targets:
                eff = QGraphicsOpacityEffect(w)
                eff.setOpacity(0.0)
                w.setGraphicsEffect(eff)
                effects.append((w, eff))

        group = QParallelAnimationGroup()
        for _w, eff in effects:
            anim = QPropertyAnimation(eff, b"opacity")
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(anim)

        def _cleanup():
            # アニメ完了後に opacity effect を外す(描画パイプラインの常時負荷回避)
            for w, _eff in effects:
                try:
                    w.setGraphicsEffect(None)
                except Exception:
                    pass
            self._welcome_fade_effects = None

        group.finished.connect(_cleanup)
        self._welcome_to_board_anim = group  # GC対策
        group.start()

    def _load_demo(self):
        """起動時処理: ウェルカム画面を表示する（デモ棋譜は使用しない）。"""
        self._set_welcome_mode(True)
        self._status_bar.showMessage("SGF ファイルを開くか、新規作成してください")

    def _new_game(self, size: int = 19):
        """新規作成: 空の盤面を開いて解析モードへ遷移する。
        size は盤面サイズ（9/13/19路を想定）。デフォルトは19路。

        現在の棋譜に未保存の変更がある場合、ユーザーに確認ダイアログを出し、
        キャンセルが選ばれたら新規作成を中止する。
        """
        # 未保存の変更があれば確認(中止された場合は何もしない)
        if not self._confirm_discard_or_save():
            return
        # 確認通過後、編集中のコメントフラグをクリアする。
        # これをしないと、後の _goto_first → _save_comment_if_editing で
        # コメント欄に残っていた古いテキストが「新棋譜のルートノード」へ
        # 書き込まれてしまい、新棋譜の0手目にコメントが残る不具合になる。
        self._comment.document().setModified(False)
        sz = int(size) if size in (9, 13, 19) else 19
        # 既に碁盤を表示中なら、データ差し替え前にクロスフェード起動
        # (ウェルカム → 碁盤の初回遷移は別途 _animate_welcome_to_board が担当)
        was_welcome = (self._left_stack.currentIndex() == 1)
        if not was_welcome:
            self._board.start_content_change_anim()
        sgf = f"(;FF[4]GM[1]SZ[{sz}]KM[6.5]PB[Black]PW[White])"
        self._game = parse_sgf(sgf)
        self._info.get_graph().clear_data()
        self._build_states()
        self._goto_first()
        self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
        self._branch_tree.update_tree(self._game_state, self._node_analyses)
        # 碁盤へ切り替え
        self._set_welcome_mode(False)
        # 棋譜差し替え後の dirty / 保存先をリセット
        # (新規作成は保存先未確定なので _current_sgf_path は None)
        self._is_dirty = False
        self._current_sgf_path = None
        self._status_bar.showMessage(f"新規棋譜({sz}路)を作成しました")

    def _open_sgf_path(self, path: str):
        """指定パスの SGF 棋譜を開く。
        現在の棋譜に未保存の変更がある場合、ユーザーに確認ダイアログを出し、
        キャンセルが選ばれたら開く操作を中止する。
        """
        # 未保存の変更があれば確認(中止された場合は何もしない)
        if not self._confirm_discard_or_save():
            return
        # 確認通過後、編集中のコメントフラグをクリアする(_new_game と同様の理由)。
        self._comment.document().setModified(False)
        try:
            # 既に碁盤を表示中なら、データ差し替え前にクロスフェード起動
            was_welcome = (self._left_stack.currentIndex() == 1)
            if not was_welcome:
                self._board.start_content_change_anim()
            self._game = load_sgf(path)
            self._info.get_graph().clear_data()
            self._build_states()
            self._goto_first()
            self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            self._branch_tree.update_tree(self._game_state, self._node_analyses)
            # 碁盤へ切り替え
            self._set_welcome_mode(False)
            # 棋譜差し替え後の dirty / 保存先をリセット
            # (読込元のパスを保存先として記憶 → 次回保存はそのまま上書き)
            self._is_dirty = False
            self._current_sgf_path = path
            self._status_bar.showMessage(f"読み込み完了: {Path(path).name}")
        except Exception as e:
            self._status_bar.showMessage(f"読み込みエラー: {e}")

    def _open_sgf(self):
        # SGF ファイルのみに対応
        filters = "SGF Files (*.sgf);;All Files (*.*)"
        path,_=QFileDialog.getOpenFileName(self,"棋譜を開く","",filters)
        if not path: return
        self._open_sgf_path(path)

    def _save_sgf(self) -> bool:
        """棋譜を SGF 形式で保存する。

        戻り値:
          True  = 保存成功(dirty フラグも False にリセット)
          False = キャンセル / 失敗(呼び出し側は「続行を中止」してよい)

        既存パスがある場合は上書き保存。無い場合 (新規作成・貼付の直後など) は
        QFileDialog で名前を尋ねる。
        """
        if not self._game:
            return False
        path = self._current_sgf_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "SGF を保存", "", "SGF Files (*.sgf)"
            )
            if not path:
                return False  # ユーザーがキャンセル
        try:
            save_sgf(self._game, path)
        except Exception as e:
            self._status_bar.showMessage(f"保存エラー: {e}")
            return False
        # 成功: パスを記憶し、dirty 解除
        self._current_sgf_path = path
        self._is_dirty = False
        self._status_bar.showMessage(f"保存: {Path(path).name}")
        return True

    def _confirm_discard_or_save(self) -> bool:
        """棋譜に未保存の変更がある時、ユーザーに確認するヘルパー。

        戻り値:
          True  = 続行してよい (保存済み or 変更を破棄する選択)
          False = 続行を中止 (キャンセル or 保存失敗)

        呼び出し側 (closeEvent / 新規作成 / SGF を開く / 貼付) は、戻り値が
        False なら現在の操作を中止する。
        """
        # 棋譜が未読込、または dirty でない場合は確認不要
        if self._game is None:
            return True
        if not self._is_dirty:
            return True

        dlg = _UnsavedChangesDialog(self)
        dlg.exec()
        code = dlg.result_code()

        if code == _UnsavedChangesDialog.RESULT_SAVE:
            # 保存して続行: 保存に失敗 / キャンセルなら続行も中止
            return self._save_sgf()
        elif code == _UnsavedChangesDialog.RESULT_DISCARD:
            # 保存せず続行: 変更を破棄して続行
            return True
        else:
            # キャンセル: 操作を中止
            return False

    def _copy_sgf(self):
        """現在のゲーム内容を SGF 形式でクリップボードにコピーする。
        既存の save_sgf を一時ファイルに使い、その内容を読み込んでコピーする。
        """
        if not self._game:
            return
        tmp_path = None
        try:
            # delete=False で一時ファイル作成（Windowsの仕様で同時アクセス不可のため）
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".sgf", delete=False, encoding="utf-8"
            ) as tmp:
                tmp_path = Path(tmp.name)
            # 既存の save_sgf を流用
            save_sgf(self._game, str(tmp_path))
            # 内容を読み込み
            sgf_text = tmp_path.read_text(encoding="utf-8")
            # クリップボードへ
            QApplication.clipboard().setText(sgf_text)
            self._status_bar.showMessage("SGF をクリップボードにコピーしました")
        except Exception as e:
            self._status_bar.showMessage(f"コピーエラー: {e}")
        finally:
            # 一時ファイル削除
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _paste_sgf(self):
        """クリップボードのテキストを SGF 棋譜として読み込み、現在のゲームを置き換える。
        現在の棋譜に未保存の変更がある場合、ユーザーに確認ダイアログを出し、
        キャンセルが選ばれたら貼り付けを中止する。
        """
        text = QApplication.clipboard().text()
        if not text or not text.strip():
            self._status_bar.showMessage("クリップボードが空です")
            return
        # 未保存の変更があれば確認(中止された場合は何もしない)
        if not self._confirm_discard_or_save():
            return
        # 確認通過後、編集中のコメントフラグをクリアする(_new_game と同様の理由)。
        self._comment.document().setModified(False)
        try:
            # 既に碁盤を表示中なら、データ差し替え前にクロスフェード起動
            was_welcome = (self._left_stack.currentIndex() == 1)
            if not was_welcome:
                self._board.start_content_change_anim()
            self._game = parse_sgf(text)
            self._build_states()
            self._goto_first()
            self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            self._branch_tree.update_tree(self._game_state, self._node_analyses)
            # 碁盤へ切り替え
            self._set_welcome_mode(False)
            # 棋譜差し替え後の dirty / 保存先をリセット
            # (貼付は保存先未確定なので _current_sgf_path は None)
            self._is_dirty = False
            self._current_sgf_path = None
            self._status_bar.showMessage("クリップボードから棋譜を読み込みました")
        except Exception as e:
            self._status_bar.showMessage(f"貼り付けエラー: {e}")

    def _save_board_screenshot(self):
        """BoardWidget を PNG でファイル保存する。
        保存先のデフォルトはデスクトップ。
        """
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "盤面をスクリーンショットとして保存",
            desktop,
            "PNG画像 (*.png)",
        )
        if not path:
            return
        # ウェルカム画面では保存しない（index 1 = ウェルカム）
        if self._left_stack.currentIndex() == 1:
            self._status_bar.showMessage("棋譜を開いてからスクリーンショットを撮ってください")
            return
        # 拡張子が付いていない場合は補完
        if not path.lower().endswith(".png"):
            path += ".png"
        # BoardWidget をキャプチャし、碁盤（木材部分）のみ切り抜いて保存
        full = self._board.grab()
        # _bg() と同じ計算式で碁盤矩形を求める
        ox, oy = self._board._orig()
        c = self._board._cell()
        bw = c * (self._board.board_size - 1)
        m = c * 1.2 if self._board.show_coords else c * 0.6
        board_x = int(ox - m)
        board_y = int(oy - m)
        board_w = int(bw + 2 * m)
        board_rect = QRect(board_x, board_y, board_w, board_w)
        cropped = full.copy(board_rect)
        # 盤面が角丸描画になっているので、保存画像にも同じ角丸クリップを適用
        # （素直に矩形コピーすると四隅にウィンドウ背景が写り込んでしまう）
        pixmap = QPixmap(cropped.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        _p = QPainter(pixmap)
        _p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clip = QPainterPath()
        _clip.addRoundedRect(QRectF(0, 0, cropped.width(), cropped.height()), R_MD, R_MD)
        _p.setClipPath(_clip)
        _p.drawPixmap(0, 0, cropped)
        _p.end()
        ok = pixmap.save(path, "PNG")
        if ok:
            self._status_bar.showMessage(f"スクリーンショットを保存しました: {path}")
        else:
            self._status_bar.showMessage("スクリーンショットの保存に失敗しました")

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

    def _write_comment_to_node(self, node, text: str):
        """指定ノードへコメントを書き込み、内容変化があれば dirty フラグを立てる。

        コメント編集の経路は複数あり (オーバーレイのテキスト変更即時保存、
        オーバーレイ閉じる時、コメント欄フォーカスアウト時、ノード移動前の
        保存等)、それぞれが個別に node.comment = ... を実行していた。これら
        を一元化し、内容変化があった場合だけ dirty 反映するようにする。

        Kizuki の dirty 判定は「コメント編集のみ」に絞ってある。盤面クリックや
        ノード削除は分岐探索の一部であり、ユーザーが意図して「保存したい変更」
        ではないため、dirty にしない。コメント編集は明確な意図を持って書く
        ものなので、これだけは保護する。
        """
        if node is None:
            return
        old_comment = getattr(node, "comment", "") or ""
        node.comment = text
        if text != old_comment:
            self._is_dirty = True

    def _save_comment_if_editing(self, node=None):
        """コメント欄に未保存の入力があれば指定ノード（省略時は現在ノード）に保存する。"""
        if not self._game_state: return
        if not self._comment.document().isModified(): return
        target = node if node is not None else self._game_state.current_node
        new_comment = self._comment_textedit.toPlainText()
        # 内容変化があれば dirty フラグを立てる(_write_comment_to_node が判定)
        self._write_comment_to_node(target, new_comment)
        self._comment.document().setModified(False)

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

    def _animate_unified(self, scroll_area, target_x: int, target_y: int,
                          tree_widget, end_marker_xy):
        """スクロール位置と分岐ツリーのリング絶対座標を **1つの QVariantAnimation で**
        同時に駆動する。
        各フレームの valueChanged で:
          - hbar.setValue(...), vbar.setValue(...) でスクロール更新
          - tree_widget._cur_marker_xy = (...) でリング絶対座標更新
          - tree_widget.update() で再描画
        を順に行う。同フレーム内で両方が更新されるためタイミングずれは発生しない。
        進行中のアニメがあれば停止して、現在の値を起点に新しい目標値へ再開する
        (連続操作時にリングがズレない)。

        - target_x, target_y: スクロール目標値(クランプ前の値、内部でクランプする)
        - end_marker_xy: アニメ終了時のリング絶対座標(=新ノード絶対座標)
        """
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if scroll_area is None or tree_widget is None:
            return
        hbar = scroll_area.horizontalScrollBar()
        vbar = scroll_area.verticalScrollBar()
        # 目標値はクランプ
        tx = max(hbar.minimum(), min(int(target_x), hbar.maximum()))
        ty = max(vbar.minimum(), min(int(target_y), vbar.maximum()))

        # 既存のスクロールアニメ(同じ scroll_area)を停止 → 破棄
        if not hasattr(self, "_scroll_anims"):
            self._scroll_anims = {}
        key = id(scroll_area)
        prev = self._scroll_anims.get(key)
        if prev is not None:
            try:
                prev.valueChanged.disconnect()
            except Exception:
                pass
            try:
                prev.finished.disconnect()
            except Exception:
                pass
            try:
                prev.stop()
            except Exception:
                pass
            try:
                prev.deleteLater()
            except Exception:
                pass

        # 旧アニメの停止後に **現在の値** を起点にする(連続操作で
        # 旧アニメが進行中だった場合、 hbar.value() と _cur_marker_xy は
        # その途中値になっているので、 それを新アニメの起点とする)
        sx = hbar.value()
        sy = vbar.value()
        # リング絶対座標の起点は _cur_marker_xy
        cur_marker = tree_widget._cur_marker_xy
        if cur_marker is None:
            # リング非表示状態(ルート等): リング側はアニメせず終点を確定
            cur_marker = end_marker_xy
        sax, say = float(cur_marker[0]), float(cur_marker[1])
        eax, eay = float(end_marker_xy[0]), float(end_marker_xy[1])

        # スクロールが動かず、 リングも動かないなら何もしない
        if sx == tx and sy == ty and sax == eax and say == eay:
            return

        anim = QVariantAnimation(self)
        anim.setDuration(self._SCROLL_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        # クロージャでキャプチャ
        def _on_changed(t, _hbar=hbar, _vbar=vbar,
                        _sx=sx, _sy=sy, _tx=tx, _ty=ty,
                        _sax=sax, _say=say, _eax=eax, _eay=eay,
                        _tree=tree_widget):
            try:
                t = float(t)
            except (TypeError, ValueError):
                return
            try:
                # ここで両方を **同じ valueChanged の中** で setValue する
                # ことで、スクロール位置とリング絶対座標は完全同フレーム同期。
                # 画面上のリング位置 = リング絶対 - スクロール = (sax + (eax-sax)*t)
                # - (sx + (tx-sx)*t)。 sax-sx と eax-tx が同じなら画面位置は不動。
                # 中央付近なら sax-sx == eax-tx == 200(中央)で常に固定。
                # 端付近なら sax-sx と eax-tx が異なるので画面位置が滑らかに変わる。
                _hbar.setValue(int(round(_sx + (_tx - _sx) * t)))
                _vbar.setValue(int(round(_sy + (_ty - _sy) * t)))
                _tree._cur_marker_xy = (
                    _sax + (_eax - _sax) * t,
                    _say + (_eay - _say) * t,
                )
                _tree.update()
            except RuntimeError:
                # スクロールエリアが破棄された場合は無視
                pass

        # 終了時にリング絶対座標を確定値で固定(浮動小数誤差を吸収)
        def _on_finished(_tree=tree_widget, _eax=eax, _eay=eay):
            try:
                _tree._cur_marker_xy = (_eax, _eay)
                _tree.update()
            except RuntimeError:
                pass

        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._scroll_anims[key] = anim
        anim.start()

    def _smooth_scroll_to(self, scroll_area, target_x: int, target_y: int):
        """QScrollArea の水平/垂直スクロールバーを (target_x, target_y) へ
        QVariantAnimation で滑らかに移動させる。アニメ時間 280ms / OutCubic。

        既存の進行中アニメがあれば停止して、現在のスクロール値を起点に
        新しい目標値へ再開する(=ユーザー操作と競合してもアニメ側は破綻しない)。
        ユーザー側のホイール/ドラッグ中はアニメと値が衝突する可能性があるが、
        着手時に呼ばれる用途では稀なケースとして許容する。
        """
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if scroll_area is None:
            return
        hbar = scroll_area.horizontalScrollBar()
        vbar = scroll_area.verticalScrollBar()
        # 目標値はスクロール範囲にクランプ
        tx = max(hbar.minimum(), min(int(target_x), hbar.maximum()))
        ty = max(vbar.minimum(), min(int(target_y), vbar.maximum()))

        # アニメ保持用辞書
        if not hasattr(self, "_scroll_anims"):
            self._scroll_anims = {}
        key = id(scroll_area)
        prev = self._scroll_anims.get(key)
        if prev is not None:
            try:
                prev.stop()
            except RuntimeError:
                pass

        sx = hbar.value()
        sy = vbar.value()
        if sx == tx and sy == ty:
            # 移動不要
            return

        anim = QVariantAnimation(self)
        anim.setDuration(self._SCROLL_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_changed(t, _hbar=hbar, _vbar=vbar,
                        _sx=sx, _sy=sy, _tx=tx, _ty=ty):
            try:
                t = float(t)
            except (TypeError, ValueError):
                return
            try:
                _hbar.setValue(int(round(_sx + (_tx - _sx) * t)))
                _vbar.setValue(int(round(_sy + (_ty - _sy) * t)))
            except RuntimeError:
                # スクロールエリアが破棄された場合は無視
                pass

        anim.valueChanged.connect(_on_changed)
        self._scroll_anims[key] = anim
        anim.start()

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

    def _on_node_comment_requested(self, node):
        """ノードの右クリックメニューからコメント追加/編集が選ばれた。
        該当ノードに移動してからコメントオーバーレイを開く。
        ノード移動の手順は _on_branch_node_clicked と同じ流れ
        (現在コメントの保存 → go_to_node → 盤面・グラフ更新)。"""
        if self._game_state and node is not self._game_state.current_node:
            self._save_comment_if_editing(self._game_state.current_node)
            self._game_state.go_to_node(node)
            self._refresh_board()
            self._update_graph()
        self._open_comment_overlay()

    def _toggle_comment_overlay(self):
        """ナビバーのコメントボタン: オーバーレイを開閉する。
        アニメ進行中は現在のアニメ方向と逆方向に切り替える。
        """
        # アニメ中: 進行方向で開閉判定
        kind = getattr(self, "_comment_anim_kind", None)
        if kind == "open":
            self._close_comment_overlay()
            return
        if kind == "close":
            self._open_comment_overlay()
            return
        # 通常時: 表示状態で判定
        if self._comment_overlay.isVisible():
            self._close_comment_overlay()
        else:
            self._open_comment_overlay()

    # コメントオーバーレイのアニメ用パラメータ
    _COMMENT_ANIM_DURATION = 250    # アニメ時間 (ms)
    _COMMENT_ANIM_SLIDE_PX = 16     # スライド距離 (下から上へ立ち上がる量)

    def _open_comment_overlay(self):
        """コメントオーバーレイを表示してフォーカスを当てる。
        スライドイン + opacity フェードのアニメで滑らかに表示する。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect, QApplication

        ov = self._comment_overlay
        # 既にアニメ中なら停止して状態をリセット
        self._cancel_comment_overlay_anim()

        # 最終ジオメトリ (=_place_panels が決めた正しい位置) を取得
        final_geom = ov.geometry()
        # 開始位置: 最終位置から SLIDE_PX 下にずらす(下から上へスライドイン)
        start_geom = QRect(final_geom.x(),
                           final_geom.y() + self._COMMENT_ANIM_SLIDE_PX,
                           final_geom.width(),
                           final_geom.height())

        # opacity effect をセット (完了時に解除する)
        eff = QGraphicsOpacityEffect(ov)
        eff.setOpacity(0.0)
        ov.setGraphicsEffect(eff)
        # ジオメトリは開始位置に置いてから show
        ov.setGeometry(start_geom)
        ov.show()
        ov.raise_()

        # アニメ: ジオメトリ (位置) と opacity の同時アニメ
        geom_anim = QPropertyAnimation(ov, b"geometry")
        geom_anim.setDuration(self._COMMENT_ANIM_DURATION)
        geom_anim.setStartValue(start_geom)
        geom_anim.setEndValue(final_geom)
        geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        op_anim = QPropertyAnimation(eff, b"opacity")
        op_anim.setDuration(self._COMMENT_ANIM_DURATION)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(geom_anim)
        group.addAnimation(op_anim)
        group.finished.connect(self._on_comment_overlay_open_done)
        self._comment_anim = group
        self._comment_anim_effect = eff
        self._comment_anim_running = True
        self._comment_anim_kind = "open"
        group.start()

        # アプリ全体のマウスクリックを監視してオーバーレイ外で閉じる
        QApplication.instance().installEventFilter(self)
        # フォーカスはアニメ完了後に当てる方が自然 (アニメ中にフォーカスを
        # 当てるとカーソルが点滅し始めて視覚的にうるさい)
        # → _on_comment_overlay_open_done で setFocus する

    def _on_comment_overlay_open_done(self):
        """開くアニメ完了: opacity effect を外してフォーカスを当てる。"""
        ov = getattr(self, "_comment_overlay", None)
        if ov is not None:
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
        self._comment_anim = None
        self._comment_anim_effect = None
        self._comment_anim_running = False
        self._comment_anim_kind = None
        # フォーカスをテキストエディットに
        if hasattr(self, "_comment_textedit"):
            self._comment_textedit.setFocus()

    def _cancel_comment_overlay_anim(self):
        """進行中のコメントオーバーレイアニメを破棄してリセットする。
        連打や開→閉の急な切替で呼ばれる。"""
        anim = getattr(self, "_comment_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
        ov = getattr(self, "_comment_overlay", None)
        if ov is not None:
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
        self._comment_anim = None
        self._comment_anim_effect = None
        self._comment_anim_running = False
        self._comment_anim_kind = None

    def _prewarm_comment_overlay(self):
        """コメントオーバーレイの初回 show() に約300ms かかる問題を緩和する。
        起動直後にユーザーから見えない位置で一度 show()/hide() を実行し、
        Qt 内部のスタイル解決・paint キャッシュ・フォントメトリクス計算等を
        済ませておく。これにより実際にユーザーがコメントボタンを押した時の
        初回表示も 2回目以降と同じ速度(<1ms)で開く。
        """
        if not hasattr(self, "_comment_overlay"):
            return
        ov = self._comment_overlay
        # 元の geometry を保存(_place_panels 後で正しい位置にあるはず)
        orig_geom = ov.geometry()
        # 画面外の負座標へ移動 → 物理的に見えない
        ov.setGeometry(-10000, -10000, max(100, orig_geom.width()),
                       max(100, orig_geom.height()))
        ov.show()
        # paint 系の初期化を確実に走らせるためイベントを処理
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        ov.hide()
        # eventFilter の installEventFilter は _open_comment_overlay 側でやるので
        # ここでは入れない(プリウォーム後にユーザーがボタンを押すまでは
        # オーバーレイ外クリック監視を起動しない)
        # geometry を元に戻す(次の _place_panels で再計算されるが念のため)
        ov.setGeometry(orig_geom)

    def _app_event_filter_active(self):
        return (hasattr(self, "_comment_overlay")
                and self._comment_overlay.isVisible())

    def _is_inside_comment_overlay(self, obj) -> bool:
        """obj がコメントオーバーレイ自身またはその子孫かを判定。
        オーバーレイ表示中の入力遮断で、オーバーレイ内部の操作だけを通すために使う。
        以下2方式を OR で組み合わせて堅牢化:
          (1) QWidget なら isAncestorOf で素直に判定
          (2) QObject.parent() チェーンを最大64階層辿る
              ※ QTextEdit の viewport 等、内部の特殊な子では (1) が
                False を返すケースがあるため、(2) を保険として併用する。
        """
        ov = getattr(self, "_comment_overlay", None)
        if ov is None or obj is None:
            return False
        # (1) QWidget は isAncestorOf で直接判定
        if isinstance(obj, QWidget):
            if obj is ov or ov.isAncestorOf(obj):
                return True
        # (2) QObject.parent() チェーンで保険判定
        node = obj
        for _ in range(64):
            if node is ov:
                return True
            try:
                parent = node.parent()
            except Exception:
                return False
            if parent is None:
                return False
            node = parent
        return False

    def mousePressEvent(self, ev):
        super().mousePressEvent(ev)

    def _load_comment_to_overlay(self, text: str):
        """手が切り替わったときにオーバーレイのテキストを更新する。
        オーバーレイが開いていれば内容を更新、閉じていればそのまま。"""
        self._comment_textedit.blockSignals(True)
        self._comment_textedit.setPlainText(text or "")
        self._comment_textedit.blockSignals(False)

    def _close_comment_overlay(self):
        """コメントオーバーレイを閉じて現在ノードに保存。
        スライドアウト + opacity フェードのアニメで滑らかに閉じる。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect, QApplication

        # 内容の保存は閉じ始めの時点で行う(アニメ中にノードが変わる可能性に備える)
        # _write_comment_to_node を経由することで dirty フラグも適切に立つ。
        if self._game_state:
            node = self._game_state.current_node
            if node:
                self._write_comment_to_node(node, self._comment_textedit.toPlainText())

        # eventFilter は即座に外す(アニメ中の外側クリックで再度 close が
        # 呼ばれて重複しないように)
        QApplication.instance().removeEventFilter(self)

        ov = self._comment_overlay
        if not ov.isVisible():
            return

        # 進行中のアニメ(開く中の可能性)を破棄してから閉じるアニメを開始
        self._cancel_comment_overlay_anim()

        cur_geom = ov.geometry()
        end_geom = QRect(cur_geom.x(),
                         cur_geom.y() + self._COMMENT_ANIM_SLIDE_PX,
                         cur_geom.width(),
                         cur_geom.height())

        eff = QGraphicsOpacityEffect(ov)
        eff.setOpacity(1.0)
        ov.setGraphicsEffect(eff)

        geom_anim = QPropertyAnimation(ov, b"geometry")
        geom_anim.setDuration(self._COMMENT_ANIM_DURATION)
        geom_anim.setStartValue(cur_geom)
        geom_anim.setEndValue(end_geom)
        geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        op_anim = QPropertyAnimation(eff, b"opacity")
        op_anim.setDuration(self._COMMENT_ANIM_DURATION)
        op_anim.setStartValue(1.0)
        op_anim.setEndValue(0.0)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(geom_anim)
        group.addAnimation(op_anim)
        group.finished.connect(self._on_comment_overlay_close_done)
        self._comment_anim = group
        self._comment_anim_effect = eff
        self._comment_anim_running = True
        self._comment_anim_kind = "close"
        group.start()

    def _on_comment_overlay_close_done(self):
        """閉じるアニメ完了: hide() + opacity effect 解除 + ジオメトリ復元。"""
        ov = getattr(self, "_comment_overlay", None)
        if ov is not None:
            ov.hide()
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
        self._comment_anim = None
        self._comment_anim_effect = None
        self._comment_anim_running = False
        self._comment_anim_kind = None
        # ジオメトリを正規位置に戻す: _place_panels が hidden 状態でも
        # 正しい位置を再計算してくれる(_comment_anim_running = False になった
        # 後に呼ぶことで上書きスキップを回避)
        self._place_panels()

    def _on_comment_overlay_changed(self):
        """テキスト変更時に即時保存。
        コメントオーバーレイ内のテキスト変更で毎キーストローク呼ばれる主経路。
        _write_comment_to_node 経由で内容変化があれば dirty フラグも立てる。
        """
        if self._game_state:
            node = self._game_state.current_node
            if node:
                self._write_comment_to_node(node, self._comment_textedit.toPlainText())

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

    def _on_rank_action(self, rank_val: int):
        """棋力メニューのアクション選択時。
        棋力変更後は解析キャッシュをクリアし、再解析を促す。
        """
        set_player_rank(rank_val)
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("player_rank", rank_val)
        # 棋力リスト各行の選択中フラグを更新 (UserRole+1)。
        # 描画は _RankItemDelegate が UserRole+1 を見て行うので、
        # データ更新後に viewport().update() で再描画をトリガする。
        lw = getattr(self, "_rank_list_widget", None)
        if lw is not None:
            for i in range(lw.count()):
                it = lw.item(i)
                if it is None:
                    continue
                v = it.data(Qt.ItemDataRole.UserRole)
                is_checked = (v == rank_val)
                it.setData(Qt.ItemDataRole.UserRole + 1, is_checked)
            lw.viewport().update()
        # 棋力閾値が変わるので解析結果のキャッシュを破棄し、現在局面を
        # 再解析できるよう再描画 + 必要ならポンダリング再開
        self._node_analyses = {}
        if self._game_state:
            self._refresh_board()
            self._update_graph()
        if self._ai_enabled:
            self._start_pondering_current()
        # 棋力閾値が変わるので解析結果のキャッシュを破棄し、現在局面を
        # 再解析できるよう再描画 + 必要ならポンダリング再開
        self._node_analyses = {}
        if self._game_state:
            self._refresh_board()
            self._update_graph()
        if self._ai_enabled:
            self._start_pondering_current()

    def _on_rank_menu_about_to_show(self):
        """棋力メニュー表示直前のフック。
        埋め込んだ QListWidget の高さを MainWindow に合わせて制限し、
        画面外はみ出しを防ぐ。同時に選択中項目を可視位置までスクロールする。

        実装メモ:
          ・QMenu の組み込みスクロール矢印は WA_TranslucentBackground +
            FramelessWindowHint 環境で描画されない (Qt のスクロール矢印
            描画パスが半透明背景と相性が悪い)。そのため棋力メニューだけは
            QWidgetAction + QListWidget でスクロール可能なリストを埋め込む
            方式を採用している(コミメニューの「その他」インラインウィジェット
            と同じ哲学)。
          ・上下余白: MainWindow 上下に各 32px の余白を残し、リストが
            アプリ枠の中に視覚的に収まるようにする。
          ・最小高: 計算結果が極端に小さくなった場合の保険として最低 200px
            は確保する(項目 5〜6 個分)。
          ・ウィンドウリサイズ後の初回表示で古い高さが反映される問題への
            対策:
              1. processEvents() で保留中のリサイズイベントを反映させる。
              2. _on_rank_menu_about_to_hide でメニュー/リストの固定サイズ
                 を解除しておき、次回 aboutToShow で確実にゼロから再計算
                 させる(Qt の QMenu 内部 sizeHint キャッシュ回避)。
              3. setFixedHeight 後に親 QMenu.adjustSize() で実サイズ強制
                 再計算。
        """
        lw = getattr(self, "_rank_list_widget", None)
        if lw is None:
            return

        # 保留中のリサイズイベントを処理してから現在の高さを取得する。
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # MainWindow 高さに対する上下余白 (px) と最小高
        MARGIN = 32
        MIN_H = 200
        win_h = max(self.height(), MIN_H + MARGIN * 2)
        cap_h = max(MIN_H, win_h - MARGIN * 2)

        # 項目を全部表示した時の自然な高さ
        # = 行高 × 項目数 + 上下フレーム余白
        row_h = lw.sizeHintForRow(0) if lw.count() > 0 else 28
        natural_h = row_h * lw.count() + 2 * lw.frameWidth() + 4
        target_h = min(natural_h, cap_h)
        lw.setFixedHeight(target_h)
        lw.updateGeometry()

        # 親 QMenu (棋力サブメニュー) のジオメトリを強制再計算。
        rm = getattr(self, "_rank_menu", None)
        if rm is not None:
            # Qt が古い sizeHint を保持していることがあるため、
            # 一度メニュー本体の固定サイズも明示的に上書きする。
            # adjustSize() は子の sizeHint を聞いて自分のサイズを決め直す。
            rm.setFixedHeight(target_h + 8)  # +8 は QMenu の上下 padding 分
            rm.adjustSize()

        # 選択中項目を可視位置へ自動スクロール
        from PyQt6.QtWidgets import QAbstractItemView
        for i in range(lw.count()):
            item = lw.item(i)
            if item is not None and item.data(Qt.ItemDataRole.UserRole + 1):
                lw.setCurrentItem(item)
                lw.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
                break

    def _on_rank_menu_about_to_hide(self):
        """棋力メニューが閉じる直前に、固定サイズ制約を解除する。
        次回 aboutToShow で「現在の」MainWindow サイズに基づき、ゼロから
        確実に再計算できるようにするため。Qt の QMenu は内部に sizeHint
        キャッシュを持っており、setFixedHeight した値をそのまま再利用する
        ことがある。これがウィンドウリサイズ後の初回表示で古いサイズが
        残る原因と推定。
        """
        lw = getattr(self, "_rank_list_widget", None)
        if lw is not None:
            # setFixedHeight で固定された min/max を解除して、自由に
            # サイズを取れる状態に戻す。
            lw.setMinimumHeight(0)
            lw.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
        rm = getattr(self, "_rank_menu", None)
        if rm is not None:
            rm.setMinimumHeight(0)
            rm.setMaximumHeight(16777215)

    def _on_ai_toggle(self, enabled: bool):
        """AIリアルタイム解析のON/OFFを切り替える。
        形勢判断が ON の場合は、AI 解析を OFF にしても ownership 取得用に
        ポンダリングは継続させる。
        """
        self._ai_enabled = enabled
        # BoardWidget の hints フェード判定で参照するため同期
        if hasattr(self, "_board"):
            self._board.ai_enabled = enabled
        # 状態を永続化（再起動時に復元）
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("analysis_enabled", enabled)
        if enabled:
            self._start_pondering_current()
            self._status_bar.showMessage("AI解析 ON")
        else:
            # 形勢判断 ON なら ownership 取得用にポンダリングは継続
            if not self._ownership_enabled:
                self._engine.stop_pondering()
            self._status_bar.showMessage("AI解析 OFF")
            # AI 解析系の盤面情報（候補手・着手評価）をクリアして現局面を再描画
            self._refresh_board()

    def _on_ownership_toggle(self, enabled: bool):
        """形勢判断オーバーレイのON/OFFを切り替える。
        AI 解析が OFF でも、形勢判断 ON なら ownership 取得用にポンダリングを走らせる。
        """
        self._ownership_enabled = enabled
        # 状態を永続化（再起動時に復元）
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("ownership_enabled", enabled)
        self._board.show_ownership = enabled
        if enabled:
            # 形勢判断 ON: ポンダリングを再起動する。
            # 以前は ownership を常時要求していたため「既存 ponder から自然に
            # 来るのを待つ」だけで済んでいたが、現在は includeOwnership を
            # _ownership_enabled に応じて条件的に送るため、 OFF→ON 切替時には
            # includeOwnership=True の新クエリを送り直す必要がある。
            # AI 解析 OFF でポンダリング未起動の場合も、ここで開始する。
            self._start_pondering_current()
        else:
            # 形勢判断 OFF: 表示中の ownership をクリアして盤面を再描画
            self._board.ownership = []
            # AI 解析も OFF ならポンダリング停止
            if not self._ai_enabled:
                self._engine.stop_pondering()
        self._board.update()

    def _on_move_numbers_toggled(self, enabled: bool):
        """手順番号オーバーレイのON/OFFを切り替える（旧「表示」メニュー版を移設）。
        ON にしたとき起点未指定なら、ルートから全手順を表示する。
        """
        self._move_numbers_enabled = enabled
        # 状態を永続化（再起動時に復元）
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("move_numbers_enabled", enabled)
        if enabled and self._move_number_anchor is None:
            self._move_number_anchor = 0  # デフォルトはルートから全手順
        self._refresh_board()

    @_profile_method("_start_pondering_current")
    def _start_pondering_current(self):
        """現在局面のポンダリングを開始。
        AI 解析 または 形勢判断 のいずれかが ON なら走らせる
        （形勢判断のみ ON の場合は ownership 取得用）。
        加えて、直前手が未解析なら補助解析を発動して悪手判定の前手データを得る。
        """
        if not (self._ai_enabled or self._ownership_enabled): return
        if not self._game_state: return
        if not self._engine.is_running(): return

        node = self._game_state.current_node
        self._pondering_node = node
        # ── 案D: 各 ponder セッションの初回中間結果を判定するためのフラグ ──
        # ここでリセットすることで、 ノード切替/着手追加/分岐切替などで
        # _start_pondering_current が再度呼ばれるたびに「初回」が再定義される。
        # _on_ponder_result の冒頭スロットルで参照する。
        self._first_intermediate_received = False
        with _profile("_start_pondering_current.build_moves"):
            moves = self._build_moves_to_node(node)

        def _callback(result):
            # KataGo の受信スレッドから呼ばれるのでシグナル経由でUIスレッドへ渡す
            self._ponder_result_signal.emit(result, node)

        with _profile("_start_pondering_current.engine_call"):
            # ── GPU 負荷削減: 形勢判断 ON のときだけ ownership を要求 ─────
            # KataGo の includeOwnership=True はメモリ使用量を約2倍にし
            # GPU 負荷も上がる(公式 Analysis_Engine.md 参照)ため、
            # 形勢判断オーバーレイ表示中(_ownership_enabled)のときだけ True にする。
            # 形勢判断 OFF → ON 切替時は _on_ownership_toggle が
            # _start_pondering_current() を呼び直すため、ここで True に
            # 切り替わったクエリが送られる(古いクエリは terminate_all で破棄)。
            self._engine.start_pondering(
                moves=moves,
                on_result=_callback,
                include_ownership=self._ownership_enabled,
            )

    @_profile_method("_on_ponder_result")
    def _on_ponder_result(self, result, node):
        """ポンダリング途中結果をUIスレッドで受け取る。"""
        if not self._game_state: return
        # 既に別の局面に移動していたら捨てる
        if node is not self._game_state.current_node: return

        # ── 中間結果の全体スロットリング ──
        # 中間結果(is_during_search=True)は 0.1 秒ごとに来るが、本メソッド全体の
        # コスト(勝率バー・MoveInfoCard・盤面再描画・ownership 361 セル更新等)を
        # 毎回走らせるのは重い。特に勝率バーと MetricLabel は値変更のたびに 300ms
        # の QVariantAnimation を再起動するため、0.1 秒ごとに呼ぶとアニメが常時
        # 進行中=60fps で update() が発火し続ける状態になる。
        # → 中間結果は _ponder_full_throttle_ms に1回までに間引く。
        # 最終結果(is_during_search=False)は必ず処理する(解析完了時の確定値表示)。
        # ただし「ノード切替直後の初回中間結果」だけは例外でスロットル免除する:
        # ホイール/キー操作で未解析の手に進んだ直後、 _refresh_board は ma=None
        # のため空 candidates で set_position して候補手が一旦消える。 ここで
        # スロットルが効いていると、 ponder の最初の中間結果 (~100ms 後) が
        # 早期 return されることがあり、 _ponder_full_throttle_ms 経過後の
        # 次の中間結果まで候補手が表示されない(=ユーザー証言のワンテンポ遅れ)。
        # 「初回中間結果」判定は _first_intermediate_received フラグで行う。
        # これは _start_pondering_current 呼び出し時に False にリセットされ、
        # 各 ponder セッションの最初の中間結果でのみ True に切り替わる。
        # 過去実装では _last_ponder_node_id で判定していたが、 これは最終結果側で
        # 先回り更新されてしまい、 中間結果の初回免除が一度も発火しなかった。
        is_first_intermediate = (
            result.is_during_search
            and not getattr(self, "_first_intermediate_received", False)
        )
        if result.is_during_search:
            if is_first_intermediate:
                # この ponder セッションの初回中間結果: 即時通過 + フラグ更新
                self._first_intermediate_received = True
                self._ponder_full_last_t = time.monotonic()
            else:
                now_full = time.monotonic()
                if (now_full - self._ponder_full_last_t) * 1000.0 < self._ponder_full_throttle_ms:
                    return
                self._ponder_full_last_t = now_full
        else:
            # 最終結果でもタイムスタンプは更新しておく(直後の中間結果が来た場合の
            # 二重実行を避ける)。
            self._ponder_full_last_t = time.monotonic()

        # ── AI 解析 OFF かつ 形勢判断 ON の場合: ownership だけ反映して終了 ──
        # AI 解析 OFF の意図を尊重し、候補手・勝率・MoveInfoCard・グラフ・解析キャッシュは
        # 一切更新しない。ownership オーバーレイのみ KataGo から取り込む。
        if not self._ai_enabled:
            if self._ownership_enabled and result.ownership:
                self._board.ownership = result.ownership
                self._board.update()
            return

        from core.analyzer import MoveAnalysis
        blunder = None

        color = node.move_color or self._game_state.turn
        coord = node.get(color, "") if node.move_color else ""
        human = sgf_coord_to_human(coord, self._game.board_size) if coord else "—"

        # ルート全体の visits（maxVisits の対象）を表示。best_moves[0].visits は
        # 1番候補手のみの visits なので、上限値と直接対応しない。
        visits = result.root_visits

        # ponder 結果で blunder の before/after を更新する。
        # after: ポンダリング（高visits）の root_win_rate / root_score_lead を使用
        # before: 直前ノード（parent）の解析結果のみを使用する（案B）
        #   → 直前ノードが未解析の場合は blunder を計算しない（異常値防止）
        if node.move_color:
            prev_node = node.parent
            prev_ma = self._node_analyses.get(id(prev_node)) if prev_node else None

            # after 値（ポンダリングの高品質値・黒視点→color視点変換）
            wr_after_black = result.root_win_rate
            sl_after_black = result.root_score_lead
            if color == "B":
                wr_after = wr_after_black
                sl_after = sl_after_black
            else:
                wr_after = 1.0 - wr_after_black
                sl_after = -sl_after_black

            # before 値: 直前ノードが解析済みの場合のみ計算する
            if prev_ma is not None:
                if color == "B":
                    wr_before = prev_ma.win_rate
                    sl_before = prev_ma.score_lead
                else:
                    wr_before = 1.0 - prev_ma.win_rate
                    sl_before = -prev_ma.score_lead
                wr_loss = max(0.0, wr_before - wr_after)
                sl_loss = max(0.0, sl_before - sl_after)
                best_move_str = prev_ma.best_moves[0].move if prev_ma.best_moves else ""
                best_move_wr  = prev_ma.best_moves[0].win_rate if prev_ma.best_moves else wr_before
                blunder = BlunderInfo(
                    win_rate_before=wr_before,
                    win_rate_after=wr_after,
                    win_rate_loss=wr_loss,
                    best_move=best_move_str,
                    best_move_wr=best_move_wr,
                    score_lead_before=sl_before,
                    score_lead_after=sl_after,
                    score_lead_loss=sl_loss,
                )
                blunder.played_move = human
            # 直前ノードが未解析の場合は blunder = None のまま（「—」表示）

        # move_number: ルートからノードまでの着手数（ルートノード自身は0）
        # node.move_color がある場合のみカウント対象。
        # 最適化: 過去に解析済みなら id(node) からキャッシュ、または親ノードの
        # move_number から差分計算する。これにより手数 N の親辿りループを回避。
        old_ma = self._node_analyses.get(id(node))
        if old_ma is not None:
            # 同じノードを再ポンダリング: move_number はノード固有なので流用
            _move_number = old_ma.move_number
        else:
            parent_node = node.parent
            parent_ma = self._node_analyses.get(id(parent_node)) if parent_node else None
            if parent_ma is not None:
                # 親が解析済み: 親の move_number に +1 (move_color あり) or +0
                _move_number = parent_ma.move_number + (1 if node.move_color else 0)
            else:
                # フォールバック: 親辿りで実数えする(初回のみのコスト)
                _path_count = 0
                _n = node
                while _n:
                    if _n.move_color:
                        _path_count += 1
                    _n = _n.parent
                _move_number = _path_count

        # win_rate: 常に黒視点で格納（勝率バー・グラフは黒視点を前提とする）
        # KataGo analysis モードの root_win_rate は常に黒視点で返る
        ma = MoveAnalysis(
            move_number=_move_number,
            color=color,
            coord=coord,
            human_coord=human,
            win_rate=result.root_win_rate,
            score_lead=result.root_score_lead,
            blunder=blunder,
            best_moves=result.best_moves,
            analysis_result=result,
            sgf_comment=node.comment,
        )
        # ── 既存解析の visits 劣化防止ガード ──
        # ホイール/キーボードで既解析の手に進むと、 _refresh_board で
        # 既存の高 visits の ma を使った set_position で候補手が即時表示される。
        # その直後に再 ponder が始まり、 ~100ms で最初の中間結果が来るが、
        # この時点では visits が低く(50〜200程度)、 上位候補手の精度が悪い。
        # この低 visits の ma で既解析データを上書きすると、 候補手の構成や
        # パーセント表示が一瞬「劣化した内容」に切り替わって見える(=ユーザー
        # 証言の「ワンテンポ遅れて出る」現象)。
        # → 既存の ma の visits >= 新しい visits なら、 既存を維持して
        #    set_position 等の後続処理にも既存の ma を渡す。
        old_ma2 = self._node_analyses.get(id(node))
        old_visits = (old_ma2.analysis_result.root_visits
                      if old_ma2 and old_ma2.analysis_result else 0)
        new_visits = result.root_visits
        if old_ma2 is not None and old_visits >= new_visits:
            # 既存の方が高 visits: ローカル変数も既存に差し替えて後続処理へ
            ma = old_ma2
        else:
            # 新しい方が高 visits: 通常通り保存
            self._node_analyses[id(node)] = ma

        # ── 値変化シグネチャ判定 ──
        # ポンダリング中は visits が増え続けて毎回ここまで来るが、上位の
        # 「実質的な値」(候補手の構成・勝率・目差)はある visits 数を超えると
        # ほとんど動かない。そのときに UI を毎回再描画するのは無駄なので、
        # シグネチャベースで「値の実質変化があった部分だけ」更新する。
        # シグネチャは粗い粒度(visits は 500 単位、勝率は 1% 単位、
        # score_lead は 0.5 目単位)にして、微小揺れでは更新が起きないように。
        # 同一ノード(node)内でのみ意味のある比較なので、ノードIDが変われば
        # 強制的に全更新(_last_ponder_sig_* をクリア)する。

        # 候補手シグネチャ(盤面候補手リング + next_moves)
        # 上位8手の (move, visits//500 単位, score_lead 0.5 目単位) のタプル。
        # blunder の有無/カテゴリも含める(blunder バッジが変わるなら更新が必要)。
        cand_sig = tuple(
            (mi.move, mi.visits // 500, round(mi.score_lead * 2) / 2.0)
            for mi in result.best_moves[:8]
        )
        # next_moves はノード構造由来なので id(node) で代替できる
        blunder_cat = blunder.category if blunder else None
        board_sig = (id(node), cand_sig, blunder_cat)

        # 解析カード/勝率シグネチャ(MoveInfoCard + ScoreBoard 勝率)
        # 勝率は 1% 単位、目差は 0.5 目単位、blunder カテゴリ。
        analysis_sig = (
            id(node),
            round(result.root_win_rate, 2),
            round(result.root_score_lead * 2) / 2.0,
            blunder_cat,
        )

        # 重い処理(グラフ + 分岐ツリー)用シグネチャ
        # 解析済みノード総数 + 現在ノードの ma の代表値。
        # ノード総数が増えるか現在 ma の値が変わったときだけ走らせる。
        heavy_sig = (
            id(node),
            len(self._node_analyses),
            round(result.root_score_lead * 2) / 2.0,
            blunder_cat,
        )

        # ノードが変わったら必ず全更新(シグネチャキャッシュをクリア)
        if getattr(self, "_last_ponder_node_id", None) != id(node):
            self._last_ponder_node_id = id(node)
            self._last_ponder_sig_board = None
            self._last_ponder_sig_analysis = None
            self._last_ponder_sig_heavy = None

        # 最終結果(is_during_search=False)はシグネチャに関わらず必ず全更新する
        # (確定値表示のため。確定値とアニメ起動でユーザーの注意を引きたい)。
        is_final = not result.is_during_search
        force_all = is_final

        # 候補手・盤面の更新(set_position は ownership も毎回渡している)
        # ownership 自体の差分比較はコストが高い(361セル)ので、ここでは
        # board_sig の他要素で代替判定する。ownership は scrolling 系の
        # アニメではないため、 1〜2 フレーム遅れても見た目の影響は小さい。
        if force_all or board_sig != self._last_ponder_sig_board:
            self._last_ponder_sig_board = board_sig
            # 候補手・勝率のみ更新（_refresh_board は重いので直接更新）
            next_moves = [(c.move[0], c.move[1], c.move_color, i == 0)
                          for i, c in enumerate(node.children) if c.move_color and c.move]
            self._board.set_position(
                self._game_state.stones, ma.best_moves, node.move, ma.blunder, next_moves,
                ownership=result.ownership if result.ownership else None,
                turn=self._game_state.turn)

        # 解析カード(MoveInfoCard) + 勝率バー更新
        if force_all or analysis_sig != self._last_ponder_sig_analysis:
            self._last_ponder_sig_analysis = analysis_sig
            self._info.update_analysis(ma, node.comment)
            # 手の情報カードをリアルタイム更新
            self._move_card.update_card(ma, ma.color)

        # コメントは(コメントオーバーレイが閉じている時)解析結果には依存
        # しないが、ノード切替時には更新したい。シグネチャ判定とは独立して
        # 「ノード切替直後(=force_all 時)のみ」更新する。
        if force_all and not self._comment_overlay.isVisible():
            self._load_comment_to_overlay(node.comment)
        # アゲハマも変化なし、毎回呼んでも軽いのでそのまま
        # アゲハマ: GameState._black_captures = 白のアゲハマ、_white_captures = 黒のアゲハマ
        self._info.update_captures(
            self._game_state._white_captures, self._game_state._black_captures)

        turn_str = "黒番" if self._game_state.turn == "B" else "白番"
        self._status_bar.showMessage(
            f"解析中  {turn_str}  勝率 {result.root_win_rate*100:.1f}%"
            f"  visits {visits}"
        )

        # ── 重い処理は中間結果ではスロットリング + シグネチャチェック ──
        # _update_graph() と _branch_tree.update_tree() はどちらも手数 N に比例する
        # コストがかかる(O(N) のレイアウトと再描画)。reportDuringSearchEvery=0.1 で
        # 0.1 秒ごとに本コールバックが呼ばれるため、後半手数(200手目以降など)では
        # これが連続で走り続けて UI が重くなる。
        # ・最終結果(is_during_search=False)は必ず走らせる(評価バッジ・グラフが確実に確定)
        # ・中間結果は前回実行から _ponder_heavy_throttle_ms 経過していれば走らせる
        # ・ただしシグネチャが前回と同じならスキップ(同じ評価でも再描画コストを払うのは無駄)
        # ・ユーザー操作起点の update_tree 呼び出しは別経路から行われており、
        #   このスロットリングの対象外(新ノード出現アニメや現在ノード移動アニメ
        #   は影響を受けない)。
        now_t = time.monotonic()
        time_ok = is_final or (
            (now_t - self._ponder_heavy_last_t) * 1000.0 >= self._ponder_heavy_throttle_ms
        )
        sig_changed = force_all or heavy_sig != self._last_ponder_sig_heavy
        if time_ok and sig_changed:
            self._ponder_heavy_last_t = now_t
            self._last_ponder_sig_heavy = heavy_sig
            # グラフをリアルタイム更新（解析済みノードの目差を反映）
            self._update_graph()
            # 分岐ツリーをリアルタイム更新（評価バッジを即時反映）
            self._branch_tree.update_tree(self._game_state, self._node_analyses)

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

    def _invalidate_graph_struct_cache(self):
        """_update_graph() の構造キャッシュを無効化する。

        ノード移動、ノード追加・削除など、棋譜のツリー構造または現在ノードに
        変化があった場合に呼ぶ。次回の _update_graph() でキャッシュを再構築する。
        """
        self._graph_struct_cache = None

    @_profile_method("_update_graph")
    def _update_graph(self):
        """解析済みノードの目差グラフを更新する。
        一度描画したデータは前の手に戻っても消えないよう、
        現在のパス上の全解析済みノードを常にプロットする。

        構造キャッシュ:
        path_to_root() / main_line() / on_main_line 判定 / graph_total 計算は
        ノード構造と現在ノードに依存するが、_node_analyses の値変化(ポンダリング
        中の score_lead/blunder 更新)では変わらない。よってこれらは構造キャッシュ
        として保持し、ポンダリング中の繰り返し呼び出しでは再計算しない。
        ノード移動・追加・削除時は _invalidate_graph_struct_cache() でキャッシュを
        破棄する。
        """
        if not self._game or not self._game_state: return

        cur_node = self._game_state.current_node

        # ── 構造キャッシュの再利用 or 再構築 ──
        cache_key = (id(cur_node), id(self._game.root))
        cache = self._graph_struct_cache
        if cache is not None and cache[0] == cache_key:
            # キャッシュヒット: 構造系の派生量を流用
            _, move_nodes_in_path, main_move_nodes, on_main_line, graph_total = cache
            total_main = len(main_move_nodes)
        else:
            # キャッシュミス: path/main_line を再構築
            path_nodes = self._game_state.path_to_root()  # ルート→現在ノードの順
            move_nodes_in_path = [n for n in path_nodes if n.move_color]

            # メインライン全体も取得（手戻り後も先の解析済みデータを保持するため）
            main_line = self._game.main_line()
            main_move_nodes = [n for n in main_line if n.move_color]
            total_main = len(main_move_nodes)

            # graph_total: メインライン上なら total_main、サブ分岐上なら
            # 「サブ分岐の真の末尾までの手数」(現在ノードから主分岐の子を末端まで
            # 辿った長さ)。スライダーの maximum と整合する。
            on_main_line = cur_node in main_line
            if on_main_line:
                graph_total = total_main
            else:
                tail = cur_node
                while tail.children:
                    tail = tail.children[0]
                t = tail
                graph_total = 0
                while t.parent is not None:
                    if t.move_color:
                        graph_total += 1
                    t = t.parent

            self._graph_struct_cache = (
                cache_key, move_nodes_in_path, main_move_nodes,
                on_main_line, graph_total,
            )

        # プロット対象: 現在パス上のノード（解析済みのみ）
        # ただし現在パスがメインラインの場合はメインライン全体の解析済みノードも含める
        # X軸はスライダー値と合わせて 1始まり（1手目=1）とする
        # _node_analyses はポンダリング中に値が変わるので、ここは毎回再構築する。
        xs, ys = [], []

        # まず現在パス上の解析済みノードを収集
        for i, n in enumerate(move_nodes_in_path):
            ma = self._node_analyses.get(id(n))
            if ma is not None:
                xs.append(i + 1)   # 1始まり: スライダー値と一致
                ys.append(ma.score_lead)

        # 現在パスがメインラインと一致する範囲より先のメインライン解析済みノードも追加
        # （手戻り後も前回解析したデータを消さない）
        cur_path_len = len(move_nodes_in_path)
        for i, n in enumerate(main_move_nodes):
            if i < cur_path_len:
                continue  # 現在パスと重複する範囲はスキップ
            ma = self._node_analyses.get(id(n))
            if ma is not None:
                xs.append(i + 1)   # 1始まり
                ys.append(ma.score_lead)

        # 現在位置のインデックス（1始まり）
        cur_plot_idx = len(move_nodes_in_path) if move_nodes_in_path else 0
        cur_ma = self._node_analyses.get(id(cur_node))

        graph = self._info.get_graph()
        if xs:
            # 解析データあり: 通常通り曲線を描画
            graph.set_data_sparse(xs, ys, graph_total)
        else:
            # 解析データなし(解析OFF や新規作成直後): 曲線データは空のままで、
            # X軸範囲(目盛りラベル)だけ手数に応じて更新する。これにより
            # グラフ下部の手数表示と縦線位置が正しく描画される。
            graph.set_total_moves(graph_total)

        cur_score = cur_ma.score_lead if cur_ma else dict(zip(xs, ys)).get(cur_plot_idx)
        graph.set_current(cur_plot_idx, score=cur_score, no_data=(cur_score is None and not self._ai_enabled))

    def closeEvent(self, ev):
        """ウィンドウを閉じる際にスレッドとエンジンを安全に停止する。
        棋譜に未保存の変更がある場合はユーザーに確認ダイアログを出し、
        キャンセルが選ばれたらクローズを中止する。

        経路ごとの確認タイミング:
          ・× ボタン経由: _animated_close で先に確認済み (_close_confirmed=True)。
            ここでは確認スキップ。closeEvent ではエンジン停止のみ実行する。
          ・Alt+F4 / OS シャットダウン経由: closeEvent が直接呼ばれるため、
            ここで確認ダイアログを出す。

        補足: × ボタン経由でアニメ後の closeEvent 時に確認すると、ウィンドウは
        既に opacity=0 + 縮小済みで「閉じた」と見える状態のため、ダイアログだけ
        がデスクトップに浮いて見える不具合が起きる。それを避けるため、
        × ボタン経路では _animated_close で先に確認するように分離してある。
        """
        # × ボタン経由で既に確認済みならスキップ。直接 closeEvent が
        # 呼ばれた経路 (Alt+F4 等) のみここで確認する。
        if not getattr(self, "_close_confirmed", False):
            # 編集中のコメントを先にノードへ反映(これで dirty が確定する)
            if self._game_state is not None:
                try:
                    self._save_comment_if_editing(self._game_state.current_node)
                except Exception:
                    pass
            # 未保存の変更があれば確認(中止された場合はクローズも中止)
            if not self._confirm_discard_or_save():
                ev.ignore()
                return
        # プロファイラ有効時は最後の集計をフラッシュしてスレッド停止
        try:
            _profiler.stop()
        except Exception:
            pass
        # シグナルを切断してC++オブジェクト破棄後のemitを防ぐ
        try:
            self._ponder_result_signal.disconnect()
        except Exception:
            pass
        # エンジン停止
        if hasattr(self, '_engine') and self._engine:
            try:
                self._engine.stop_pondering()
                self._engine.stop()
            except Exception:
                pass
        ev.accept()

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

    def _apply_min_window_size(self, panel_open: bool) -> None:
        """右パネル開閉状態に応じて setMinimumSize を切り替える。

        - panel_open=True : (_MIN_WIN_OPEN_W, _MIN_WIN_OPEN_H)
        - panel_open=False: 幅をパネル分(=_FP_MIN_W + _FP_MARGIN_X*2)縮める

        通常時の開閉アニメ呼び出し規約:
          - 閉じる時: アニメ前にこのメソッド(open=False)を呼ぶ
                     (Qt がアニメ後の小さい幅を阻止しないように先に下げる)
          - 開く時:  アニメ後にこのメソッド(open=True)を呼ぶ
                     (アニメ前に上げると現在幅 < 新min となり Qt がいきなり
                      ウィンドウをリサイズしてしまう)
        """
        if panel_open:
            new_w = self._MIN_WIN_OPEN_W
        else:
            new_w = self._MIN_WIN_OPEN_W - self._FP_MIN_W - self._FP_MARGIN_X * 2
        new_h = self._MIN_WIN_OPEN_H
        self.setMinimumSize(new_w, new_h)

    def _place_panels(self):
        """left の幅を碁盤用に設定し、情報パネル・ナビバーを配置する。
        情報パネルは left の右隣（碁盤とは重ならない）、
        ナビバーは碁盤エリア下部中央にフローティング。

        新2層構造: centralWidget(outer) 内の VBoxLayout で
        [_titlebar(36), _root_widget(残り)] と縦並び。
        本メソッドは _root_widget 内で絶対配置を行うため、
        rw/rh は _root_widget のサイズを使う。
        """
        root = getattr(self, "_root_widget", None)
        if root is None:
            return

        rw = root.width()
        rh = root.height()

        # 右パネルアニメ中は、最終形のroot幅を使ってレイアウト計算する。
        # これがないと開く時、アニメ初期(ウィンドウまだ狭い)に
        # max_board_w_by_panel = rw - _FP_MIN_W が小さくなり碁盤が一瞬縮む問題が起きる。
        rw_override = getattr(self, "_panel_anim_target_rw", None)
        if rw_override is not None and rw_override > 0:
            rw = rw_override

        if rw <= 0 or rh <= 0:
            return



        fp_m = self._FP_MARGIN          # 縦方向(上下)
        fp_mx = self._FP_MARGIN_X       # 横方向(左右)
        # 情報パネルの表示状態に応じて碁盤エリア幅を決定
        # ウェルカム画面では情報パネルが非表示 → left_col を全幅に広げる
        # 右パネル開閉アニメ中は、最終形の状態(target_visible)を優先する。
        # フェードアニメ中は実際にはまだ visible だが、レイアウト的には目標状態で
        # 計算しないと、閉じる時に碁盤が一瞬縮むなどの揺れが起きる。
        anim_target_visible = getattr(self, "_panel_anim_target_visible", None)
        if anim_target_visible is not None:
            panel_visible = anim_target_visible
        else:
            panel_visible = (hasattr(self, "_right_col")
                             and self._right_col.isVisible())

        # 碁盤エリアの利用可能高さ: rh - ナビバー領域
        nb_h_reserve = NavBar.NB_HEIGHT + NavBar.NB_MARGIN  # ナビバー + 下マージン
        avail_board_h = rh - nb_h_reserve

        if panel_visible:
            # ── レスポンシブ配置 (戦略B寄り) ──
            # 1) 碁盤エリアを正方形に保つ(board_w == board_h)。
            #    幅と高さの小さい方に合わせる。
            #    ・縦方向の上限: avail_board_h
            #    ・横方向の上限: rw - _FP_MIN_W - fp_mx*2 (パネル MIN を確保)
            # 2) パネルは余り横幅すべてを引き取る(MIN 以上、上限なし)。
            #    ウィンドウが横に広い場合、パネルが画面右端まで広がる。
            max_board_w_by_panel = rw - self._FP_MIN_W - fp_mx * 2
            board_size = min(avail_board_h, max_board_w_by_panel)
            board_w = board_size
            board_h = board_size
            # 余り幅をパネルに全て割り当て(MIN 以上を保証)
            remaining = rw - board_w - fp_mx * 2
            fp_w = max(self._FP_MIN_W, remaining)
            # right_x は画面右端から逆算(右側 fp_mx の余白を保証)
            right_x = rw - fp_w - fp_mx
            _fp_inner_w = fp_w
        else:
            # パネル非表示時は全幅を碁盤エリアに使う(従来通り)
            board_w = rw
            board_h = avail_board_h

        # ── left（碁盤エリア）─────────────────────────────────────────────
        # 碁盤+ナビバーをひとまとめにして上下中央に配置する。
        # 横方向制約で board_h が avail_board_h より小さくなった場合、
        # 余った縦スペースを上下に等分して上に top_offset を確保する。
        board_block_h = board_h + nb_h_reserve  # 碁盤 + ナビバー領域
        top_offset = max(0, (rh - board_block_h) // 2)
        if hasattr(self, "_left_col"):
            self._left_col.setGeometry(0, 0, board_w, rh)
        if hasattr(self, "_left_stack"):
            self._left_stack.setGeometry(0, top_offset, board_w, board_h)

        # ── 情報パネル ────────────────────────────────────────────────────
        if panel_visible and hasattr(self, "_right_col"):
            fp_h = max(200, rh - fp_m * 2)
            self._right_col.setGeometry(right_x, fp_m, fp_w, fp_h)
            self._right_col.raise_()
            if hasattr(self, "_cards_scroll"):
                self._cards_scroll.setMaximumWidth(_fp_inner_w)
                w = self._cards_scroll.widget()
                if w:
                    w.setMaximumWidth(_fp_inner_w)

        # ── ナビバー（碁盤に少し食い込ませる） ────────────────────────────
        # 碁盤エリアの下端はそのままだが、ナビバーを 8px 上にオフセットして
        # 碁盤の下マージン部分(罫線より外側の木目余白)に重ねる。
        # これによりスライダーが碁盤の罫線にもう少し近づいて見える。
        # 値は NavBar.NB_OVERLAP として定数化。
        nb_overlap = NavBar.NB_OVERLAP
        if hasattr(self, "_navbar"):
            nb_h = NavBar.NB_HEIGHT
            nb_y = top_offset + board_h - nb_overlap  # 碁盤に食い込ませる
            self._navbar.setGeometry(0, nb_y, board_w, nb_h)
            self._navbar.raise_()

        # ── コメントオーバーレイ（ナビバーの直上） ───────────────────────
        if hasattr(self, "_comment_overlay"):
            ov_h = 100
            ov_w = board_w - 60  # 碁盤エリア幅 - 60px
            ov_x = (board_w - ov_w) // 2  # 中央揃え
            # ナビバーが上にオフセットしたので、オーバーレイ下端 = ナビバー上端
            ov_y = top_offset + board_h - nb_overlap - ov_h
            # コメントオーバーレイの開閉アニメ中はジオメトリをアニメに任せる
            # (setGeometry で上書きするとアニメが破綻する)
            if not getattr(self, "_comment_anim_running", False):
                self._comment_overlay.setGeometry(ov_x, ov_y, ov_w, ov_h)
            self._comment_overlay.raise_()
            # フェードオーバーレイをコメントオーバーレイ全体に追従させる
            if hasattr(self, "_comment_fade_overlay"):
                self._comment_fade_overlay.setGeometry(0, 0, ov_w, ov_h)
                self._comment_fade_overlay.raise_()
                self._comment_fade_overlay.update_visibility()
            # 閉じるボタンを右上に配置(フェードより手前)
            if hasattr(self, "_comment_close_btn"):
                btn = self._comment_close_btn
                btn.move(ov_w - btn.width() - SP_XS, SP_XS)
                btn.raise_()

        # ── D&D オーバーレイの追従 ─────────────────────────────
        # _root_widget の子なのでサイズ更新だけ行えばよい(タイトルバーは
        # 自動で除外される)。表示中のときのみカード位置も再計算する。
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.setGeometry(0, 0, rw, rh)
            if self._drop_overlay.isVisible():
                cw = self._drop_card.width()
                ch = self._drop_card.height()
                self._drop_card.move((rw - cw) // 2, (rh - ch) // 2)
                self._drop_overlay.raise_()

    def showEvent(self, ev):
        """初回表示時にフローティングパネルを配置する。"""
        super().showEvent(ev)
        # 0ms: イベントループ開始直後（ウィンドウサイズ確定後）に配置
        # 50ms: DWM / ウィンドウマネージャのフレーム確定後に再配置（Windows対策）
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0,  self._place_panels)
        QTimer.singleShot(50, self._place_panels)
        # コメントオーバーレイのプリウォームは起動アニメ完了後に遅らせる
        # (アニメ中に走るとカクつくため)。500ms 後 = アニメ完了後。
        if not getattr(self, "_overlay_prewarmed", False):
            QTimer.singleShot(500, self._do_prewarm_overlay_once)
        # Windows 11 ネイティブ角丸を有効化(初回のみ)。
        # winId() がここで確定しているため、 __init__ ではなく showEvent で行う。
        if not getattr(self, "_win11_corners_applied", False):
            self._win11_corners_applied = True
            self._enable_win11_rounded_corners()
        # 初回表示時にフェードインアニメを実行(初回のみ、最小化からの
        # 復元では呼ばない)。
        # showEvent 直後だと _place_panels(0ms,50ms) と競合してカクつくため、
        # 80ms 遅延させてレイアウト確定後に開始する。
        if not getattr(self, "_startup_anim_done", False):
            self._startup_anim_done = True
            QTimer.singleShot(80, self._animated_show_on_startup)

    def _enable_win11_rounded_corners(self):
        """Windows 11 で window corner を rounded にする(OS側で描画)。
        Win10 では attribute_key=33 が認識されず E_INVALIDARG が返るだけで、
        例外も視覚効果の変化もない(矩形のまま)。Win11 build 22000 以降で
        効果が現れる。Mac/Linux では win32 ではないので即時 return。
        """
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2  # 値 2 = 通常の角丸(他: 0=Default, 1=DoNotRound, 3=RoundSmall)
            hwnd = int(self.winId())
            value = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            # dwmapi.dll が無い環境(Wine 等)でも害なくスルー
            pass

    def _do_prewarm_overlay_once(self):
        """プリウォームを最初の1回だけ実行する。"""
        if getattr(self, "_overlay_prewarmed", False):
            return
        self._overlay_prewarmed = True
        self._prewarm_comment_overlay()

    # ── frameless ウィンドウ用のイベント処理 ──────────────────────────
    # FramelessWindowHint で OS 標準のタイトルバーを廃したため、
    # ドラッグ移動・ダブルクリック最大化・端のリサイズを自前で実装する。

    _RESIZE_BORDER = 6  # ウィンドウ端からこのピクセル以内をリサイズホットエリアとする

    def _toggle_maximized(self):
        """最大化↔復元 を切り替える。タイトルバーの[□]ボタンから呼ばれる。

        実装方針: QPropertyAnimation で geometry を300msかけて補間 +
        アニメ中はウィンドウ最前面に背景色のオーバーレイをかぶせて、
        ウィンドウの中身を完全に隠蔽する。

        - frameless ウィンドウでは Qt の showMaximized() は OS のアニメが
          動かないため、自前で補間アニメを行う。
        - ジオメトリ変化に伴う子ウィジェットの再描画タイミングのズレが
          カクつきとして見えるため、アニメ中は背景色オーバーレイで画面
          を覆って中身を見えなくする。
        - オーバーレイは MainWindow の centralWidget の子にして、
          ウィンドウリサイズに valueChanged で追従させる。
        - 連打防止: _anim_in_progress フラグで保護
        - 角丸切替: 完了時に実行
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QEasingCurve, QRect,
        )
        # 既にアニメ中なら無視(連打対策)
        if getattr(self, "_anim_in_progress", False):
            return
        if self._is_pseudo_maximized():
            # 復元
            target_geom = getattr(self, "_pre_max_geometry", None)
            if target_geom is None:
                # 念のためのフォールバック(復元先がない場合は中央配置)
                screen = self.screen() or QApplication.primaryScreen()
                avail = screen.availableGeometry()
                w = min(self.width(), int(avail.width() * 0.7))
                h = min(self.height(), int(avail.height() * 0.7))
                target_geom = QRect(
                    avail.x() + (avail.width() - w) // 2,
                    avail.y() + (avail.height() - h) // 2,
                    w, h,
                )
            new_max_state = False
            self._pseudo_max_active = False
        else:
            # 最大化: 現在のジオメトリを保存し、availableGeometry へ拡大
            self._pre_max_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            target_geom = screen.availableGeometry()
            new_max_state = True
            self._pseudo_max_active = True

        # アニメ中フラグ
        self._anim_in_progress = True
        # タイトルバーアイコンは即時更新(視覚的な応答性のため)
        if hasattr(self, "_titlebar"):
            self._titlebar.update_max_restore_icon(new_max_state)

        # ── 不透明オーバーレイを作成(背景色で塗りつぶし) ──
        # アニメ中ずっと完全不透明で表示し、ウィンドウ中身を見えなくする。
        # ウィンドウと同じ角丸 R_MD(12px) を指定して、ウィンドウの角と
        # オーバーレイの角がぴったり一致するようにする。
        from PyQt6.QtWidgets import QWidget
        cw = self.centralWidget()
        if cw is not None:
            overlay = QWidget(cw)
            overlay.setObjectName("max_anim_overlay")
            theme_bg = T().BG
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            # StyleSheet で背景色 + 角丸を設定
            overlay.setStyleSheet(
                f"#max_anim_overlay {{"
                f"  background-color: rgb({theme_bg.red()},{theme_bg.green()},{theme_bg.blue()});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )
            overlay.setGeometry(0, 0, cw.width(), cw.height())
            overlay.raise_()
            overlay.show()
            self._max_overlay = overlay
        else:
            self._max_overlay = None

        # ── アニメ中、_root_widget の再描画を停止して滑らかさを確保 ──
        # ジオメトリアニメ中は中身(碁盤・パネル・ナビバー等)の resizeEvent /
        # paintEvent が毎フレーム走り、これがCPU/GPU負荷とフレームスキップの
        # 原因になる。オーバーレイで完全に隠蔽している間は中身を描画する
        # 必要がないので、_root_widget を setUpdatesEnabled(False) にして
        # 内部の再描画を抑制する。
        # _titlebar とオーバーレイは _root_widget の兄弟(centralWidget の子)
        # なので、この設定の影響を受けず引き続き描画される。
        # アニメ完了時に setUpdatesEnabled(True) + update() で復帰する。
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(False)

        # ── ジオメトリアニメ(300ms、OutCubic) ──
        DURATION = 300
        geom_anim = QPropertyAnimation(self, b"geometry")
        geom_anim.setDuration(DURATION)
        geom_anim.setStartValue(self.geometry())
        geom_anim.setEndValue(target_geom)
        geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # フレームごとにオーバーレイをウィンドウサイズへ追従
        geom_anim.valueChanged.connect(self._update_overlay_geometry)
        geom_anim.finished.connect(lambda: self._on_maximize_anim_done(new_max_state))
        self._max_anim = geom_anim
        geom_anim.start()

    def _update_overlay_geometry(self, _v=None):
        """オーバーレイをウィンドウサイズに追従させる。"""
        ov = getattr(self, "_max_overlay", None)
        if ov is not None:
            cw = self.centralWidget()
            if cw is not None:
                ov.setGeometry(0, 0, cw.width(), cw.height())
                ov.raise_()  # 念のため最前面を維持

    def _on_maximize_anim_done(self, is_now_maximized: bool = False):
        """最大化/復元アニメ完了時の後処理。
        ジオメトリアニメは完了したが、オーバーレイをフェードアウトさせる。
        フェードアウト完了後にオーバーレイを破棄する。
        """
        # 最終形状で _place_panels を1回実行(レイアウト確定)
        # この時点では _root_widget は setUpdatesEnabled(False) のままなので
        # 内部の paintEvent はまだ走らない。
        self._place_panels()
        # 角丸切替: 最大化中は矩形、復元なら角丸復活
        self._set_win11_corner_round(not is_now_maximized)

        # ── _root_widget の再描画を再開 ──
        # _toggle_maximized でアニメ開始時に setUpdatesEnabled(False) にして
        # いるので、ここで True に戻して update() で再描画を強制する。
        # オーバーレイはまだ完全不透明で被さっているので、再描画中の
        # ちらつきは見えない(これからフェードアウトしていく間に下に
        # 整った中身が表示される)。
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(True)
            self._root_widget.update()

        # オーバーレイのフェードアウト(150ms)
        ov = getattr(self, "_max_overlay", None)
        if ov is None:
            self._anim_in_progress = False
            return

        # rgba の alpha 値を 255 → 0 にアニメ
        # alpha値を動的に変えるため、Qオブジェクトのプロパティを介して
        # QPropertyAnimation で操作する。シンプルにタイマー駆動でstyleSheet
        # を再設定する方式の方が確実なので、そちらを採用する。
        from PyQt6.QtCore import QTimer
        FADE_OUT_MS = 150
        FRAME_INTERVAL_MS = 16  # 約60fps
        STEPS = max(1, FADE_OUT_MS // FRAME_INTERVAL_MS)
        theme_bg = T().BG
        bg_r, bg_g, bg_b = theme_bg.red(), theme_bg.green(), theme_bg.blue()

        self._max_overlay_fade_step = 0
        self._max_overlay_fade_timer = QTimer(self)
        self._max_overlay_fade_timer.setInterval(FRAME_INTERVAL_MS)

        def _fade_step():
            self._max_overlay_fade_step += 1
            t = self._max_overlay_fade_step / STEPS
            if t >= 1.0:
                # フェードアウト完了
                self._max_overlay_fade_timer.stop()
                self._max_overlay_fade_timer.deleteLater()
                ov2 = getattr(self, "_max_overlay", None)
                if ov2 is not None:
                    ov2.hide()
                    ov2.deleteLater()
                    self._max_overlay = None
                self._anim_in_progress = False
                return
            # OutCubic イージング: 1 - (1-t)^3
            eased = 1 - (1 - t) ** 3
            alpha = int(255 * (1 - eased))  # 255 → 0
            ov.setStyleSheet(
                f"#max_anim_overlay {{"
                f"  background-color: rgba({bg_r},{bg_g},{bg_b},{alpha});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )

        self._max_overlay_fade_timer.timeout.connect(_fade_step)
        self._max_overlay_fade_timer.start()

    # ── 最小化・閉じる・起動時のアニメーション ────────────────────────
    def _detect_taskbar_direction(self) -> str:
        """タスクバーの位置(画面のどの辺にあるか)を推定する。
        戻り値: "bottom" / "top" / "left" / "right"
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return "bottom"
        avail = screen.availableGeometry()
        full = screen.geometry()
        # avail と full の差からタスクバーの場所を判定
        if avail.bottom() < full.bottom():
            return "bottom"
        elif avail.top() > full.top():
            return "top"
        elif avail.x() > full.x():
            return "left"
        elif avail.right() < full.right():
            return "right"
        return "bottom"  # デフォルト

    # ───────────────────────── 右パネル開閉 ─────────────────────────
    def _toggle_right_panel(self):
        """右パネルの開閉をトグルする。
        - 通常時: ウィンドウ自体の横幅を 300ms かけて変化させる(左端固定)
        - 最大化中: ウィンドウは変えず、パネル領域だけ即座に hide/show する
        - アニメ中の連打は無視
        """
        if getattr(self, "_panel_anim_running", False):
            return
        if not hasattr(self, "_right_col"):
            return

        # 現在の状態を反転
        currently_collapsed = getattr(self, "_right_panel_collapsed", False)
        new_collapsed = not currently_collapsed
        self._right_panel_collapsed = new_collapsed

        # アイコン即座に更新
        if hasattr(self, "_titlebar"):
            self._titlebar.update_panel_toggle_icon(is_open=not new_collapsed)

        # QSettings に保存
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("right_panel_collapsed", new_collapsed)

        # 最大化中: ウィンドウ幅は変えず、内部要素だけアニメ
        # - _right_col の opacity をフェード
        # - _left_stack の幅を新しい board_w へアニメ
        # で「パネル消失 + 碁盤の中央移動」を滑らかに演出する。
        if self._is_pseudo_maximized() or self.isMaximized():
            self._animate_max_panel_toggle(new_collapsed)
            return

        # 通常時: ウィンドウ幅をアニメで変える(左端固定)
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect
        cur_geo = self.geometry()
        # パネル幅を計算(現在の panel 幅 or 既定値)
        panel_w = self._right_col.width() if self._right_col.isVisible() else self._last_panel_width
        if panel_w <= 0:
            panel_w = self._FP_MIN_W + self._FP_MARGIN_X * 2

        if new_collapsed:
            # 閉じる: パネル分縮める
            # 閉じた状態の最小ウィンドウ幅(_apply_min_window_size と整合)
            closed_min_w = (self._MIN_WIN_OPEN_W
                            - self._FP_MIN_W - self._FP_MARGIN_X * 2)
            # cur_geo.width() - panel_w が closed_min_w を下回ると、
            # アニメ後に Qt が min サイズに強制リサイズして「再オープン時に
            # ウィンドウ幅が想定より広くなる → パネル幅も広がる」事故が起きる。
            # よってここでは _FP_MIN_W ではなく closed_min_w を下限にする。
            new_w = max(closed_min_w, cur_geo.width() - panel_w)
            target_geo = QRect(cur_geo.x(), cur_geo.y(), new_w, cur_geo.height())
            # 復元用に保存するのは「実際にアニメで縮めた幅」(= cur幅 - new_w)。
            # panel_w 自体ではなく、ウィンドウ幅の差を記録することで、
            # closed_min_w で頭打ちされた場合でも開いた時に元のウィンドウ幅
            # に戻れるようにする。
            self._last_panel_width = cur_geo.width() - new_w
            # 閉じる時はパネルをすぐ隠さず、フェードアウトしてから完了時に隠す
            # 最小ウィンドウサイズを「閉じた状態」のサイズに先に下げる
            # (Qt がアニメ後の小さい幅を阻止しないように)
            self._apply_min_window_size(panel_open=False)
        else:
            # 開く: パネル分広げる + パネル再表示
            new_w = cur_geo.width() + panel_w
            # 画面端を超えないようクリップ
            from PyQt6.QtWidgets import QApplication
            screen = self.screen() or QApplication.primaryScreen()
            avail = screen.availableGeometry()
            max_w = avail.x() + avail.width() - cur_geo.x()
            new_w = min(new_w, max_w)
            target_geo = QRect(cur_geo.x(), cur_geo.y(), new_w, cur_geo.height())
            # パネルを表示してからアニメ
            self._right_col.setVisible(True)

        # アニメ中は _place_panels の rw 計算を最終形ウィンドウ幅で固定する。
        # 開く時、アニメ初期でウィンドウがまだ狭いと碁盤が一瞬縮んで見えるのを防ぐ。
        # root_widget の幅 = ウィンドウ幅(タイトルバー以外なので親と同じ横幅)
        self._panel_anim_target_rw = new_w
        # アニメ中の論理的な panel_visible 状態(目標状態)。
        # フェードアウト中は実際にはまだ visible だが、レイアウト計算では
        # 目標状態(=非表示)を使う。
        self._panel_anim_target_visible = (not new_collapsed)

        self._panel_anim_running = True

        # ── ジオメトリアニメ(ウィンドウ幅) ──
        from PyQt6.QtCore import QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        geo_anim = QPropertyAnimation(self, b"geometry")
        geo_anim.setDuration(300)
        geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        geo_anim.setStartValue(cur_geo)
        geo_anim.setEndValue(target_geo)
        # アニメ中は毎フレーム _place_panels で内部レイアウトを追従
        geo_anim.valueChanged.connect(lambda _v: self._place_panels())

        # ── 右パネル opacity アニメ ──
        # 過去 QGraphicsOpacityEffect 単独では黒帯バグが発生したが、
        # アニメ中だけ autoFillBackground=False にすることで合成挙動を変え、
        # 黒帯を回避できないか検証する。
        # 閉じる: 1.0 → 0.0、開く: 0.0 → 1.0
        self._right_col.setAutoFillBackground(False)
        opacity_eff = QGraphicsOpacityEffect(self._right_col)
        opacity_eff.setOpacity(1.0 if new_collapsed else 0.0)
        self._right_col.setGraphicsEffect(opacity_eff)
        self._panel_opacity_effect = opacity_eff  # 完了時に解除するため保持
        opacity_anim = QPropertyAnimation(opacity_eff, b"opacity")
        opacity_anim.setDuration(300)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity_anim.setStartValue(1.0 if new_collapsed else 0.0)
        opacity_anim.setEndValue(0.0 if new_collapsed else 1.0)

        # ── 並列実行 ──
        group = QParallelAnimationGroup(self)
        group.addAnimation(geo_anim)
        group.addAnimation(opacity_anim)
        group.finished.connect(self._on_panel_anim_done)
        self._panel_anim = group
        group.start()

    def _animate_max_panel_toggle(self, new_collapsed: bool):
        """最大化中の右パネル開閉アニメ。
        ウィンドウ幅は変えられないので、内部要素を 300ms でアニメする。
        - 開く時: パネルをfade in、_left_stack を新 board_w に縮める
        - 閉じる時: パネルをfade out、_left_stack を全幅に広げる

        注意: アニメ中、_left_stack のジオメトリ補間で内部の碁盤に水平方向の
        ティアリング(絵が分割表示される現象)が発生することがある。これは
        Qt + Windows DWM の組み合わせにおけるフレーム合成タイミングの制約
        によるもので、setUpdatesEnabled / setFixedSize / QPixmap オーバーレイ
        など複数の対策を試したが完全に消すのは困難なため、現状は受容する。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        # 閉じる時はアニメ前に最小ウィンドウサイズを下げる
        # (通常時の挙動と一貫させる)
        if new_collapsed:
            self._apply_min_window_size(panel_open=False)

        # 現在のジオメトリを保持
        cur_left_stack_geo = self._left_stack.geometry() if hasattr(self, "_left_stack") else None
        if cur_left_stack_geo is None:
            # フォールバック: アニメせず即時切替
            self._right_col.setVisible(not new_collapsed)
            # 開く時の min 拡大は _on_panel_anim_done で行うが、ここを通った場合は
            # _on_panel_anim_done が呼ばれないので直接適用する
            if not new_collapsed:
                self._apply_min_window_size(panel_open=True)
            self._place_panels()
            return

        # 開く時はパネルを先に表示してフェードイン
        if not new_collapsed:
            self._right_col.setVisible(True)

        # 最終形のレイアウトを計算するため、target_visible を仮設定して
        # _place_panels の panel_visible 判定を上書き → 最終形の board_w を取得
        prev_target_visible = getattr(self, "_panel_anim_target_visible", None)
        self._panel_anim_target_visible = (not new_collapsed)
        # 一度通常の _place_panels を呼んで「最終形のジオメトリ」を取得する
        # → 直後にアニメ開始時点に戻すため、現状のジオメトリを保存しておく
        cur_lc_geo = self._left_col.geometry() if hasattr(self, "_left_col") else None
        cur_nb_geo = self._navbar.geometry() if hasattr(self, "_navbar") else None
        self._place_panels()
        target_left_stack_geo = self._left_stack.geometry()
        target_lc_geo = self._left_col.geometry() if hasattr(self, "_left_col") else None
        target_nb_geo = self._navbar.geometry() if hasattr(self, "_navbar") else None
        # アニメ開始時点(現状)のジオメトリに戻す
        # ※ _left_stack だけでなく _left_col / _navbar も戻さないと、最終形の
        #    全幅で配置されたままになり、パネルが裏に隠れてフェードが見えなくなる。
        self._left_stack.setGeometry(cur_left_stack_geo)
        if cur_lc_geo is not None:
            self._left_col.setGeometry(cur_lc_geo)
        if cur_nb_geo is not None:
            self._navbar.setGeometry(cur_nb_geo)

        # ── Z順序制御 ──
        # 閉じる時: 碁盤エリアを前面に出す。アニメで碁盤が広がるとき、その
        #          下にあるパネルが「碁盤に少しずつ覆い被されながらフェード
        #          アウト」する見え方になる。重なり時に色が混ざることもない。
        # 開く時:   パネルを前面に出す。フェードインしながら碁盤の上に正しく
        #          描画される。
        if new_collapsed:
            if hasattr(self, "_left_col"):
                self._left_col.raise_()
            if hasattr(self, "_left_stack"):
                self._left_stack.raise_()
            if hasattr(self, "_navbar"):
                self._navbar.raise_()
        else:
            if hasattr(self, "_right_col"):
                self._right_col.raise_()

        self._panel_anim_running = True

        # ── _left_stack の geometry アニメ ──
        ls_anim = QPropertyAnimation(self._left_stack, b"geometry")
        ls_anim.setDuration(300)
        ls_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        ls_anim.setStartValue(cur_left_stack_geo)
        ls_anim.setEndValue(target_left_stack_geo)

        # ── _left_col(碁盤エリアの背景)の geometry も連動させる ──
        anims = [ls_anim]
        if hasattr(self, "_left_col"):
            cur_lc_geo = self._left_col.geometry()
            target_lc_geo = QRect(0, 0, target_left_stack_geo.width(), cur_lc_geo.height())
            lc_anim = QPropertyAnimation(self._left_col, b"geometry")
            lc_anim.setDuration(300)
            lc_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            lc_anim.setStartValue(cur_lc_geo)
            lc_anim.setEndValue(target_lc_geo)
            anims.append(lc_anim)

        # ── ナビバーも碁盤幅に合わせて連動 ──
        if hasattr(self, "_navbar") and self._navbar.isVisible():
            cur_nb_geo = self._navbar.geometry()
            # ナビバーは碁盤エリア下端中央(0オフセットで board_w 全幅)
            target_nb_geo = QRect(0, cur_nb_geo.y(),
                                  target_left_stack_geo.width(), cur_nb_geo.height())
            nb_anim = QPropertyAnimation(self._navbar, b"geometry")
            nb_anim.setDuration(300)
            nb_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            nb_anim.setStartValue(cur_nb_geo)
            nb_anim.setEndValue(target_nb_geo)
            anims.append(nb_anim)

        # ── パネルの opacity アニメ ──
        opacity_eff = QGraphicsOpacityEffect(self._right_col)
        opacity_eff.setOpacity(1.0 if new_collapsed else 0.0)
        self._right_col.setGraphicsEffect(opacity_eff)
        self._panel_opacity_effect = opacity_eff
        op_anim = QPropertyAnimation(opacity_eff, b"opacity")
        op_anim.setDuration(300)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        op_anim.setStartValue(1.0 if new_collapsed else 0.0)
        op_anim.setEndValue(0.0 if new_collapsed else 1.0)
        anims.append(op_anim)

        # ── パネルの位置スライドアニメ ──
        # フェードと並列に位置を動かすことで「スライドイン/アウト + フェード」を演出する。
        # 閉じる時: 現在位置 → 画面右端の外側へ右にスライドアウト
        # 開く時:   画面右端の外側 → 最終位置へ左にスライドイン
        cur_panel_geo = self._right_col.geometry()
        # 画面外右側の位置(rw 相当を root_widget の右端から取得)
        rw_outside = (self._root_widget.width()
                      if hasattr(self, "_root_widget") and self._root_widget
                      else cur_left_stack_geo.x() + cur_left_stack_geo.width())
        if new_collapsed:
            # 閉じる: 現在位置から画面外へスライドアウト
            start_panel_geo = cur_panel_geo
            target_panel_geo = QRect(rw_outside, cur_panel_geo.y(),
                                     cur_panel_geo.width(), cur_panel_geo.height())
        else:
            # 開く: 画面外から最終位置へスライドイン
            # 最終位置は _place_panels(panel_visible=True) 時の right_x
            # = root幅 - パネル幅 - fp_mx
            final_right_x = rw_outside - cur_panel_geo.width() - self._FP_MARGIN_X
            start_panel_geo = QRect(rw_outside, cur_panel_geo.y(),
                                    cur_panel_geo.width(), cur_panel_geo.height())
            target_panel_geo = QRect(final_right_x, cur_panel_geo.y(),
                                     cur_panel_geo.width(), cur_panel_geo.height())
            # 開始位置に即座に配置
            self._right_col.setGeometry(start_panel_geo)
        slide_anim = QPropertyAnimation(self._right_col, b"geometry")
        slide_anim.setDuration(300)
        slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide_anim.setStartValue(start_panel_geo)
        slide_anim.setEndValue(target_panel_geo)
        anims.append(slide_anim)

        # ── 並列実行 ──
        group = QParallelAnimationGroup(self)
        for a in anims:
            group.addAnimation(a)
        # 最大化中は target_rw オーバーライドは使わない
        self._panel_anim_target_rw = None
        group.finished.connect(self._on_panel_anim_done)
        self._panel_anim = group
        group.start()

    def _on_panel_anim_done(self):
        """右パネルアニメ完了時の後処理。
        - 幅オーバーライド・visible オーバーライドを解除
        - 閉じた場合は実際に setVisible(False)
        - opacity effect を解除(描画パイプラインの常時負荷を避ける)
        - 最小ウィンドウサイズを最終形に合わせて更新
          (開いた時のみ。閉じた時はアニメ前に既に下げてある)
        - アニメ中に False にした autoFillBackground を True に戻す
        """
        self._panel_anim_running = False
        # アニメ用のオーバーライドを解除
        self._panel_anim_target_rw = None
        target_vis = getattr(self, "_panel_anim_target_visible", None)
        self._panel_anim_target_visible = None
        # 閉じた場合: ここで初めて実際に非表示にする
        if hasattr(self, "_right_col") and target_vis is False:
            self._right_col.setVisible(False)
        # 開いた場合: 最小ウィンドウサイズを「開いた状態」に上げる
        # (閉じる時はアニメ前に既に下げているのでここでの操作不要)
        if target_vis is True:
            self._apply_min_window_size(panel_open=True)
        # opacity effect を解除して描画パイプラインの常時負荷を回避
        if hasattr(self, "_panel_opacity_effect") and self._panel_opacity_effect is not None:
            try:
                if hasattr(self, "_right_col"):
                    self._right_col.setGraphicsEffect(None)
            except Exception:
                pass
            self._panel_opacity_effect = None
        # アニメ中 False にしていた autoFillBackground を True に戻す
        # (角丸の外側に旧色が残らないようにするため)。
        if hasattr(self, "_right_col"):
            self._right_col.setAutoFillBackground(True)
        self._place_panels()

    def _animated_minimize(self):
        """最小化ボタン用カスタムアニメ。
        最大化アニメと同じ「不透明オーバーレイで中身を隠蔽 + 内部再描画停止 +
        ウィンドウのジオメトリ/透明度をアニメ」方式で、ガタつきを抑える。

        フロー:
          1. 不透明オーバーレイを最前面に被せ、ウィンドウ中身を隠蔽
          2. _root_widget.setUpdatesEnabled(False) で内部の再描画を停止
          3. windowOpacity 1.0→0.0 と geometry を「下方向に縮小+移動(沈む)」で
             同時アニメ(200ms / InCubic)
          4. 完了したら showMinimized() を呼び、ジオメトリ/透明度を復元、
             オーバーレイ破棄、内部再描画を再開
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect,
        )
        # 既にアニメ中なら無視(連打対策)
        if getattr(self, "_anim_in_progress", False):
            return
        # 既に最小化中なら何もしない
        if self.windowState() & Qt.WindowState.WindowMinimized:
            return

        self._anim_in_progress = True
        # 最小化前のジオメトリ・透明度を保存(復元時に元に戻すため)
        self._pre_minimize_geometry = self.geometry()
        self._pre_minimize_opacity = self.windowOpacity()

        # ── 縮小先ジオメトリの計算 ───────────────────────────────────
        cur = self.geometry()
        # 縮小率 75%
        target_w = int(cur.width() * 0.75)
        target_h = int(cur.height() * 0.75)
        cx = cur.x() + (cur.width() - target_w) // 2
        cy = cur.y() + (cur.height() - target_h) // 2
        # 「下に沈む」演出: 常に下方向へ +300px オフセット
        offset = 300  # px
        cy += offset
        target_geom = QRect(cx, cy, target_w, target_h)

        # ── 不透明オーバーレイを作成(最大化アニメと同じ作り) ──────────
        # アニメ中ずっと完全不透明で表示し、ウィンドウ中身を見えなくする。
        # ウィンドウと同じ角丸 R_MD(12px) を指定して、ウィンドウの角と
        # オーバーレイの角がぴったり一致するようにする。
        from PyQt6.QtWidgets import QWidget
        cw = self.centralWidget()
        if cw is not None:
            overlay = QWidget(cw)
            overlay.setObjectName("min_anim_overlay")
            theme_bg = T().BG
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            overlay.setStyleSheet(
                f"#min_anim_overlay {{"
                f"  background-color: rgb({theme_bg.red()},{theme_bg.green()},{theme_bg.blue()});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )
            overlay.setGeometry(0, 0, cw.width(), cw.height())
            overlay.raise_()
            overlay.show()
            self._min_overlay = overlay
        else:
            self._min_overlay = None

        # ── アニメ中、_root_widget の再描画を停止 ──────────────────────
        # 最大化アニメと同じ。ジオメトリ変化に伴う子ウィジェットの
        # resizeEvent / paintEvent を抑制し、ガタつきの原因になる
        # CPU/GPU 負荷とフレームスキップを回避する。
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(False)

        # ── アニメーション(200ms / InCubic) ───────────────────────────
        DURATION = 200

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(DURATION)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)

        shrink = QPropertyAnimation(self, b"geometry")
        shrink.setDuration(DURATION)
        shrink.setStartValue(cur)
        shrink.setEndValue(target_geom)
        shrink.setEasingCurve(QEasingCurve.Type.InCubic)
        # フレームごとにオーバーレイをウィンドウサイズへ追従させる
        shrink.valueChanged.connect(self._update_min_overlay_geometry)

        group = QParallelAnimationGroup()
        group.addAnimation(fade)
        group.addAnimation(shrink)
        group.finished.connect(self._on_minimize_anim_done)
        self._min_anim = group  # GC対策で保持
        group.start()

    def _update_min_overlay_geometry(self, _v=None):
        """最小化アニメ中のオーバーレイをウィンドウサイズに追従させる。"""
        ov = getattr(self, "_min_overlay", None)
        if ov is not None:
            cw = self.centralWidget()
            if cw is not None:
                ov.setGeometry(0, 0, cw.width(), cw.height())
                ov.raise_()  # 念のため最前面を維持

    def _on_minimize_anim_done(self):
        """最小化アニメ完了時:
        1. showMinimized() で OS の最小化状態へ
        2. ジオメトリ・透明度を元に戻す(復元時にこの状態が見える)
        3. オーバーレイ破棄、再描画再開、フラグ解除
        """
        # OS の最小化を呼ぶ
        self.showMinimized()
        # 元のジオメトリ・透明度に戻す(タスクバーから復元した時にこの状態で出る)
        if hasattr(self, "_pre_minimize_geometry"):
            self.setGeometry(self._pre_minimize_geometry)
        if hasattr(self, "_pre_minimize_opacity"):
            self.setWindowOpacity(self._pre_minimize_opacity)
        else:
            self.setWindowOpacity(1.0)

        # _root_widget の再描画を再開
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(True)
            self._root_widget.update()

        # オーバーレイ破棄
        ov = getattr(self, "_min_overlay", None)
        if ov is not None:
            try:
                ov.hide()
                ov.setParent(None)
                ov.deleteLater()
            except Exception:
                pass
            self._min_overlay = None

        self._anim_in_progress = False

    def _animated_close(self):
        """閉じるボタン用: Windows標準と同様、その場でわずかに縮みながら
        フェードアウトしてから close() を呼ぶ。

        重要: dirty 確認 (未保存コメントの警告ダイアログ) は、フェードアウト
        アニメ「前」に行う必要がある。アニメ後の closeEvent 内で確認すると、
        ウィンドウは opacity=0 + 縮小で実質「消えた」状態になった後に
        ダイアログだけがデスクトップに浮いて見えるため。
        ここで確認 OK だった場合は _close_confirmed フラグを立て、
        後続の closeEvent では二重ダイアログを避けるためスキップする。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect
        if getattr(self, "_close_anim_started", False):
            return  # 二重起動防止

        # 未保存のコメントを先にノードへ反映してから dirty 確認
        if self._game_state is not None:
            try:
                self._save_comment_if_editing(self._game_state.current_node)
            except Exception:
                pass
        # 確認ダイアログ: キャンセルが選ばれたらアニメも何もしない
        if not self._confirm_discard_or_save():
            return
        # 続行確定: closeEvent 側での重複確認を防ぐフラグを立てる
        self._close_confirmed = True

        self._close_anim_started = True

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(300)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)

        # 中央に向けてわずかに縮小(98% → 軽い "縮む" 感)
        cur = self.geometry()
        target_w = int(cur.width() * 0.98)
        target_h = int(cur.height() * 0.98)
        target_geom = QRect(
            cur.x() + (cur.width() - target_w) // 2,
            cur.y() + (cur.height() - target_h) // 2,
            target_w, target_h,
        )
        shrink = QPropertyAnimation(self, b"geometry")
        shrink.setDuration(300)
        shrink.setStartValue(cur)
        shrink.setEndValue(target_geom)
        shrink.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QParallelAnimationGroup()
        group.addAnimation(fade)
        group.addAnimation(shrink)
        # アニメ完了後にウィンドウを閉じる
        group.finished.connect(lambda: super(MainWindow, self).close())
        self._close_anim = group  # GC対策
        group.start()

    def _animated_show_on_startup(self):
        """初回表示時のアニメ。
        windowOpacity のフェードインに加えて、ウィンドウ自体を 85%→100% に
        スケールイン(中心固定)することで「ふわっと拡大して現れる」演出を
        スプラッシュ画面と同じトーンで実現する。
        __init__ で setWindowOpacity(0.0) 済み。

        スケールについての注意:
        ・geometry を毎フレーム変更するため、初回起動の重い処理が
          終わった後にこの関数を呼ぶ前提(showEvent から QTimer.singleShot
          で 80ms 遅延済み)。
        ・最小サイズ(_MIN_WIN_OPEN_W/H 等)に近いウィンドウサイズだと、
          85% に縮めても minimum で押し戻されてアニメが効かない。
          その場合はスケールアニメを省略して opacity のみで表現する。

        中身フェードイン(QGraphicsOpacityEffect)は描画パイプライン常時負荷で
        カクつきの原因となるため使用しない。windowOpacity だけで全体フェードする。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect
        # アニメ中フラグ(_place_panels スキップ用)
        self._startup_anim_running = True

        # アニメ時間(スプラッシュ画面のフェードインと揃えて 400ms)
        ANIM_MS = 400

        # ウィンドウ全体のフェードイン(opacity)
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(ANIM_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(self._on_startup_anim_done)
        self._startup_anim = fade  # GC対策
        fade.start()

        # ── スケールイン(geometry を中心固定で 85%→100%) ────────
        # 現在のジオメトリを正規(終了)状態として保存し、開始時は
        # 中心位置を保ったまま 85% サイズに縮めてからアニメする。
        target_geom = self.geometry()
        scale_from = 0.85
        # 96% にしたサイズが minimum を下回るかチェック
        # (下回る場合は minimum で抑えられてアニメが効かないので、
        #  スケールアニメ自体を省略する)
        min_w = self.minimumWidth()
        min_h = self.minimumHeight()
        start_w = max(int(round(target_geom.width()  * scale_from)), min_w)
        start_h = max(int(round(target_geom.height() * scale_from)), min_h)
        # 実際の縮小率(minimum で押し戻された場合は target に近づく)
        actual_scale_w = start_w / target_geom.width()  if target_geom.width()  > 0 else 1.0
        actual_scale_h = start_h / target_geom.height() if target_geom.height() > 0 else 1.0
        # 縮小率が両方向で十分小さい(=スケールが視認できる)場合のみ実行
        if min(actual_scale_w, actual_scale_h) < 0.99:
            # 中心固定で配置
            cx_t = target_geom.center().x()
            cy_t = target_geom.center().y()
            start_geom = QRect(
                cx_t - start_w // 2,
                cy_t - start_h // 2,
                start_w, start_h,
            )
            # アニメ前に開始ジオメトリへ瞬時移動(描画ちらつき防止のため
            # opacity=0 のうちに行う)
            self.setGeometry(start_geom)
            scale_anim = QPropertyAnimation(self, b"geometry")
            scale_anim.setDuration(ANIM_MS)
            scale_anim.setStartValue(start_geom)
            scale_anim.setEndValue(target_geom)
            scale_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._startup_scale_anim = scale_anim  # GC対策
            scale_anim.start()

    def _on_startup_anim_done(self):
        """起動アニメ完了時の後処理: フラグ解除 + 必要なら _place_panels 1回。"""
        self._startup_anim_running = False
        # 念のため最終形状で 1 回再配置
        self._place_panels()

    def _cleanup_root_opacity_effect(self):
        """起動時アニメ完了後、_root_widget の QGraphicsOpacityEffect を解除する。
        QGraphicsEffect は設定したままだと描画パイプラインに常時オーバーヘッドが
        生じる(子ウィジェットの再描画ごとに合成処理が走る)ため、不要になった
        時点で setGraphicsEffect(None) で外す。
        """
        try:
            if hasattr(self, "_root_widget"):
                self._root_widget.setGraphicsEffect(None)
            self._root_opacity_effect = None
        except Exception:
            pass

    def _is_pseudo_maximized(self) -> bool:
        """自前最大化中かどうか。QPropertyAnimation 方式では _pseudo_max_active
        フラグで管理する(showMaximized は使わないので isMaximized() は常にFalse)。
        """
        return getattr(self, "_pseudo_max_active", False)

    def _set_win11_corner_round(self, rounded: bool):
        """Windows 11 のネイティブ角丸を切り替える。
        rounded=False(最大化中) → 矩形、rounded=True → 通常の角丸。
        Win10 や非Windowsでは何もしない(API呼び出しが無視されるだけ)。
        """
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            DWMWCP_DONOTROUND = 1
            hwnd = int(self.winId())
            value = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    def changeEvent(self, ev):
        """OSからウィンドウ状態が変わった場合の処理。
        ・自前最大化フラグに合わせてタイトルバーアイコン更新
        ・最小化状態から通常状態へ戻った瞬間に復元アニメを実行
        """
        from PyQt6.QtCore import QEvent
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, "_titlebar"):
                # 自前最大化フラグ優先で判定
                is_max = self._is_pseudo_maximized()
                self._titlebar.update_max_restore_icon(is_max)
            # 最小化からの復元を検知:
            # ev.oldState() に Minimized が含まれていて、現在は含まれていない
            try:
                old_minimized = bool(ev.oldState() & Qt.WindowState.WindowMinimized)
                cur_minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
                if old_minimized and not cur_minimized:
                    self._animated_show_from_minimized()
            except Exception:
                pass

    def _animated_show_from_minimized(self):
        """最小化からの復元時のカスタムアニメ。
        最大化アニメ・最小化アニメと同じ「不透明オーバーレイ + 内部再描画停止 +
        ウィンドウのジオメトリ/透明度をアニメ」方式。最小化アニメと完全に対称
        (逆再生)で、最小化で沈んだ位置から元位置へ上昇しながら現れる演出。

        フロー:
          1. ウィンドウを縮小+透明状態にいったんセット(元位置より下にオフセット)
          2. 不透明オーバーレイを最前面に被せ、ウィンドウ中身を隠蔽
          3. _root_widget.setUpdatesEnabled(False) で内部の再描画を停止
          4. windowOpacity 0.0→1.0 と geometry を「下から元位置へ上昇+拡大」で
             同時アニメ(200ms / OutCubic)
          5. 完了したらオーバーレイ破棄、内部再描画を再開
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect,
        )
        # アニメ中の二重起動を防止
        if getattr(self, "_anim_in_progress", False):
            return

        # ── 開始ジオメトリ・最終ジオメトリの計算 ──────────────────────
        # 最終位置(復元後の本来あるべき場所): 現在のジオメトリ
        final_geom = self.geometry()
        # 開始サイズ 75% スケール
        start_w = int(final_geom.width() * 0.75)
        start_h = int(final_geom.height() * 0.75)
        cx = final_geom.x() + (final_geom.width() - start_w) // 2
        cy = final_geom.y() + (final_geom.height() - start_h) // 2
        # 「下から上昇する」演出: 最小化で沈んだ位置(元位置より下 +300px)から
        # 元位置へ上昇しながら拡大する。最小化アニメと完全に対称(逆再生)。
        offset = 300  # px
        cy += offset
        start_geom = QRect(cx, cy, start_w, start_h)

        self._anim_in_progress = True

        # ── 不透明オーバーレイを作成(最大化・最小化アニメと同じ作り) ───
        from PyQt6.QtWidgets import QWidget
        cw = self.centralWidget()
        if cw is not None:
            overlay = QWidget(cw)
            overlay.setObjectName("min_anim_overlay")
            theme_bg = T().BG
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            overlay.setStyleSheet(
                f"#min_anim_overlay {{"
                f"  background-color: rgb({theme_bg.red()},{theme_bg.green()},{theme_bg.blue()});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )
            overlay.setGeometry(0, 0, cw.width(), cw.height())
            overlay.raise_()
            overlay.show()
            self._min_overlay = overlay
        else:
            self._min_overlay = None

        # ── 内部再描画を停止(最大化・最小化アニメと同じ) ──────────────
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(False)

        # ── ウィンドウを開始状態にセット ────────────────────────────────
        # 透明度 0、サイズ 75%、タスクバー方向にオフセット位置
        self.setWindowOpacity(0.0)
        self.setGeometry(start_geom)

        # ── アニメーション(200ms / OutCubic、最小化と対称) ────────────
        DURATION = 200

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(DURATION)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        grow = QPropertyAnimation(self, b"geometry")
        grow.setDuration(DURATION)
        grow.setStartValue(start_geom)
        grow.setEndValue(final_geom)
        grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        # フレームごとにオーバーレイをウィンドウサイズへ追従させる
        grow.valueChanged.connect(self._update_min_overlay_geometry)

        group = QParallelAnimationGroup()
        group.addAnimation(fade)
        group.addAnimation(grow)
        group.finished.connect(self._on_restore_anim_done)
        self._restore_anim = group  # GC対策で保持
        group.start()

    def _on_restore_anim_done(self):
        """最小化からの復元アニメ完了時:
        1. オーバーレイ破棄
        2. 内部再描画を再開
        3. アニメ中フラグ解除
        """
        # _root_widget の再描画を再開
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(True)
            self._root_widget.update()

        # オーバーレイ破棄
        ov = getattr(self, "_min_overlay", None)
        if ov is not None:
            try:
                ov.hide()
                ov.setParent(None)
                ov.deleteLater()
            except Exception:
                pass
            self._min_overlay = None

        self._anim_in_progress = False

    def _edge_at(self, pos: QPoint) -> Qt.Edge | None:
        """マウス位置がウィンドウ端のリサイズホットエリアにあれば、
        対応する Qt.Edge(複数辺なら OR 結合)を返す。なければ None。
        最大化中はリサイズ無効。
        """
        if self.isMaximized() or self._is_pseudo_maximized():
            return None
        b = self._RESIZE_BORDER
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        edge = Qt.Edge(0)
        if x <= b:
            edge |= Qt.Edge.LeftEdge
        elif x >= w - b:
            edge |= Qt.Edge.RightEdge
        if y <= b:
            edge |= Qt.Edge.TopEdge
        elif y >= h - b:
            edge |= Qt.Edge.BottomEdge
        # PyQt6 では Qt.Edge を int() に渡せないので .value で判定
        return edge if edge.value else None

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

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if getattr(self, "_startup_anim_running", False):
            return
        self._place_panels()

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


def _check_models_or_exit():
    """katago/models/ 直下の .bin.gz の個数をチェックする。

    ちょうど1個ならそのまま return。
    0個または複数個ならエラーダイアログを表示してアプリを終了する。
    QApplication が既に生成されている前提で呼ばれる。
    """
    from pathlib import Path
    from PyQt6.QtWidgets import QMessageBox

    models_dir = Path(MainWindow.KATAGO_DIR) / "models"
    abs_path = str(models_dir.absolute())
    found = MainWindow._scan_models()

    if len(found) == 1:
        return  # OK: ちょうど1個

    if len(found) == 0:
        # 0個: モデルが見つからない
        QMessageBox.critical(
            None,
            "AIモデルが見つかりません",
            "AIモデル（.bin.gz）が以下のフォルダに見つかりません。\n\n"
            f"{abs_path}\n\n"
            "AIモデルを配置してから再起動してください。",
        )
    else:
        # 複数個: どれを使うか決められない
        files_list = "\n".join(f"  ・{name}" for name in found)
        QMessageBox.critical(
            None,
            "複数のAIモデルが検出されました",
            "以下のフォルダに複数のAIモデル（.bin.gz）が配置されています。\n\n"
            f"{abs_path}\n\n"
            "使用するモデルを1つだけ配置してください。\n\n"
            "検出されたモデル:\n"
            f"{files_list}",
        )
    sys.exit(1)


# ── スプラッシュ画面 ─────────────────────────────────────────────────
class _SplashScreen(QWidget):
    """アプリ起動中に表示するスプラッシュ画面。
    フレームレスのウィンドウとして画面中央に表示される。
    KataGo エンジンの起動には数秒かかるため、その間ユーザーに
    「読み込み中である」ことを示すフィードバックを提供する。

    構成:
      ・上半分: 仮ロゴ領域(96x96) — 後ほど Kizuki アプリロゴに差し替え予定
      ・中段: アプリ名「Kizuki」テキスト
      ・下段: 回転スピナー + 「読み込み中...」テキスト

    使い方:
      splash = _SplashScreen()
      splash.show()
      app.processEvents()  # 描画を保証
      # ... 重い初期化処理 ...
      splash.close()
    """
    # サイズ・レイアウト定数
    WIDTH  = 480
    HEIGHT = 320
    LOGO_SIZE = 120  # 仮ロゴ領域(正方形)

    # アニメ時間
    _FADE_IN_MS  = 400   # 表示時のフェードイン時間
    _FADE_OUT_MS = 200   # 終了時のフェードアウト時間
    # スケールアニメ(フェードインと同期、中心からふわっと拡大)
    _SCALE_FROM = 0.85   # フェードイン開始時のスケール
    _SCALE_TO   = 1.00   # 通常表示時のスケール

    def __init__(self):
        super().__init__()
        # フレームレス + 常に最前面 + ツール扱い(タスクバーに出さない)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
        )
        # 角丸表示のための背景透過
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        # 画面中央に配置
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.WIDTH // 2,
            screen.center().y() - self.HEIGHT // 2,
        )

        # スピナー回転角度(0..359)
        self._spin_angle = 0
        # 回転アニメ: 0..359 を 0.9秒で1周、無限ループ
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._spin_anim = QVariantAnimation(self)
        self._spin_anim.setStartValue(0)
        self._spin_anim.setEndValue(360)
        self._spin_anim.setDuration(900)
        self._spin_anim.setLoopCount(-1)  # 無限ループ
        self._spin_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._spin_anim.valueChanged.connect(self._on_spin)
        self._spin_anim.start()

        # ── フェードイン/アウト用 ─────────────────────────────────
        # windowOpacity を直接操作する QPropertyAnimation。
        # 初期 opacity=0 で生成し、showEvent でフェードインを開始する。
        from PyQt6.QtCore import QPropertyAnimation
        self.setWindowOpacity(0.0)
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # フェードアウト完了後に本当に閉じるためのフラグ
        self._fading_out: bool = False
        self._fade_anim.finished.connect(self._on_fade_finished)

        # ── スケールアニメ用 ─────────────────────────────────────
        # ウィジェット自体のサイズは固定のまま、paintEvent 内で
        # コンテンツを中心スケール変換して「ふわっと拡大」を表現する。
        # フェードインと同期(同じ duration / easing)。
        self._scale = self._SCALE_FROM   # 現在のスケール値
        self._scale_anim = QVariantAnimation(self)
        self._scale_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scale_anim.valueChanged.connect(self._on_scale)

        # ── ロゴSVGの読み込み ────────────────────────────────────
        # スプラッシュは「マーク部分(K+石)」と「テキスト部分(Kizuki)」を
        # 別々に描画する。これにより両者の間隔を Python 側で自由に調整できる。
        # 配置: gui/assets/logo_mark_{light,dark}.svg
        #       gui/assets/logo_text_{light,dark}.svg
        self._mark_renderer = self._load_svg_renderer("logo_mark")
        self._text_renderer = self._load_svg_renderer("logo_text")

    def _on_scale(self, v):
        try:
            self._scale = float(v)
        except (TypeError, ValueError):
            self._scale = self._SCALE_TO
        self.update()

    def _load_svg_renderer(self, name: str) -> Optional["QSvgRenderer"]:
        """テーマに応じた SVG ファイルを読み込んで QSvgRenderer を返す。
        ファイルが見つからない/読み込み失敗時は None を返し、
        paintEvent はフォールバック表示で動作する。
        配置:
          gui/assets/{name}_light.svg(ライトテーマ用)
          gui/assets/{name}_dark.svg (ダークテーマ用)
        例: name="logo_mark" → logo_mark_light.svg / logo_mark_dark.svg
        """
        try:
            from pathlib import Path
            theme_mode = "dark" if T().BG.lightness() < 128 else "light"
            assets_dir = Path(__file__).parent / "assets"
            svg_path = assets_dir / f"{name}_{theme_mode}.svg"
            if not svg_path.exists():
                return None
            renderer = QSvgRenderer(str(svg_path))
            return renderer if renderer.isValid() else None
        except Exception:
            return None

    def _on_fade_finished(self):
        """フェードアニメ完了時のハンドラ。
        フェードアウト中に呼ばれた場合は実際にウィンドウを閉じる。"""
        if self._fading_out:
            # 実 close を呼ぶ(再度フェードアウトに入らないようフラグ済み)
            super().close()

    def showEvent(self, ev):
        """表示時にフェードインを開始する。"""
        super().showEvent(ev)
        # 既にアニメ中ならスキップ
        if self._fade_anim.state() == self._fade_anim.State.Running:
            return
        self._fading_out = False
        # フェードイン
        self._fade_anim.stop()
        self._fade_anim.setDuration(self._FADE_IN_MS)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        # スケールイン(フェードと同期)
        self._scale_anim.stop()
        self._scale_anim.setDuration(self._FADE_IN_MS)
        self._scale_anim.setStartValue(self._scale)
        self._scale_anim.setEndValue(self._SCALE_TO)
        self._scale_anim.start()

    def close(self):
        """フェードアウトしてから実際に閉じる。
        外部から splash.close() が呼ばれた時のエントリポイント。"""
        # 既にフェードアウト中なら何もしない
        if self._fading_out:
            return False
        self._fading_out = True
        # フェードアウト
        self._fade_anim.stop()
        self._fade_anim.setDuration(self._FADE_OUT_MS)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()
        # スケールアウト(同期、わずかに縮む)
        self._scale_anim.stop()
        self._scale_anim.setDuration(self._FADE_OUT_MS)
        self._scale_anim.setStartValue(self._scale)
        self._scale_anim.setEndValue(self._SCALE_FROM)
        self._scale_anim.start()
        # この時点ではまだ閉じない(アニメ完了で _on_fade_finished が super().close() を呼ぶ)
        return True

    def _on_spin(self, v):
        try:
            self._spin_angle = int(v) % 360
        except (TypeError, ValueError):
            self._spin_angle = 0
        self.update()

    def closeEvent(self, ev):
        # アニメを止めてからクローズ
        try:
            self._spin_anim.stop()
        except Exception:
            pass
        try:
            self._fade_anim.stop()
        except Exception:
            pass
        try:
            self._scale_anim.stop()
        except Exception:
            pass
        super().closeEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # ── スケール変換(中心スケール) ─────────────────────────────
        # ウィジェットサイズ自体は固定のまま、内部コンテンツを中心から
        # スケールすることで「ふわっと拡大」演出を行う。
        # 中心: (W/2, H/2)、スケール係数: self._scale(0.92〜1.00 で変動)
        if self._scale != 1.0:
            cx_w, cy_w = W / 2, H / 2
            p.translate(cx_w, cy_w)
            p.scale(self._scale, self._scale)
            p.translate(-cx_w, -cy_w)

        # ── 背景パネル(角丸) ─────────────────────────────────────
        # ダーク前提で T().BG を使用。境界線は薄く PANEL2 程度。
        panel_color  = T().BG
        border_color = T().PANEL2
        p.setPen(QPen(border_color, 1, Qt.PenStyle.SolidLine))
        p.setBrush(QBrush(panel_color))
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 12, 12)

        # ── ロゴSVG描画(マーク部分 + テキスト部分の2段) ──────────
        # マーク(K+石)を上に、テキスト(Kizuki)を下に、Python側で間隔調整。
        # SVG ファイル内の余白に依存しないため、見た目を Python で完結制御できる。
        cx = W / 2
        # 表示サイズの基準値
        # マーク: viewBox 264×238 (横長)、表示高さを基準に幅を比例計算
        # テキスト: viewBox 284×62 (横長)、表示幅を基準に高さを比例計算
        MARK_DISPLAY_H   = 80   # マーク表示高さ(px)
        TEXT_DISPLAY_W   = 120  # テキスト表示幅(px)
        GAP_MARK_TO_TEXT = 16   # マーク下端 → テキスト上端 の間隔(px)
        # マーク表示寸法(viewBox 264×238 のアスペクト比を維持)
        mark_w = MARK_DISPLAY_H * (264.0 / 238.0)
        mark_h = MARK_DISPLAY_H
        # テキスト表示寸法(viewBox 284×62 のアスペクト比を維持)
        text_w = TEXT_DISPLAY_W
        text_h = TEXT_DISPLAY_W * (62.0 / 284.0)
        # ブロック全体の縦方向中央寄せ(スピナー領域分やや上寄せ)
        block_h = mark_h + GAP_MARK_TO_TEXT + text_h
        block_top = (H - block_h) / 2 - 28  # スピナー領域分やや上寄せ

        if self._mark_renderer is not None:
            mark_rect = QRectF(cx - mark_w / 2, block_top, mark_w, mark_h)
            self._mark_renderer.render(p, mark_rect)
        if self._text_renderer is not None:
            text_top = block_top + mark_h + GAP_MARK_TO_TEXT
            text_rect = QRectF(cx - text_w / 2, text_top, text_w, text_h)
            self._text_renderer.render(p, text_rect)
        # フォールバック(両SVG読み込み失敗時): 仮ロゴ(石2個) + テキスト描画
        if self._mark_renderer is None and self._text_renderer is None:
            logo_top = 48
            stone_r = 28
            bx = cx - 13
            wx = cx + 13
            sy = logo_top + self.LOGO_SIZE / 2
            p.setPen(QPen(T().STONE_BORDER_BLACK, 1))
            p.setBrush(QBrush(T().STONE_BLACK))
            p.drawEllipse(QPointF(bx, sy), stone_r, stone_r)
            p.setPen(QPen(T().STONE_BORDER_WHITE, 1))
            p.setBrush(QBrush(T().STONE_WHITE))
            p.drawEllipse(QPointF(wx, sy), stone_r, stone_r)
            # アプリ名テキスト
            p.setPen(QPen(T().TEXT))
            p.setFont(Font_XL(True))
            fm = p.fontMetrics()
            name = "Kizuki"
            nw = fm.horizontalAdvance(name)
            name_y = logo_top + self.LOGO_SIZE + 24 + fm.ascent()
            p.drawText(QPointF((W - nw) / 2, name_y), name)

        # ── スピナー + ステータステキスト ─────────────────────────
        status_text = "読み込み中..."
        p.setFont(Font_XS())
        fm2 = p.fontMetrics()
        sw = fm2.horizontalAdvance(status_text)
        spin_size = 14
        gap = 10  # スピナーとテキストの間隔
        total_w = spin_size + gap + sw
        # 配置基準 y(視覚中心): カード下端から余白を取る
        bottom_center_y = H - 40   # 下端からの余白(WIDTH/HEIGHT拡大に伴い増量)
        # スピナー描画(左)
        sx = (W - total_w) / 2
        sy_spin = bottom_center_y - spin_size / 2
        # ベース円(全周、薄め)
        p.setPen(QPen(T().PANEL2, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        spin_rect = QRectF(sx, sy_spin, spin_size, spin_size)
        p.drawEllipse(spin_rect.adjusted(1, 1, -1, -1))
        # 回転する円弧(90度ぶん)
        # QPainter.drawArc の角度は 1/16 度単位、反時計回りが正
        # アニメ値 _spin_angle は 0..359、時計回りに見せたいので -angle
        p.setPen(QPen(T().TEXT2, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        start_angle = (-self._spin_angle) * 16
        span_angle  = -90 * 16  # 時計回りに90度ぶん
        p.drawArc(spin_rect.adjusted(1, 1, -1, -1).toRect(), start_angle, span_angle)
        # ステータステキスト(右)
        p.setPen(QPen(T().TEXT2))
        text_x = sx + spin_size + gap
        text_y = bottom_center_y + (fm2.ascent() - fm2.descent()) / 2
        p.drawText(QPointF(text_x, text_y), status_text)

        p.end()


def main():
    logging.basicConfig(level=logging.INFO,format="%(name)s %(levelname)s %(message)s")

    # ── DPI・スケーリング設定（QApplication生成前に設定）──
    import os
    os.environ.setdefault("QT_FONT_DPI", "96")

    # 高DPI対応
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app=QApplication(sys.argv)
    app.setApplicationName("囲碁AI解析")
    app.setStyle("Fusion")

    # ── テーマ復元(スプラッシュ表示前に行う) ─────────────────────
    # スプラッシュ画面は T().BG などのテーマトークンを参照して描画するため、
    # MainWindow 生成より前にユーザー設定のテーマを復元しておく必要がある。
    # MainWindow.__init__ 内でも同じ復元処理を行うが、二重実行しても害はない。
    from PyQt6.QtCore import QSettings
    _saved_theme = QSettings("Kizuki", "Kizuki").value("theme", "dark", type=str)
    if _saved_theme not in ("dark", "light"):
        _saved_theme = "dark"
    _theme.set_mode(_saved_theme)

    # ── スプラッシュ画面を即時表示 ─────────────────────────────────
    # KataGoエンジンの起動はモデルロード等で数秒かかるため、ユーザーに
    # 「アプリは起動中である」というフィードバックを早期に出す。
    splash = _SplashScreen()
    splash.show()
    # 描画を確実に行うため processEvents で1サイクル回す
    # (アニメも初期フレームが描画される)
    app.processEvents()

    # ── KataGo モデル数チェック ─────────────────────────────────
    # katago/models/ 直下の .bin.gz がちょうど1個あることを確認する。
    # 0個 / 複数の場合はユーザーにエラーダイアログを表示してアプリを終了する。
    _check_models_or_exit()

    # ── KataGoエンジンをワーカースレッドで起動 ─────────────────
    # start() は _ready_event.wait(timeout=600) でブロックするため、
    # メインスレッドで呼ぶとイベントループが止まりスプラッシュのアニメが
    # フリーズする。バックグラウンドスレッドに送ることで、メインスレッドは
    # イベントループを回し続けてフェードイン+スピナー回転を継続できる。
    #
    # KataGoEngine 自体は内部でスレッドを使う設計でスレッドセーフ。
    # ・__init__ は属性設定だけで I/O なし → メインスレッドで生成OK
    # ・start() は subprocess.Popen + _ready_event.wait() → ワーカーへ
    engine = _build_startup_engine()
    worker = _EngineStartupWorker(engine)

    # メインスレッドのイベントループでワーカー完了を待つ
    # (この間 splash のアニメは正常に動き続ける)
    from PyQt6.QtCore import QEventLoop
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    worker.start()
    loop.exec()

    # ── ワーカーのエラーチェック ─────────────────────────────────
    if worker.error is not None:
        # KataGo起動失敗(タイムアウトなど): スプラッシュを閉じてダイアログ表示
        splash.close()
        app.processEvents()
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "起動エラー",
            f"KataGoエンジンの起動に失敗しました。\n\n{worker.error}",
        )
        sys.exit(1)

    # ── メインウィンドウ生成(起動済みエンジンを受け取る) ──────────
    # MainWindow.__init__ は重い処理(ウィジェット生成・配置)が含まれるが、
    # __init__ 内部の節目で QApplication.processEvents() を呼ぶようにして
    # あるため、スプラッシュ画面のスピナーアニメは継続的に進む。
    app.processEvents()
    w = MainWindow(engine=engine)
    app.processEvents()
    w.show()
    app.processEvents()

    # スプラッシュを閉じるタイミング:
    # メインウィンドウのフェードイン(初回起動アニメ)中もスピナーが見えて
    # ほしいので、即時 close ではなく少し遅らせる。遅延中も
    # app.exec() のイベントループでスピナーは動き続ける。
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(150, splash.close)

    sys.exit(app.exec())


def _build_startup_engine() -> "KataGoEngine":
    """起動時の KataGoEngine インスタンスを生成する(start() はまだ呼ばない)。
    MainWindow._create_engine と同じロジックだが、main() 段階では
    MainWindow がまだ存在しないので、独立した関数として定義する。
    モデル数の事前チェックは _check_models_or_exit() が済ませている前提。
    """
    from pathlib import Path
    katago_dir = Path(MainWindow.KATAGO_DIR)
    models_dir = katago_dir / "models"
    # ちょうど1個ある前提(_check_models_or_exit でチェック済み)
    model_files = sorted(p.name for p in models_dir.glob("*.bin.gz"))
    model_path = models_dir / model_files[0]
    return KataGoEngine(
        executable=str(katago_dir / "katago.exe"),
        model=str(model_path),
        config=str(katago_dir / "analysis.cfg"),
        human_model="",
        board_size=19, komi=6.5,
    )


class _EngineStartupWorker(QThread):
    """KataGoEngine.start() をバックグラウンドスレッドで実行するワーカー。

    KataGoEngine.start() は内部で _ready_event.wait(timeout=600) によって
    モデルロード完了まで同期ブロックする。これをメインスレッドで呼ぶと
    イベントループが止まり、スプラッシュ画面のアニメ(フェードイン・
    スピナー回転)がフリーズしてしまう。

    そのため start() のみを別スレッドに送り、メインスレッドは
    QEventLoop で完了を待ちつつイベント処理を継続する。

    エラーは self.error に格納し、finished シグナル後に main() で確認する。
    """
    def __init__(self, engine: "KataGoEngine"):
        super().__init__()
        self._engine = engine
        self.error: Optional[str] = None

    def run(self):
        try:
            self._engine.start()
        except Exception as e:
            self.error = str(e)


if __name__=="__main__":
    main()
