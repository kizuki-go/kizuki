"""
gui/widgets/graph.py — 勝率・目差グラフと関連ウィジェット。

依存: gui.theme, gui.fonts, PyQt6, pyqtgraph(optional)
pyqtgraph が無い環境では pg=None とフォールバックし、グラフは無効化される。

提供:
- ScoreLeadAxis / IntegerBottomAxis: カスタム軸 (目差・手数)
- _GraphLabelOverlay: グラフ上に重ねる目差ラベル
- WinRateGraph: 勝率折れ線 + 目差エリア + 各種マーカー
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPointF, QEvent, QTimer,
    QVariantAnimation, QEasingCurve,
)
from PyQt6.QtGui import QPainter

try:
    import pyqtgraph as pg
except ImportError:
    pg = None

from gui.theme import T, SP_XS, SP_XL
from gui.fonts import F, Font_XS, FontMono_XS, FontMono_SM
from gui.infra import _profile, _profile_method


class ScoreLeadAxis(pg.AxisItem if pg else object):
    """目差グラフ用カスタム軸：±N と 0 の3点のみ表示。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._y_range = 5

    def set_y_range(self, y_range: int):
        self._y_range = y_range
        self.picture = None
        self.update()

    def tickValues(self, minVal, maxVal, size):
        return [(self._y_range * 2, [self._y_range, 0, -self._y_range])]

    def tickStrings(self, values, scale, spacing):
        # 縦軸の数値は非表示
        return [""] * len(values)


class IntegerBottomAxis(pg.AxisItem if pg else object):
    """目差グラフの手数軸：整数のみ表示（小数は抑制）。"""
    def tickValues(self, minVal, maxVal, size):
        # デフォルトの tick 候補を取得し、整数刻みに丸めて重複除去する
        ticks = super().tickValues(minVal, maxVal, size)
        result = []
        for spacing, values in ticks:
            int_values = sorted({int(round(v)) for v in values
                                 if minVal <= v <= maxVal})
            if int_values:
                # spacing も整数化（最小1）
                int_spacing = max(1, int(round(spacing)))
                result.append((int_spacing, int_values))
        return result

    def tickStrings(self, values, scale, spacing):
        # 整数として描画（小数点以下は表示しない）
        return [str(int(round(v))) for v in values]


# ── グラフラベルオーバーレイ ────────────────────────────────────────────────
class _GraphLabelOverlay(QWidget):
    """PlotWidget の上に重ねて目差ラベルと Y軸ラベルを描画する透明ウィジェット。"""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._score: Optional[float] = None
        self._px: float = 0
        self._py: float = 0
        # Y軸ラベル用: [(y_px, label_text), ...]
        self._y_labels: list[tuple[float, str]] = []

    def set_label(self, score: Optional[float], px: float, py: float):
        self._score = score
        self._px = px
        self._py = py
        self.update()

    def set_y_labels(self, labels: list[tuple[float, str]]):
        """Y軸ラベルを設定する。labels = [(y座標px, テキスト), ...]"""
        self._y_labels = labels
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Y軸ラベルは非表示（縦軸数値を廃止）

        # スコアラベルは WinRateGraph 上部の QLabel で表示

        p.end()


# ── Win rate graph ──────────────────────────────────────────────────────────
class WinRateGraph(QWidget):
    # クリック/ドラッグで手を変更したときに発火するシグナル
    # move_dragged  : 押下中（盤面表示のみ更新、ポンダリングなし）
    # move_released : 押下完了（確定位置でポンダリング再開）
    move_dragged  = pyqtSignal(int)
    move_released = pyqtSignal(int)

    # 現在手の縦線・スコアラベルのアニメーション設定
    _CUR_ANIM_DURATION_MS = 300  # アニメ時間(ms)

    def __init__(self):
        super().__init__()

        # ======================================================================
        # Phase 7: _setup() / set_data_sparse() で後から代入される子要素を
        # 事前に None で置く。これにより hasattr() ガードを `is not None`
        # で置き換えられる。
        # ======================================================================
        self._overlay = None
        self._pw = None
        self._score_label = None

        self.score_leads = []
        self.current_move = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(150)
        self._use_pg = False
        # クリックジャンプ用
        self._total_moves = 0   # X軸の最大手数（ジャンプ範囲のクランプに使う）
        self._clicks_enabled = True  # set_clicks_enabled() で無効化可能
        self._dragging = False
        # 押下時の手数 index を記録。MouseMove 中に別の idx に変化したら
        # 「ドラッグ」と判定して即時追従、変化しないままリリースされたら
        # 「クリック」と判定してアニメで遷移する。
        self._press_idx: Optional[int] = None
        # 現在手アニメ用: 目標値と表示値を分離
        self._cur_target_idx = 0
        self._cur_target_score = None  # None = ハイフン or 非表示
        self._cur_display_idx = 0
        self._cur_display_score = None
        self._cur_anim = None  # QVariantAnimation(遅延生成)
        self._setup()

    def _setup(self):
        if pg is None: return
        pg.setConfigOptions(antialias=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── スコアラベル（絶対配置・縦線X位置に追従、Y=上端固定）──
        # PlotWidget の上に 28px (SP_XL + SP_XS) のマージンを設け、
        # スコアラベル（漢字:12px / 数字:14px）と青い縦棒の上端の間に余白を確保する。
        layout.setContentsMargins(0, SP_XL + SP_XS, 0, 0)

        self._score_label = QLabel("—", self)
        self._score_label.setFont(Font_XS(True))
        self._score_label.setTextFormat(Qt.TextFormat.RichText)
        # setFixedHeight は使わない: CSS padding による上下均等配置を優先し、
        # 高さは adjustSize() の結果に任せる
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._score_label.setStyleSheet(
            f"color:{T().TEXT2.name()}; background:transparent;")
        self._score_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._score_label.raise_()

        self._left_ax = ScoreLeadAxis(orientation="left")
        self._left_ax.setTextPen(pg.mkPen(color=T().TEXT2.name()))
        # 左軸の縦線は非表示（目盛り値のみ表示）。pen=None で軸線描画を抑制。
        self._left_ax.setPen(None)
        self._left_ax.setStyle(
            tickFont=FontMono_SM(),
            tickTextWidth=48,
            tickTextOffset=6,
        )
        self._left_ax.setWidth(8)

        # 整数のみ表示するX軸
        self._bottom_ax = IntegerBottomAxis(orientation="bottom")

        pw = pg.PlotWidget(axisItems={"left": self._left_ax, "bottom": self._bottom_ax})
        pw.setBackground(T().PANEL.name())
        pw.showGrid(x=False, y=True, alpha=0.06)
        pw.getAxis("bottom").setTextPen(pg.mkPen(color=T().TEXT2.name()))
        pw.getAxis("bottom").setPen(pg.mkPen(color=T().BORDER2.name()))
        pw.getAxis("bottom").setStyle(
            tickFont=FontMono_XS(),
            tickTextOffset=4,
            tickLength=0,  # 短い縦棒(tick mark)を非表示にする
        )

        # ユーザー操作によるズーム・パンを完全無効化
        pw.setMouseEnabled(x=False, y=False)
        pw.hideButtons()
        pw.getPlotItem().getViewBox().setMenuEnabled(False)

        # 0基準線
        self._ref = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen(color=T().GRAPH_AXIS, style=Qt.PenStyle.SolidLine, width=1))
        pw.addItem(self._ref)

        # 黒有利（y > 0）：塗り＋線を同一カーブで描画
        self._curve_black = pw.plot(
            pen=pg.mkPen(color=T().GRAPH_AXIS, width=1),
            fillLevel=0,
            brush=pg.mkBrush(T().GRAPH_BLACK),
        )

        # 白有利（y < 0）：塗り＋線を同一カーブで描画
        # 黒カーブと同じく GRAPH_AXIS で線を引き、塗り(GRAPH_WHITE)との
        # 境界を視認できるようにする(色の統一性と視認性を両立)。
        self._curve_white = pw.plot(
            pen=pg.mkPen(color=T().GRAPH_AXIS, width=1),
            fillLevel=0,
            brush=pg.mkBrush(T().GRAPH_WHITE),
        )

        # 現在手の縦線（現在位置 = 選択中）
        # 色は T().TEXT を使い、ダーク=#ffffff / ライト=#333333 で
        # メインテキスト色と一致させる(モード依存で自動追従)。
        # width=1 は整数化のため。1.5 等の非整数だと描画位置(サブピクセル)
        # によって 1px / 2px のブレが発生し、手数によって縦棒の太さが変わって
        # 見える(同じ pen でも x が .5 付近だとアンチエイリアスで滲み太く見える)。
        self._cur_line = pg.InfiniteLine(
            pos=0, angle=90,
            pen=pg.mkPen(color=T().TEXT.name(), width=1))
        pw.addItem(self._cur_line)

        self._pw = pw
        self._y_range = 5
        pw.setYRange(-5, 5, padding=0)
        pw.setLimits(yMin=-220, yMax=220)
        layout.addWidget(pw)
        self._use_pg = True

        # ラベル描画用の状態変数
        self._label_score: Optional[float] = None
        self._label_x_idx: int = 0

        # PlotWidget の上に重ねる透明オーバーレイ
        self._overlay = _GraphLabelOverlay(pw)
        self._overlay.setGeometry(pw.rect())
        self._overlay.raise_()

        # ── マウスクリック/ドラッグで手数ジャンプ ──
        # カーソル形状をポインターにして押下可能であることを示す
        pw.setCursor(Qt.CursorShape.PointingHandCursor)
        # PlotWidget の viewport に eventFilter を入れて押下/移動/解放を捕捉
        pw.viewport().installEventFilter(self)

        # レイアウト確定後に Y軸ラベルを初期描画
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, self._update_y_labels)

    # ── クリック/ドラッグ処理 ──────────────────────────────────────────
    def set_clicks_enabled(self, enabled: bool):
        """クリックジャンプの有効/無効を切り替える。"""
        self._clicks_enabled = enabled
        if self._use_pg:
            self._pw.setCursor(
                Qt.CursorShape.PointingHandCursor if enabled
                else Qt.CursorShape.ArrowCursor
            )

    def _x_to_move_idx(self, scene_pos) -> Optional[int]:
        """PlotWidget の viewport 座標から最寄りの手数インデックスを返す。

        範囲外クリックでも 0〜total_moves にクランプして返す。
        """
        if not self._use_pg:
            return None
        vb = self._pw.getPlotItem().getViewBox()
        # viewport 座標 → scene 座標 → view 座標
        scene_pt = self._pw.mapToScene(scene_pos)
        view_pt = vb.mapSceneToView(scene_pt)
        x = view_pt.x()
        idx = int(round(x))
        # 0 〜 total_moves にクランプ（_total_moves が未設定ならデータ範囲を使う）
        xy_map = getattr(self, "_xy_map", {})
        x_max = max(self._total_moves, max(xy_map.keys()) if xy_map else 0)
        idx = max(0, min(idx, x_max))
        return idx

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if not self._use_pg or not self._clicks_enabled:
            return super().eventFilter(obj, ev)
        if obj is not self._pw.viewport():
            return super().eventFilter(obj, ev)

        et = ev.type()
        if et == QEvent.Type.MouseButtonPress:
            if ev.button() == Qt.MouseButton.LeftButton:
                idx = self._x_to_move_idx(ev.position().toPoint())
                if idx is not None:
                    # 押下時点では「クリック」か「ドラッグ」か不明なので、
                    # idx を記録するだけで盤面更新は行わない。
                    # MouseMove で別の idx に変わったら _dragging=True にして
                    # その時点から即時追従、変わらないままリリースされたら
                    # 「クリック」として move_released でアニメ遷移する。
                    self._press_idx = idx
                    self._dragging = False
                return True
        elif et == QEvent.Type.MouseMove:
            # 押下中のみ反応（ホバー移動は無視）
            if self._press_idx is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
                idx = self._x_to_move_idx(ev.position().toPoint())
                if idx is not None:
                    if not self._dragging:
                        # まだドラッグ判定していない: 押下位置と異なる idx に
                        # 動いた時点でドラッグへ移行(即時追従モード)。
                        if idx != self._press_idx:
                            self._dragging = True
                            self.move_dragged.emit(idx)
                    else:
                        # 既にドラッグ中: 通常の即時追従
                        self.move_dragged.emit(idx)
                return True
        elif et == QEvent.Type.MouseButtonRelease:
            if ev.button() == Qt.MouseButton.LeftButton and self._press_idx is not None:
                idx = self._x_to_move_idx(ev.position().toPoint())
                was_dragging = self._dragging
                press_idx = self._press_idx
                self._dragging = False
                self._press_idx = None
                if idx is not None:
                    # ドラッグ後: 既にドラッグ中で位置は追従済み → 確定のみ
                    # クリック: 押下時点で更新していないので、ここでアニメ遷移
                    # どちらも move_released でジャンプ確定 + ポンダリング再開
                    self.move_released.emit(idx)
                elif not was_dragging:
                    # クリックだが idx 取得失敗(範囲外等): press_idx で確定
                    self.move_released.emit(press_idx)
                return True
        return super().eventFilter(obj, ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._overlay is not None and self._pw is not None:
            self._overlay.setGeometry(self._pw.rect())
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, self._update_y_labels)

    def _update_y_labels(self):
        """Y軸の 黒+N / 0 / 白+N ラベル座標を計算してオーバーレイに渡す。"""
        if self._overlay is None or self._pw is None:
            return
        vb = self._pw.getPlotItem().getViewBox()
        labels = []
        for val, text in [
            ( self._y_range, f"黒+{self._y_range}"),
            (-self._y_range, f"白+{self._y_range}"),
        ]:
            scene_pt = vb.mapViewToScene(QPointF(0, val))
            local_pt = self._pw.mapFromScene(scene_pt)
            labels.append((local_pt.y(), text))
        self._overlay.set_y_labels(labels)

    def set_total_moves(self, total: int):
        """棋譜読み込み時に横軸の最大値を設定する。
        末尾追従: 直前まで縦線(_cur_display_idx)が末尾(=旧 _total_moves)に
        いて、かつ範囲が拡大した場合、X軸範囲拡大に合わせて縦線も新末尾へ
        瞬時にスナップする。これにより最新手追加時の「縦線が一瞬左にずれて
        右にスライド」を防ぐ。
        範囲が縮小した場合(例: サブ分岐に入る)は _cur_display_idx を維持し、
        後続の set_current_move でアニメ補間させる(ハンドルが動かないように
        見えるのを防ぐ)。"""
        old_total = self._total_moves
        new_total = max(total, 0)
        # 末尾追従判定: 表示位置が旧末尾と等しい かつ 範囲が拡大した
        was_at_end_and_grew = (
            new_total > old_total
            and abs(self._cur_display_idx - float(old_total)) < 1e-6
        )
        self._total_moves = new_total
        if not self._use_pg: return
        x_max = max(total, 1)
        vb = self._pw.getPlotItem().getViewBox()
        # setLimits は前回値が残って制約になるため使わない
        # disableAutoRange 後に setXRange で直接表示範囲を指定する
        vb.disableAutoRange(vb.XAxis)
        self._pw.setXRange(0, x_max, padding=0)
        # 末尾追従(拡大時のみ): 進行中の縦線アニメを止めて表示位置を新末尾へ
        if was_at_end_and_grew:
            self._stop_cur_anim()
            self._cur_display_idx = float(new_total)
            self._cur_target_idx = new_total
            self._apply_cur_display()

    def _apply_y_range(self, values: list[float]):
        """データの最大絶対値から Y 軸範囲を計算して適用する。値が変わった時のみ更新。
        データ最大値が y_range の 80% 位置に来るよう逆算することで、
        曲線と上下軸枠との間に余白を確保する。"""
        max_abs = max((abs(v) for v in values), default=5)
        # データ最大値が y_range の 80% 位置に来るよう逆算 (= max_abs / 0.80)
        # +2 は数値の切り捨てによる余裕削れの防止と、データ最小時の最低限の余白確保。
        y_range = max(5, min(int(max_abs / 0.80) + 2, 200))
        if y_range == self._y_range:
            return  # 変化なければ再描画しない（縦線太さのちらつき防止）
        self._y_range = y_range
        self._left_ax.set_y_range(y_range)
        self._pw.setYRange(-y_range, y_range, padding=0)
        self._left_ax.update()
        self._update_y_labels()

    def clear_data(self):
        """棋譜切替時にグラフデータをリセットする。"""
        self.score_leads = []
        self._label_score = None
        self._xy_map = {}
        if self._use_pg:
            self._curve_black.setData([], [])
            self._curve_white.setData([], [])
        if self._score_label is not None:
            self._score_label.hide()
        self.update()

    @staticmethod
    def _zero_cross_interpolate(xs: list, ys: list) -> tuple[list, list]:
        """ゼロクロスする区間に (x_cross, 0.0) を挿入して返す。

        黒カーブ (y≥0) と白カーブ (y≤0) を fillLevel=0 で塗る構造では、
        符号をまたぐ区間で 0 ラインとの交点を明示的に入れないと、
        塗りが不自然に重なる（両方＋側に出るなど）。
        線形補間で 0 クロス点を計算して折れ線に挿入する。
        """
        if not xs:
            return [], []
        xs_aug: list = []
        ys_aug: list = []
        for i, (x, y) in enumerate(zip(xs, ys)):
            if i > 0:
                prev_x, prev_y = xs[i-1], ys[i-1]
                # 符号が変化し、かつどちらも非ゼロなら 0 クロス点を挿入
                if prev_y * y < 0:
                    t = prev_y / (prev_y - y)  # 0〜1
                    x_cross = prev_x + t * (x - prev_x)
                    xs_aug.append(x_cross)
                    ys_aug.append(0.0)
            xs_aug.append(x)
            ys_aug.append(y)
        return xs_aug, ys_aug

    @_profile_method("Graph.set_data_sparse")
    def set_data_sparse(self, xs: list[int], ys: list[float], total: int):
        """解析済み手だけの (x, y) ペアでグラフを更新する。

        X軸範囲は total（棋譜全体の手数）に固定する。
        xs の最大値に引きずられて伸びないようにするため、
        「先の手にジャンプして途中までしか解析データがない」場合でも
        X軸スケールが安定する。

        ゼロクロス処理:
        黒カーブ (y≥0) と白カーブ (y≤0) を fillLevel=0 で塗る構造上、
        符号をまたぐ区間では 0 ラインとの交点で折れ線を分割する必要がある。
        これを怠ると「黒塗りと白塗りが同じ区間で重なって両方＋側に見える」
        などの描画不具合が発生するため、本メソッド内で
        ・xs でソート
        ・符号がまたぐ区間はゼロクロス点を補間して挿入
        を行う。
        """
        if not self._use_pg or not xs: return

        # ── xs 昇順でソート（分岐上のノードが混在すると順序が崩れる場合あり）──
        pairs = sorted(zip(xs, ys), key=lambda p: p[0])
        xs_s = [p[0] for p in pairs]
        ys_s = [p[1] for p in pairs]

        # ── 初期局面 (x=0, y=0) を先頭に挿入：左端の隙間を埋め、
        #     0手目クリックで初期局面ジャンプも可能にする ──
        if not xs_s or xs_s[0] != 0:
            xs_s.insert(0, 0)
            ys_s.insert(0, 0.0)

        # ── ゼロクロス補間で符号変化点を挿入 ──
        xs_aug, ys_aug = self._zero_cross_interpolate(xs_s, ys_s)

        # ── fillLevel=0 で塗るため、黒用 / 白用にクリップ ──
        ys_black = [max(0.0, v) for v in ys_aug]
        ys_white = [min(0.0, v) for v in ys_aug]
        self._curve_black.setData(xs_aug, ys_black)
        self._curve_white.setData(xs_aug, ys_white)

        # 元の (xs, ys) で xy_map を作る（補間点は除外、初期点 x=0 は含む）
        self._xy_map = dict(zip(xs_s, ys_s))
        self._apply_y_range(ys_s)

        # X軸範囲は棋譜全体に固定（xs の範囲外でも伸ばさない）
        # set_total_moves を経由して末尾追従ロジックを通す(直前末尾にいた場合、
        # 範囲拡大に合わせて縦線も瞬時に新末尾へスナップ)。
        self.set_total_moves(total)

    def update_theme(self):
        """テーマ切り替え時に pyqtgraph の色を再適用する。"""
        if not self._use_pg:
            return
        self._pw.setBackground(T().PANEL.name())
        # 黒番カーブ: 塗り + 線
        self._curve_black.setBrush(pg.mkBrush(T().GRAPH_BLACK))
        self._curve_black.setPen(pg.mkPen(color=T().GRAPH_AXIS, width=1))
        # 白番カーブ: 塗り + 線(線は黒カーブと同じく GRAPH_AXIS で統一)
        self._curve_white.setBrush(pg.mkBrush(T().GRAPH_WHITE))
        self._curve_white.setPen(pg.mkPen(color=T().GRAPH_AXIS, width=1))
        self._cur_line.setPen(pg.mkPen(color=T().TEXT.name(), width=1))
        self._ref.setPen(pg.mkPen(color=T().GRAPH_AXIS, style=Qt.PenStyle.SolidLine, width=1))
        self._left_ax.setTextPen(pg.mkPen(color=T().TEXT2.name()))
        # 左軸の縦線は非表示維持
        self._left_ax.setPen(None)
        self._pw.getAxis("bottom").setTextPen(pg.mkPen(color=T().TEXT2.name()))
        self._pw.getAxis("bottom").setPen(pg.mkPen(color=T().BORDER2.name()))
        # スコアラベル: スコア確定済みならバッジスタイルを再適用、
        # 未確定（"—"表示）なら初期スタイルに戻す
        s = getattr(self, "_label_score", None)
        if s is not None:
            if s >= 0:
                # 黒側ラベル背景は GRAPH_BLACK と同期(色・α一致)
                gb = T().GRAPH_BLACK
                bg = f"rgba({gb.red()},{gb.green()},{gb.blue()},{gb.alpha()})"
                fg = "#ffffff"
                border = T().SCORE_LABEL_BORDER_BLACK.name()
            else:
                # 白側ラベル: グラフ白塗りと同じ GRAPH_WHITE で背景を同色化(無彩色)。
                # ボーダーも無彩色の SCORE_LABEL_BORDER_WHITE に揃え、
                # 黒側 (SCORE_LABEL_BORDER_BLACK) と対称な設計にしている。
                # 文字色は勝率バー白側 (ScoreBoard) と同じく
                # ダーク=#1e1e1e / ライト=#333333 で統一する。
                bg = T().GRAPH_WHITE.name()
                fg = "#1e1e1e" if T().is_dark else "#333333"
                border = T().SCORE_LABEL_BORDER_WHITE.name()
            # 完全ピル形を保つため 2 パスで適用する。
            # (1パス目で adjustSize() し、2パス目で height/2 を radius に使う)
            # 1 パスで border-radius を固定値にすると、ラベル高さに対して
            # 角丸が小さくなり、テーマ切替時だけピルが角丸長方形になってしまう
            # (グラフ移動時は _apply_cur_display が同じ 2 パスで再適用するため
            # 完全ピル形に戻るが、テーマ切替直後だけ崩れて見える)。
            self._score_label.setStyleSheet(
                f"QLabel {{ color:{fg}; background:{bg}; "
                f"padding:1px 8px; border-radius:0px; "
                f"border:1px solid {border}; }}")
            self._score_label.adjustSize()
            radius = self._score_label.height() // 2
            self._score_label.setStyleSheet(
                f"QLabel {{ color:{fg}; background:{bg}; "
                f"padding:1px 8px; border-radius:{radius}px; "
                f"border:1px solid {border}; }}")
        else:
            self._score_label.setStyleSheet(
                f"color:{T().TEXT2.name()}; background:transparent;")
        self._pw.update()

    @_profile_method("Graph.set_current")
    def set_current(self, idx, score=None, no_data=False):
        """現在手の縦線・スコアラベルを更新する。
        通常はアニメで滑らかに移動するが、ドラッグ中はリアルタイム追従のため
        即時反映する。

        score の決定ロジック:
          - 引数 score が直接渡された場合はそれを優先
          - そうでなく no_data=False なら _xy_map[idx] から検索
          - 検索結果も None で、かつグラフにデータがある場合は前回の
            _label_score を保持(チラつき防止)
        """
        self.current_move = idx
        if not self._use_pg:
            return

        # ── 目標値の決定 ─────────────────────────────────────────
        # score が直接渡された場合はそれを優先、なければ _xy_map から検索
        if score is None and not no_data:
            xy_map = getattr(self, "_xy_map", {})
            score = xy_map.get(idx)
        # チラつき防止: scoreがNullの場合は前回値を保持
        if score is not None:
            self._label_score = score
        # ※ グラフデータが全くない場合は _label_score は None のまま
        target_score = self._label_score
        self._cur_target_idx = idx
        self._cur_target_score = target_score
        self._cur_target_no_data = bool(no_data)

        # ── ドラッグ中はアニメせず即時反映(リアルタイム追従)──
        if self._dragging:
            self._stop_cur_anim()
            self._cur_display_idx = float(idx)
            self._cur_display_score = target_score
            self._apply_cur_display()
            return

        # ── アニメで補間 ─────────────────────────────────────────
        self._start_cur_anim(idx, target_score)

    def _stop_cur_anim(self):
        if self._cur_anim is not None:
            self._cur_anim.stop()

    def _start_cur_anim(self, target_idx, target_score):
        """現在の表示値から目標値へ向けてアニメを開始する。
        valueChanged で 0.0..1.0 の進捗を受け取り、idx と score を線形補間する。
        score 側は None ↔ 数値の遷移は補間できないため瞬時切替。"""
        if self._cur_anim is None:
            from PyQt6.QtCore import QVariantAnimation, QEasingCurve
            anim = QVariantAnimation(self)
            anim.setDuration(self._CUR_ANIM_DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.valueChanged.connect(self._on_cur_anim_value_changed)
            self._cur_anim = anim

        # アニメ開始時点の表示値(start)と目標値(end)を保存
        self._anim_start_idx = float(self._cur_display_idx)
        self._anim_end_idx = float(target_idx)
        self._anim_start_score = self._cur_display_score
        self._anim_end_score = target_score

        # score の補間可否: 両方が数値なら補間、それ以外は即時切替
        s0, s1 = self._anim_start_score, self._anim_end_score
        self._anim_score_interpolate = (s0 is not None and s1 is not None)

        anim = self._cur_anim
        anim.stop()
        anim.start()

    def _on_cur_anim_value_changed(self, t):
        """アニメ進捗 t (0.0..1.0) を受け取り、idx と score を補間して反映する。"""
        try:
            t = float(t)
        except (TypeError, ValueError):
            return
        # idx は線形補間
        i0 = self._anim_start_idx
        i1 = self._anim_end_idx
        self._cur_display_idx = i0 + (i1 - i0) * t
        # score は両方が数値なら補間、そうでなければ完了側(end)で瞬時切替
        if self._anim_score_interpolate:
            s0 = float(self._anim_start_score)
            s1 = float(self._anim_end_score)
            self._cur_display_score = s0 + (s1 - s0) * t
            # _label_score も補間値で上書き(ラベル描画ロジックが参照するため)
            self._label_score = self._cur_display_score
        else:
            # アニメ完了時にだけ end 側に切り替える
            if t >= 1.0:
                self._cur_display_score = self._anim_end_score
                self._label_score = self._anim_end_score
        self._apply_cur_display()

    def _apply_cur_display(self):
        """表示値 _cur_display_idx / _cur_display_score を縦線とラベルに反映する。"""
        idx = self._cur_display_idx
        self._cur_line.setValue(idx)
        # ラベル X 位置の追従に使うインデックス
        self._label_x_idx = idx

        if self._score_label is None:
            self._update_y_labels()
            return

        # ラベルテキスト/スタイルを更新
        if self._label_score is None:
            no_data = getattr(self, "_cur_target_no_data", False)
            if getattr(self, "_xy_map", {}) or no_data:
                # グラフにデータあり・未解析 (or no_data 明示) → ハイフン表示
                # 確定スコア時と同様に「border-radius を高さの半分」にして
                # 完全なピル形状にする(固定 8px だと高さ次第で楕円に見えるため)
                t = T()
                self._score_label.setText("—")
                self._score_label.setStyleSheet(
                    f"QLabel {{ color:{t.TEXT2.name()}; background:{t.PANEL.name()}; "
                    f"padding:1px 8px; border-radius:0px; "
                    f"border:1px solid {t.BORDER.name()}; }}")
                self._score_label.adjustSize()
                radius = self._score_label.height() // 2
                self._score_label.setStyleSheet(
                    f"QLabel {{ color:{t.TEXT2.name()}; background:{t.PANEL.name()}; "
                    f"padding:1px 8px; border-radius:{radius}px; "
                    f"border:1px solid {t.BORDER.name()}; }}")
                self._score_label.show()
            else:
                # グラフにデータなし → 非表示
                self._score_label.hide()
        else:
            # 現在手が解析済み → その手の目差を表示
            s = self._label_score
            if s >= 0:
                text = f'黒 <span style="font-size:14px">+{s:.1f}</span>'
                # 黒側ラベル背景は GRAPH_BLACK と同期(色・α一致)
                gb = T().GRAPH_BLACK
                bg = f"rgba({gb.red()},{gb.green()},{gb.blue()},{gb.alpha()})"
                fg = "#ffffff"
                border = T().SCORE_LABEL_BORDER_BLACK.name()
            else:
                text = f'白 <span style="font-size:14px">+{abs(s):.1f}</span>'
                # 白側ラベル: グラフ白塗りと同じ GRAPH_WHITE で背景を同色化(無彩色)。
                # ボーダーも無彩色の SCORE_LABEL_BORDER_WHITE に揃え、
                # 黒側 (SCORE_LABEL_BORDER_BLACK) と対称な設計にしている。
                # 文字色は勝率バー白側 (ScoreBoard) と同じく
                # ダーク=#1e1e1e / ライト=#333333 で統一する。
                bg = T().GRAPH_WHITE.name()
                fg = "#1e1e1e" if T().is_dark else "#333333"
                border = T().SCORE_LABEL_BORDER_WHITE.name()
            self._score_label.setText(text)
            self._score_label.setStyleSheet(
                f"QLabel {{ color:{fg}; background:{bg}; "
                f"padding:1px 8px; border-radius:0px; "
                f"border:1px solid {border}; }}")
            self._score_label.adjustSize()
            radius = self._score_label.height() // 2
            self._score_label.setStyleSheet(
                f"QLabel {{ color:{fg}; background:{bg}; "
                f"padding:1px 8px; border-radius:{radius}px; "
                f"border:1px solid {border}; }}")
            self._score_label.show()

        # X位置は常に縦線(表示値)に追従
        vb = self._pw.getPlotItem().getViewBox()
        scene_pt = vb.mapViewToScene(QPointF(self._label_x_idx, 0))
        pw_pt = self._pw.mapFromScene(scene_pt)
        widget_x = self._pw.mapTo(self, pw_pt).x()
        lw = self._score_label.width()
        lx = int(widget_x - lw / 2)
        lx = max(2, min(lx, self.width() - lw - 2))
        self._score_label.move(lx, 1)

        self._update_y_labels()
