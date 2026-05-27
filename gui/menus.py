"""
gui/menus.py — メニュー関連のスタイル/位置補正/コミウィジェット。

依存: gui.theme, gui.fonts, gui.icons, PyQt6.

提供:
- style_qmenu: QMenu に共通スタイル (menu_qss) と Frameless/Translucent 設定を適用
- _SubMenuPositioner: サブメニューが親メニューに重ならないよう位置補正する eventFilter
- _install_submenu_positioner: 上記をシングルトンとして QMenu にインストールするヘルパ
- _KomiCustomWidget: コミメニューの「その他」項目用インラインウィジェット
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QMenu, QWidgetAction, QPushButton, QHBoxLayout, QLabel,
)
from PyQt6.QtCore import (
    Qt, QObject, QEvent, QRect, QSettings, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QBrush, QPixmap

from gui.theme import T, SP_SM, SP_MD
from gui.fonts import Font_SM
from gui.icons import menu_qss, _get_check_mark_path


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

