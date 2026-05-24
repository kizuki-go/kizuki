"""
gui/widgets/branchtree.py — 分岐ツリーウィジェットと端フェードオーバーレイ。

依存: gui.theme, gui.fonts, gui.infra, core.sgf_parser, core.game_state, PyQt6.
style_qmenu (現状 main_window.py、Phase 4 で gui.menus に移動) は
contextMenu 内で lazy import している。

提供:
- _TreeEdgeFadeOverlay: viewport 端のフェードオーバーレイ (4方向対応)
- BranchTreeWidget: 棋譜の分岐ツリー (描画、ノードクリック、スクロール)
"""
from __future__ import annotations
import time
from typing import Optional

from PyQt6.QtWidgets import QWidget, QScrollArea, QMenu
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QSize, QEvent
from PyQt6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QPixmap,
    QLinearGradient, QFontMetrics,
)
from PyQt6.QtCore import QVariantAnimation, QEasingCurve

from gui.theme import T, EVAL_COLORS
from gui.fonts import F, FontMono_XS
from gui.infra import _profile, _profile_method
from core.sgf_parser import SGFNode
from core.game_state import GameState


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
        # Phase 4: replace with rom gui.menus import style_qmenu
        from gui.main_window import style_qmenu
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

