"""
gui/widgets/welcome.py — 解析モード起動時のウェルカム画面。

依存: gui.theme, gui.fonts, gui.icons, PyQt6, pathlib.
style_qmenu (現状 main_window.py、Phase 4 で gui.menus に移動) は
contextMenu 内で lazy import している。

提供:
- WelcomePane: 碁盤の代わりに表示する2カード横並びのペイン
- _WelcomeCard: ベースの DropZone カード (D&D 受付 + クリック開く)
- _NewGameCard: 新規作成用、SGFを開くペースを継承
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSizePolicy,
    QApplication, QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import (
    QAction, QKeySequence, QBrush, QColor, QPainter, QPainterPath, QPen,
)

from gui.theme import T
from gui.fonts import Font_XS, Font_SM, Font_LG
from gui.icons import make_icon


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
        from gui.menus import style_qmenu
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

