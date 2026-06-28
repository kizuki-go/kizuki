"""
gui/widgets/common.py — 汎用ウィジェット。

依存: gui.theme, gui.icons, PyQt6
このモジュールは widgets レイヤの基盤。同レイヤの他 widget からも参照されてよいが、
上層 (gui.dialogs, gui.menus, gui._mixins.*, gui.main_window) を直接 import してはならない。
style_qmenu (gui.menus) はレイヤ的に上層のため _AlwaysAcceptWheelTextEdit 内で
lazy import している。

提供:
- _RankItemDelegate: 棋力サブメニューの QListWidget item を独自描画する delegate
- SLIDER_HEIGHT / SLIDER_HANDLE: FlatSlider 用サイズ定数
- FlatSlider: 細いトラック + 正円ハンドルのフラットスライダー
- _ScrollFadeOverlay: QTextEdit に被せるフェードオーバーレイ
- _AlwaysAcceptWheelTextEdit: 限界到達ホイールイベントを親へ伝播させない QTextEdit
"""
from __future__ import annotations
from PyQt6.QtWidgets import QSlider, QStyledItemDelegate, QStyle, QWidget, QTextEdit
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap

from gui.theme import T, R_MD
from gui.icons import _get_rank_check_mark_path


# ─────────────────────────────────────────────────────────────────────────
# 棋力サブメニューのリスト項目専用デリゲート
# ─────────────────────────────────────────────────────────────────────────
# QListWidget で setIcon を使うと、Qt が QStyleOptionViewItem の内部余白を
# 経由してアイコンとテキストの位置を決めるため、ピクセル単位で正確な
# レイアウトが取りにくい (Qt のスタイルやプラットフォームで微妙にずれる)。
#
# このデリゲートでは、paint() を完全に独自実装し、ホバー背景・チェック
# マーク・テキストを 全て px 単位の固定オフセットで描画することで、ルール
# サブメニュー (QMenu::indicator + QMenu::item padding) と完全に同じ位置に
# 揃える。
#
# 設計値 (item rect.left = 0 を基準とする item 内座標):
#   ・PADDING_LEFT_TO_CHECK_CENTER = 14px (rect.left + 14 が ✓ 中心 X)
#   ・PADDING_LEFT_TO_TEXT         = 30px (rect.left + 30 が テキスト左端)
#   ・CHECK_SIZE                   = 12x12
# これを「QListWidget padding:4」と組み合わせると、メニュー border 内側
# (ルール側測定値: x=383) からの ✓ 中央オフセットが 4 + 14 = 18px になる。
# ※ ルール側の実機オフセットは 33px だが、これは QMenu の border 内側
#    から indicator margin-left:8 + width:12 + α が含まれている結果。
#    棋力リストでは border が QMenu 親のものを使い、QListWidget 自身に
#    は border が無いため、px をそのまま指定する。
class _RankItemDelegate(QStyledItemDelegate):
    """棋力サブメニュー用 QListWidget の item を独自描画するデリゲート。

    UserRole+1 (Qt.ItemDataRole.UserRole + 1) に保存された bool を見て
    チェックマークの ON/OFF を描き分ける。チェックマーク画像は
    _get_rank_check_mark_path() でキャッシュされた 12x12 SVG を読み込み、
    DPR を考慮して描画する。
    """

    # px 単位のレイアウト定数 (item 内座標)。実機 (Windows) でのユーザ
    # フィードバックを反映した最終値。ルール側との完全一致は狙わず、
    # 棋力サブメニュー単独で視覚的にバランスが取れる値に調整した。
    CHECK_LEFT = 8       # item 左端から ✓ 矩形 左端まで
    CHECK_SIZE = 12      # ✓ 描画矩形のサイズ (12x12)
    TEXT_LEFT = 36       # item 左端から テキスト左端まで
                         #  ✓中央(8+6=14) → テキスト = 36 - 14 = 22px の間隔
    ROW_HEIGHT = 36      # 各行の高さ

    def __init__(self, parent=None):
        super().__init__(parent)
        # _check_pix12 は _build_rank_check_pixmap() でキャッシュされる
        # 12x12 純白 QPixmap (現在テーマ色)。delegate からは parent
        # (QListWidget) の親 (= MainWindow) を辿って取得する。
        self._cached_pix: QPixmap | None = None
        self._cached_color: str = ""

    def _get_check_pixmap(self) -> QPixmap:
        """現在テーマ色のチェックマーク 12x12 QPixmap を返す。
        テーマ色変更時のみ再ロード(キャッシュ)。"""
        text_color = T().TEXT.name()
        if self._cached_pix is None or self._cached_color != text_color:
            svg_path = _get_rank_check_mark_path(text_color)
            pix = QPixmap(svg_path)
            if pix.width() != self.CHECK_SIZE or pix.height() != self.CHECK_SIZE:
                pix = pix.scaled(
                    self.CHECK_SIZE, self.CHECK_SIZE,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._cached_pix = pix
            self._cached_color = text_color
        return self._cached_pix

    def sizeHint(self, option, index):
        # 行の高さは固定 (ROW_HEIGHT)。幅は親 widget のものを使うので
        # option.rect.width() をそのまま返す (実際には QListWidget が
        # viewport の幅に合わせるため、ここでは親に任せる)。
        from PyQt6.QtCore import QSize as _QSize
        return _QSize(option.rect.width() or 100, self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = option.rect
        t = T()

        # ─ ホバー背景 (角丸) ─
        # QSS の :hover は delegate を使うと効かないため、ここで描画。
        # option.state に QStyle.StateFlag.State_MouseOver が立っていれば
        # ホバー中。背景は PANEL2、角丸は他メニューと同じ 4px。
        from PyQt6.QtCore import QRectF as _QRectF
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.PANEL2)
            # 上下左右に 0px、ただし行間が詰まりすぎないよう若干 inset
            painter.drawRoundedRect(_QRectF(rect), 4, 4)

        # ─ チェックマーク描画 (UserRole+1 が True のとき) ─
        is_checked = bool(index.data(Qt.ItemDataRole.UserRole + 1))
        if is_checked:
            pix = self._get_check_pixmap()
            # ✓ を rect.left + CHECK_LEFT, 縦中央 に配置
            cx = rect.left() + self.CHECK_LEFT
            cy = rect.top() + (rect.height() - self.CHECK_SIZE) // 2
            painter.drawPixmap(
                _QRectF(cx, cy, self.CHECK_SIZE, self.CHECK_SIZE),
                pix,
                _QRectF(0, 0, pix.width(), pix.height()),
            )

        # ─ テキスト描画 ─
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.setPen(t.TEXT)
        # font は QListWidget の既定 (= QSS の font-size:14px が継承される)
        font = option.font
        painter.setFont(font)
        # 縦中央揃え、左揃え。テキスト矩形は rect.left + TEXT_LEFT から
        # rect.right まで。
        text_rect = rect.adjusted(self.TEXT_LEFT, 0, -8, 0)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            str(text),
        )

        painter.restore()


# ── FlatSlider ────────────────────────────────────────────────────────────
# QSS の負 margin はプラットフォーム依存で handle が楕円になるため、
# paintEvent を自前実装して確実に正円を描画する。
SLIDER_HEIGHT = 28   # ウィジェット固定高さ (NavBar の他ボタン 28x28 と揃える)
SLIDER_HANDLE = 16   # handle の直径（正円）
_TRACK_H = 4         # トラックの高さ

class FlatSlider(QSlider):
    """細いトラックと正円ハンドルを持つフラットデザインのスライダー。
    水平・垂直どちらの向きにも対応(orientation で切り替え)。
    QSS の負 margin に依存せず paintEvent で直接描画するため、
    Windows / macOS どちらでも正円が保証される。

    ハンドル位置・進捗トラックは _display_value(float) を参照して描画し、
    値変化時に QVariantAnimation で 180ms / OutCubic 補間する。
    self.value() 自体は即時更新されるので valueChanged 信号や
    上位ロジックには影響しない。ドラッグ中(isSliderDown=True)は
    補間せず即時反映、リアルタイム追従を維持する。
    """
    _SLIDER_ANIM_DURATION_MS = 180

    def __init__(self, parent=None, orientation: Qt.Orientation = Qt.Orientation.Horizontal):
        super().__init__(orientation, parent)
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(SLIDER_HEIGHT)
        else:
            self.setFixedWidth(SLIDER_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        # ハンドル表示位置の補間用 float 値
        self._display_value: float = float(self.value())
        self._value_anim = None  # QVariantAnimation(参照保持・連打時の停止用)
        # 直前の maximum を記録: range 変更時、表示値が直前 max と等しかった場合
        # 「末尾追従」として新 max にスナップ追従させる(範囲拡大時の見た目ズレ防止)。
        self._prev_max: int = self.maximum()

    def _start_value_anim(self, target: float):
        """_display_value を target まで OutCubic で補間する。"""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        # 既存アニメは止めて連結(連打時に滑らか)
        if self._value_anim is not None:
            self._value_anim.stop()
            self._value_anim = None

        start = self._display_value
        if start == target:
            return

        anim = QVariantAnimation(self)
        anim.setDuration(self._SLIDER_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(float(start))
        anim.setEndValue(float(target))

        def _on_value(v):
            try:
                self._display_value = float(v)
                self.update()
            except RuntimeError:
                pass  # ウィジェット破棄済み

        anim.valueChanged.connect(_on_value)
        anim.finished.connect(lambda: setattr(self, "_value_anim", None))
        self._value_anim = anim
        anim.start()

    def sliderChange(self, change):
        """値・range 変化を捕捉して補間を起動。
        QSlider.setValue / triggerAction / キー入力 / ホイール 等、
        全ての値変化経路を網羅できるフックポイント。
        """
        super().sliderChange(change)
        try:
            SC = QSlider.SliderChange
        except AttributeError:
            from PyQt6.QtWidgets import QAbstractSlider
            SC = QAbstractSlider.SliderChange

        if change == SC.SliderValueChange:
            target = float(self.value())
            # ドラッグ中は補間しない(リアルタイム追従)
            if self.isSliderDown():
                if self._value_anim is not None:
                    self._value_anim.stop()
                    self._value_anim = None
                self._display_value = target
                self.update()
            else:
                self._start_value_anim(target)
        elif change == SC.SliderRangeChange:
            new_max = self.maximum()
            old_max = self._prev_max
            # range 変更時の挙動:
            # - range が拡大し、かつ直前の表示値が「直前 maximum」と等しかった
            #   場合 → 末尾追従。棋譜の最終手にいる時に新しい手が追加された等
            #   のケースで、表示値を新 maximum に瞬時スナップして「左にずれて
            #   右へスライド」というアニメ起動を防ぐ。
            # - range が縮小した場合は基本的に何もしない。
            #   setMaximum 呼び出し時に Qt が value をクランプし、
            #   SliderValueChange が発火するので、そこでアニメが起動する。
            #   _display_value をここで触ると、その後発火する SliderValueChange
            #   による _start_value_anim の start/target 計算が壊れて、
            #   ハンドルが動かないように見える問題が出る。
            if (new_max > old_max
                and abs(self._display_value - float(old_max)) < 1e-6):
                # 末尾追従(拡大時のみ): 進行中アニメを止めて新 maximum へスナップ
                if self._value_anim is not None:
                    self._value_anim.stop()
                    self._value_anim = None
                self._display_value = float(new_max)
                self.update()
            self._prev_max = new_max

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter, QColor
        from PyQt6.QtCore import QRectF
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        r = SLIDER_HANDLE // 2          # handle 半径

        # 値→比率(_display_value: アニメ補間された表示位置)
        span = max(1, self.maximum() - self.minimum())
        ratio = (self._display_value - self.minimum()) / span
        # 数値誤差で範囲外に出ないようクランプ
        if ratio < 0.0:
            ratio = 0.0
        elif ratio > 1.0:
            ratio = 1.0

        if self.orientation() == Qt.Orientation.Horizontal:
            cy = h // 2                     # 中央 Y
            handle_x = r + ratio * (w - SLIDER_HANDLE)  # handle 中心 X

            # トラック（全体）
            track_y = cy - _TRACK_H // 2
            track_rect = QRectF(r, track_y, w - SLIDER_HANDLE, _TRACK_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.BORDER.name()))
            p.drawRoundedRect(track_rect, _TRACK_H / 2, _TRACK_H / 2)

            # トラック（進捗・アクセント色）
            filled_w = handle_x - r
            if filled_w > 0:
                filled_rect = QRectF(r, track_y, filled_w, _TRACK_H)
                p.setBrush(QColor(t.ACCENT.name()))
                p.drawRoundedRect(filled_rect, _TRACK_H / 2, _TRACK_H / 2)

            # ハンドル（正円）
            # 外形 SLIDER_HANDLE×SLIDER_HANDLE(16×16)を完全に保ちつつ、
            # ボーダー線の滲みを排除するため「塗り円 2 重」方式で描画する。
            # ペンを使うとペン幅 1px が画素境界にまたがって滲むので、
            # 全てペンなしの塗りで描き、外側を BORDER 色、内側 1px 縮めた
            # 円を白で塗ることで「外形 16×16 + 内側に白 14×14、その差分が
            # 1px のボーダー」という見た目を作る。
            p.setPen(Qt.PenStyle.NoPen)
            # 1. 外側: BORDER 色の塗り円(16×16)
            p.setBrush(QColor(t.BORDER.name()))
            p.drawEllipse(QRectF(handle_x - r, cy - r, SLIDER_HANDLE, SLIDER_HANDLE))
            # 2. 内側: 白の塗り円(14×14、1px ずつ内側にずらす)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QRectF(
                handle_x - r + 1, cy - r + 1,
                SLIDER_HANDLE - 2, SLIDER_HANDLE - 2,
            ))
        else:
            # 垂直: Qt の慣例で「上=最大、下=最小」になる(QSlider 標準動作)。
            # ratio=1.0 のとき handle が上端、ratio=0.0 のとき下端に来る。
            cx = w // 2                     # 中央 X
            handle_y = r + (1.0 - ratio) * (h - SLIDER_HANDLE)

            # トラック（全体）
            track_x = cx - _TRACK_H // 2
            track_rect = QRectF(track_x, r, _TRACK_H, h - SLIDER_HANDLE)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.BORDER.name()))
            p.drawRoundedRect(track_rect, _TRACK_H / 2, _TRACK_H / 2)

            # トラック（進捗・アクセント色）handle_y から下端まで
            filled_top = handle_y
            filled_h = (h - r) - filled_top
            if filled_h > 0:
                filled_rect = QRectF(track_x, filled_top, _TRACK_H, filled_h)
                p.setBrush(QColor(t.ACCENT.name()))
                p.drawRoundedRect(filled_rect, _TRACK_H / 2, _TRACK_H / 2)

            # ハンドル（正円）
            # 水平スライダーと同じ「塗り円 2 重」方式(ペンを使わない)で
            # 外形 16×16 + 内側 14×14 の差分でボーダーを表現する。
            p.setPen(Qt.PenStyle.NoPen)
            # 1. 外側: BORDER 色の塗り円
            p.setBrush(QColor(t.BORDER.name()))
            p.drawEllipse(QRectF(cx - r, handle_y - r, SLIDER_HANDLE, SLIDER_HANDLE))
            # 2. 内側: 白の塗り円
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QRectF(
                cx - r + 1, handle_y - r + 1,
                SLIDER_HANDLE - 2, SLIDER_HANDLE - 2,
            ))

        p.end()


# ── ホイールイベントを親に伝播させない QTextEdit ─────────────────────────────
class _ScrollFadeOverlay(QWidget):
    """QTextEdit の上に被せるフェードオーバーレイ。
    上下端に背景色 → 透明のグラデーションを描き、スクロール余地がある方向だけ
    フェードを表示する。マウスイベントは透過する(下のテキストエディットへ)。
    親ウィジェット(=被せる対象)とスクロール監視対象テキストエディットは
    別々に指定できる。コメントオーバーレイ全体に被せ、左右端まで
    フェードを伸ばす用途で親=コメントオーバーレイ、textedit=コメント本文 とする。"""
    FADE_HEIGHT = 32  # フェードの高さ (px)

    def __init__(self, parent, textedit=None):
        super().__init__(parent)
        self._te = textedit if textedit is not None else parent
        self._show_top = False
        self._show_bottom = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def update_visibility(self):
        sb = self._te.verticalScrollBar()
        new_top = sb.value() > sb.minimum()
        new_bottom = sb.value() < sb.maximum()
        if new_top != self._show_top or new_bottom != self._show_bottom:
            self._show_top = new_top
            self._show_bottom = new_bottom
            self.update()

    def paintEvent(self, ev):
        from PyQt6.QtGui import QPainter, QLinearGradient, QColor, QPainterPath
        from PyQt6.QtCore import QRectF
        if not (self._show_top or self._show_bottom):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # フェード色はコメントオーバーレイの PANEL 色 (alpha=230 相当)
        # TranslucentWidget が描画する背景色と揃える
        c = QColor(T().PANEL)
        c.setAlpha(230)
        transparent = QColor(c.red(), c.green(), c.blue(), 0)
        h = self.FADE_HEIGHT
        w = self.width()
        H = self.height()
        # コメントオーバーレイの角丸(R_MD=12)に合わせ、
        # フェードの外側の角(上フェードは上2角、下フェードは下2角)を
        # 角丸クリップする。これでフェードがオーバーレイ枠からはみ出ない。
        r = float(R_MD)
        if self._show_top and H > 0:
            QRectF(0, 0, w, h)
            top_path = QPainterPath()
            # 上2角だけ角丸、下辺は直線(フェード境界)
            top_path.moveTo(r, 0)
            top_path.lineTo(w - r, 0)
            top_path.quadTo(w, 0, w, r)
            top_path.lineTo(w, h)
            top_path.lineTo(0, h)
            top_path.lineTo(0, r)
            top_path.quadTo(0, 0, r, 0)
            top_path.closeSubpath()
            grad_top = QLinearGradient(0, 0, 0, h)
            grad_top.setColorAt(0.0, c)
            grad_top.setColorAt(1.0, transparent)
            p.fillPath(top_path, grad_top)
        if self._show_bottom and H > 0:
            bot_path = QPainterPath()
            # 下2角だけ角丸、上辺は直線(フェード境界)
            bot_path.moveTo(0, H - h)
            bot_path.lineTo(w, H - h)
            bot_path.lineTo(w, H - r)
            bot_path.quadTo(w, H, w - r, H)
            bot_path.lineTo(r, H)
            bot_path.quadTo(0, H, 0, H - r)
            bot_path.lineTo(0, H - h)
            bot_path.closeSubpath()
            grad_bot = QLinearGradient(0, H - h, 0, H)
            grad_bot.setColorAt(0.0, transparent)
            grad_bot.setColorAt(1.0, c)
            p.fillPath(bot_path, grad_bot)
        p.end()


# ── ホイールイベントを親に伝播させない QTextEdit ─────────────────────────────
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
        from gui.menus import style_qmenu

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

