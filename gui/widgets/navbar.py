"""
gui/widgets/navbar.py — 碁盤下のナビゲーションバーと関連トグル UI。

依存: gui.theme, gui.fonts, gui.icons, gui.widgets.common, PyQt6.

提供:
- _HelpPopover: クリックで開閉する小ポップオーバー (?, i ボタン用)
- ToggleSwitch: モダンなフラットトグルスイッチ
- ToggleBar: 複数 ToggleSwitch を横並びにする小バー
- NavBar: 戻る/進む/スライダ/AI/Ownership/手数表示 などのナビバー
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QSlider,
    QHBoxLayout, QVBoxLayout, QCheckBox, QRadioButton,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPointF, QSize, QSettings, QTimer,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QIcon, QPixmap, QPalette,
)

from gui.theme import T, SP_XS, SP_SM, SP_LG
from gui.fonts import F, Font_XS, Font_MD
from gui.icons import make_icon, icon_button_qss, install_icon_hover_color_swap
from gui.widgets.common import FlatSlider, SLIDER_HANDLE


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

