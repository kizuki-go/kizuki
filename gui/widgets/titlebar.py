"""
gui/widgets/titlebar.py — カスタムタイトルバー(VS Code 型)。

依存: gui.theme, gui.fonts, gui.icons, PyQt6.
style_qmenu (現状 main_window.py、Phase 4 で gui.menus に移動) への
依存は contextMenu イベント内で lazy import している。

提供:
- _CustomTitleBar: アプリ全体のフレームレスタイトルバー
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QPushButton, QHBoxLayout, QLabel, QMenu, QMainWindow,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QEvent, QPoint, QRect, QRectF, QSize,
    QDateTime, QByteArray,
)
from PyQt6.QtGui import (
    QPainter, QColor, QIcon, QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer

from gui.theme import T, SP_MD, SP_LG
from gui.fonts import F
from gui.icons import make_icon


class _CustomTitleBar(QWidget):
    """カスタムタイトルバー(VS Code 型)。
    
    左から: アプリアイコン → メニューボタン群(プレースホルダ) → 中央ドラッグエリア
    右端: 最小化 / 最大化(復元) / 閉じる
    
    高さ 32px 固定。背景は T().TOOLBAR.name() でテーマに追従。
    
    Phase 1 ではメニューボタンは表示用プレースホルダのみで、ポップアップ機能は
    Phase 2(後続のStep 4)で接続する。
    
    親の QMainWindow が FramelessWindowHint で動作することを前提とする。
    タイトルバーの空き領域でのドラッグ・ダブルクリック・リサイズは
    親 MainWindow 側の event handler で処理する(本クラスは純粋に
    描画とボタンクリック委譲のみ)。
    """
    
    HEIGHT = 36
    BTN_WIDTH = 46          # ウィンドウ操作ボタンの幅(Win11/VS Code 相当)
    APP_ICON_AREA = 40      # 左端のアプリアイコン領域(SP_LG=16の左右余白でアイコンを内包)
    MENU_ITEM_PADDING = 12  # メニュー項目の左右padding(SP_MD)
    
    # メニューラベル
    # 音量はアイコンボタンとしてタイトルバー右端に独立配置するため、
    # メニューバーからは外している
    MENU_LABELS = ["ファイル", "表示", "設定"]
    
    # シグナル: ウィンドウ操作
    minimize_clicked = pyqtSignal()
    maximize_toggle_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    panel_toggle_clicked = pyqtSignal()  # 右パネル開閉トグル
    volume_clicked = pyqtSignal()        # 音量アイコンクリック
    
    def __init__(self, parent=None):
        super().__init__(parent)

        # ======================================================================
        # Phase 7: 子ウィジェット/状態属性の事前初期化。
        # _make_window_btn / メニューインストールで後から代入されるものは
        # None / [] で先に置き、hasattr() ガードを使わずに済むようにする。
        # ======================================================================
        self._menu_buttons: list[QPushButton] = []
        self._menus: list = []
        self._btn_toggle_panel = None
        self._btn_volume = None
        self._last_any_menu_close_ms = 0

        self.setObjectName("custom_titlebar")
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ── 左端: アプリアイコン領域(40×32) ───────────────────
        self._app_icon_lbl = QLabel()
        self._app_icon_lbl.setObjectName("titlebar_app_icon")
        self._app_icon_lbl.setFixedSize(self.APP_ICON_AREA, self.HEIGHT)
        self._app_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 背景・border-bottom は apply_theme() で他の子要素と統一管理する。
        layout.addWidget(self._app_icon_lbl)
        
        # ── メニューボタン群(プレースホルダ) ─────────────────
        # _menu_buttons は冒頭の Phase 7 init block で空 list 初期化済み
        for label in self.MENU_LABELS:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(self.HEIGHT)
            # 文字幅 + padding。setStyleSheetで色制御できるようにObjectName付与
            btn.setObjectName("titlebar_menu_btn")
            layout.addWidget(btn)
            self._menu_buttons.append(btn)
        
        # ── 中央ドラッグエリア(stretchで埋める) ──────────────
        self._drag_spacer = QWidget()
        self._drag_spacer.setObjectName("titlebar_drag_area")
        layout.addWidget(self._drag_spacer, stretch=1)
        
        # ── 右端: 音量 / 右パネルトグル / 最小化 / 最大化 / 閉じる ──
        # 音量アイコンはトグルパネルアイコンの左隣に配置
        self._btn_volume = self._make_window_btn("volume_on")
        self._btn_toggle_panel = self._make_window_btn("toggle_panel")
        self._btn_min = self._make_window_btn("min")
        self._btn_max = self._make_window_btn("max")
        self._btn_close = self._make_window_btn("close")
        self._btn_volume.clicked.connect(self.volume_clicked.emit)
        self._btn_toggle_panel.clicked.connect(self.panel_toggle_clicked.emit)
        self._btn_min.clicked.connect(self.minimize_clicked.emit)
        self._btn_max.clicked.connect(self.maximize_toggle_clicked.emit)
        self._btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self._btn_volume)
        layout.addWidget(self._btn_toggle_panel)
        layout.addWidget(self._btn_min)
        layout.addWidget(self._btn_max)
        layout.addWidget(self._btn_close)
        
        self.apply_theme()
    
    def _make_window_btn(self, kind: str) -> QPushButton:
        """ウィンドウ操作ボタン(_、□、×)を生成する。
        kind: 'min' | 'max' | 'close' | 'toggle_panel' | 'volume_on' | 'volume_off'
        """
        btn = QPushButton()
        btn.setFlat(True)
        btn.setFixedSize(self.BTN_WIDTH, self.HEIGHT)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # objectName は状態に依存しない安定値に揃える
        # (音量はON/OFFで切り替わるが、QSS適用が外れないように一意名を維持)
        if kind.startswith("volume_"):
            btn.setObjectName("titlebar_window_btn_volume")
        else:
            btn.setObjectName(f"titlebar_window_btn_{kind}")
        # アイコンは paintEvent 経由で描画したいが、シンプルさ優先で
        # SVG画像を QIcon としてセットする方式を採る
        # トグルパネルアイコンは図形要素(枠+区切り線)が多く視認性のため
        # 他のウィンドウ操作ボタンより大きめに描く
        # かつ横長(17x14)で右パネル開閉のメタファを表現
        # 音量アイコンも 14x14 で視認性を上げる
        btn.setIcon(self._make_window_btn_icon(kind, T().TEXT.name()))
        if kind.startswith("toggle_panel"):
            btn.setIconSize(QSize(17, 14))
        elif kind.startswith("volume_"):
            # 自作 SVG: viewBox=描画サイズ(20×20), stroke=1 で他アイコンと統一。
            # 全座標を 0.5 オフセットで線を 1 ピクセルに収束させ、視覚的な濃さを揃える。
            btn.setIconSize(QSize(20, 20))
        elif kind in ("max", "restore"):
            # max/restore は他のアイコン(min/close)より一回り大きく(12×12)
            # して、最大化操作の存在感を出す。stroke-width は 0.83 に下げて
            # 実描画の線太さを 1.0 px に揃える。
            btn.setIconSize(QSize(12, 12))
        else:
            btn.setIconSize(QSize(10, 10))
        return btn
    
    def _make_window_btn_icon(self, kind: str, color: str, opacity: float = 1.0) -> QIcon:
        """細線スタイルのウィンドウ操作アイコンを生成。

        opacity: 0.0〜1.0 のアルファ値。1.0 = 不透明(通常)、0.5 = 半透明。
                 QPainter の setOpacity をラスタライズ時に適用する方式なので、
                 SVG の色文字列に依存せずどの kind にも適用可能。
                 非アクティブ状態のトグルパネルボタン等で使用する。
        """
        if kind == "min":
            # 線端を内側に 0.5 オフセット + stroke-linecap=round
            # (close と統一、丸み統一の方向 A)
            # y=4.5 にすることで Qt SVG ラスタライザが線を 1 ピクセルに完全収束
            # させる(y=5 (整数) だと 2 ピクセルに分散して薄く R=143 になるため)。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="10" height="10">'
                f'<line x1="0.5" y1="4.5" x2="9.5" y2="4.5" stroke="{color}" stroke-width="1" stroke-linecap="round"/>'
                f'</svg>'
            )
        elif kind == "max":
            # 方針 Y: viewBox=描画サイズ(12×12), stroke=1 で統一。
            # Qt SVG ラスタライザでは小数 stroke-width(0.83 等)で α が大きく
            # 落ちて他アイコンより薄く見える問題があったため、すべてのアイコンを
            # 「viewBox = 描画サイズ + stroke-width=1」に統一して線の濃さを揃える。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12" width="12" height="12">'
                f'<rect x="0.5" y="0.5" width="11" height="11" rx="1.5" ry="1.5" '
                f'stroke="{color}" stroke-width="1" fill="none" stroke-linejoin="round"/>'
                f'</svg>'
            )
        elif kind == "restore":
            # 最大化時の「復元」アイコン: 重なった2つの四角(Win11 風)
            # 手前は完全な角丸矩形、奥は右上に「┐字」の 2 辺だけを path で描画。
            # 方針 Y: viewBox=描画サイズ(12×12), stroke=1 で統一。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12" width="12" height="12">'
                f'<rect x="0.5" y="3" width="8.5" height="8.5" rx="1.2" ry="1.2" '
                f'stroke="{color}" stroke-width="1" fill="none" stroke-linejoin="round"/>'
                f'<path d="M 3.5 2.5 L 3.5 1.5 Q 3.5 0.5 4.5 0.5 L 10.5 0.5 '
                f'Q 11.5 0.5 11.5 1.5 L 11.5 7.5 Q 11.5 8.5 10.5 8.5 L 9.5 8.5" '
                f'stroke="{color}" stroke-width="1" fill="none" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
                f'</svg>'
            )
        elif kind == "toggle_panel_open":
            # 右パネルが開いている状態のアイコン: 外枠+右側に縦区切り線(右パネル領域)
            # クリックで「閉じる」イメージ
            # 方針 Y: viewBox=描画サイズ(17×14), stroke=1 で統一。
            # 内側縦線は右側 1/3 程度の位置(x=11.5)に。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 17 14" width="17" height="14">'
                f'<rect x="0.5" y="0.5" width="16" height="13" rx="2" ry="2" '
                f'stroke="{color}" stroke-width="1" fill="none" stroke-linejoin="round"/>'
                f'<line x1="11.5" y1="2" x2="11.5" y2="12" '
                f'stroke="{color}" stroke-width="1" stroke-linecap="round"/>'
                f'</svg>'
            )
        elif kind == "toggle_panel_closed":
            # 右パネルが閉じている状態のアイコン: 外枠+破線区切り
            # クリックで「開く」イメージ
            # 方針 Y: viewBox=描画サイズ(17×14), stroke=1 で統一。
            # 破線パターン:
            #   stroke-dasharray を使うと Qt SVG ラスタライザでサイクル端が
            #   フラクショナル位置に乗って薄い余韻が出る(上下のダッシュが
            #   α=127 で薄く描画される問題)ため、3 つの <rect> で個別に
            #   ダッシュを描画する。
            #   ・各ダッシュ: 1px幅 × 2px高、x=11/y=3,6,9
            #   ・整数座標 + 整数サイズで物理ピクセル境界に完全一致 → α=255 で
            #     完全収束、3 つのダッシュが完全に均一
            #   ・上下に 1px ずつ余白(rect の上端 y=2.5、下端 y=11.5 から
            #     等距離)で対称
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 17 14" width="17" height="14">'
                f'<rect x="0.5" y="0.5" width="16" height="13" rx="2" ry="2" '
                f'stroke="{color}" stroke-width="1" fill="none" stroke-linejoin="round"/>'
                f'<rect x="11" y="3" width="1" height="2" fill="{color}"/>'
                f'<rect x="11" y="6" width="1" height="2" fill="{color}"/>'
                f'<rect x="11" y="9" width="1" height="2" fill="{color}"/>'
                f'</svg>'
            )
        elif kind == "toggle_panel":
            # _make_window_btn 経由の初期生成用(open状態のアイコンを返す)
            # 方針 Y: viewBox=描画サイズ(17×14), stroke=1 で統一。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 17 14" width="17" height="14">'
                f'<rect x="0.5" y="0.5" width="16" height="13" rx="2" ry="2" '
                f'stroke="{color}" stroke-width="1" fill="none" stroke-linejoin="round"/>'
                f'<line x1="11.5" y1="2" x2="11.5" y2="12" '
                f'stroke="{color}" stroke-width="1" stroke-linecap="round"/>'
                f'</svg>'
            )
        elif kind == "volume_on":
            # スピーカー(角取り三角)+ 音波2本(常時アイコン)
            # 方針 Y: viewBox=描画サイズ(20×20), stroke=1 で他アイコンと統一。
            # 全座標を 0.5 オフセットして、Qt SVG ラスタライザで線が 1 ピクセル
            # に完全収束するようにする(整数座標だと線が 2 ピクセルに分散して
            # 薄くなる問題を回避)。
            # スピーカー本体は 6 角を Q ベジエで小さく丸めた path(線画)。
            # サイズ調整版:
            #   ・コーン y を 3.7〜16.3 に拡大して縦に存在感を出す
            #   ・横方向は左に 1.5px シフトして音波・×マークとの間隔を確保
            #   ・チャネル左端 x=2.5, 角取り基点 x=6, コーン頂点 x=11
            #   ・音波 1 本目との隙間: 3px、× マーク左端との隙間: 2.5px
            # 音波 2 本は viewBox 制約のため現状維持。
            # 直線部分は引き続き 0.5 オフセットで 1 ピクセル収束。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20">'
                f'<path d="M 3.5 7.5 L 5.5 7.5 Q 6 7.5 6.4 7.2 L 10.5 3.7 '
                f'Q 11 3.2 11 4 L 11 16 Q 11 16.8 10.5 16.3 '
                f'L 6.4 12.8 Q 6 12.5 5.5 12.5 L 3.5 12.5 '
                f'Q 2.5 12.5 2.5 11.5 L 2.5 8.5 Q 2.5 7.5 3.5 7.5 Z" '
                f'fill="none" stroke="{color}" stroke-width="1" stroke-linejoin="round"/>'
                f'<path d="M14 7.5 Q16 10 14 12.5" stroke="{color}" stroke-width="1" '
                f'fill="none" stroke-linecap="round"/>'
                f'<path d="M16.5 5.5 Q19 10 16.5 14.5" stroke="{color}" stroke-width="1" '
                f'fill="none" stroke-linecap="round"/>'
                f'</svg>'
            )
        elif kind == "volume_off":
            # スピーカー(角取り三角)+ ×マーク(ミュート/0%表示)
            # 方針 Y: viewBox=描画サイズ(20×20), stroke=1 で他アイコンと統一。
            # volume_on と同じスピーカー本体 path を使用(角取り、左シフト版)。
            # × マークは斜め 45° 線で 2 つの問題があるため close と同じ補正を適用:
            #   1) 整数座標だと線端が 2 ピクセルに分散して薄くなる
            #      → 全座標を 0.5 オフセット(x=13.5/18.5, y=7.5/12.5)で 1px 収束
            #   2) 斜め 45° 線は実描画線太さが stroke-width × 1/√2 ≒ 0.707 倍になり
            #      水平/垂直線(stroke=1)より構造的に薄く見える
            #      → stroke-width=1.2 で実描画線太さを 1.0 px に補正
            #      (close アイコンと同じ補正方針)
            # 線の長さは 5px 維持(13.5 → 18.5)。
            # 実証済み: コア α≒252、両端均一、対称性 OK。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20">'
                f'<path d="M 3.5 7.5 L 5.5 7.5 Q 6 7.5 6.4 7.2 L 10.5 3.7 '
                f'Q 11 3.2 11 4 L 11 16 Q 11 16.8 10.5 16.3 '
                f'L 6.4 12.8 Q 6 12.5 5.5 12.5 L 3.5 12.5 '
                f'Q 2.5 12.5 2.5 11.5 L 2.5 8.5 Q 2.5 7.5 3.5 7.5 Z" '
                f'fill="none" stroke="{color}" stroke-width="1" stroke-linejoin="round"/>'
                f'<line x1="13.5" y1="7.5" x2="18.5" y2="12.5" stroke="{color}" stroke-width="1.2" '
                f'stroke-linecap="round"/>'
                f'<line x1="18.5" y1="7.5" x2="13.5" y2="12.5" stroke="{color}" stroke-width="1.2" '
                f'stroke-linecap="round"/>'
                f'</svg>'
            )
        else:  # close
            # stroke-linecap=round で線端を丸め、線端を viewBox 内側に
            # 0.7/9.3 にオフセット(linecap が viewBox の外にはみ出さない)
            # min と統一(丸み統一の方向 A)
            # stroke-width=1.2 は他アイコン(stroke=1)より大きいが、斜め 45° 線は
            # 実描画の垂直方向幅が stroke-width × (1/√2) ≒ 0.7 倍になるため、
            # 1.2 にすることで実描画線太さが他の水平/垂直線(1.0)と揃う。
            # これにより視覚的な濃さが他アイコンと統一される。
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="10" height="10">'
                f'<line x1="0.7" y1="0.7" x2="9.3" y2="9.3" stroke="{color}" stroke-width="1.2" stroke-linecap="round"/>'
                f'<line x1="9.3" y1="0.7" x2="0.7" y2="9.3" stroke="{color}" stroke-width="1.2" stroke-linecap="round"/>'
                f'</svg>'
            )
        # トグルパネルアイコンだけは横長(横12:縦10のviewBox)で描画
        # 音量アイコンはviewBox=14, 描画14×14
        # 他のアイコン(min/max/close)は従来通り正方形(10x10 viewBox)
        if kind.startswith("toggle_panel"):
            # アイコン全体サイズ 17x14 (横:縦 = 12:10 を維持)
            w, h = 17, 14
            data = svg.replace("{{color}}", color).encode("utf-8")
            renderer = QSvgRenderer(QByteArray(data))
            pix = QPixmap(w, h)
            pix.fill(QColor(0, 0, 0, 0))
            p = QPainter(pix)
            if opacity < 1.0:
                p.setOpacity(opacity)
            renderer.render(p)
            p.end()
            return QIcon(pix)
        if kind.startswith("volume_"):
            return make_icon(svg, size=20, color=color, opacity=opacity)
        if kind in ("max", "restore"):
            # max/restore は 12×12 で他アイコンより一回り大きく描画。
            return make_icon(svg, size=12, color=color, opacity=opacity)
        return make_icon(svg, size=10, color=color, opacity=opacity)

    # 非アクティブ時のアイコン不透明度(0.0〜1.0)。半透明で「無効化されているが
    # 存在は分かる」ことを示す。
    INACTIVE_ICON_OPACITY = 0.35

    def update_panel_toggle_icon(self, is_open: bool):
        """右パネル開閉状態に応じてトグルボタンのアイコンを切り替える。

        inactive 状態(ウェルカム画面)の場合は半透明アイコンで描画する。
        """
        kind = "toggle_panel_open" if is_open else "toggle_panel_closed"
        # 現在の(_open/closed)状態を覚えておく(inactive→active 切替時の復元用)
        self._panel_toggle_kind = kind
        if getattr(self, "_panel_toggle_inactive", False):
            # 非アクティブ: 半透明アイコンで「見えるが無効」を表現
            self._btn_toggle_panel.setIcon(self._make_window_btn_icon(
                kind, T().TEXT.name(), opacity=self.INACTIVE_ICON_OPACITY))
        else:
            self._btn_toggle_panel.setIcon(self._make_window_btn_icon(kind, T().TEXT.name()))

    def set_panel_toggle_active(self, active: bool):
        """右パネルトグルボタンのアクティブ状態を切り替える。

        ウェルカム画面では右パネルが存在しないためトグル操作も無意味だが、
        setVisible(False) でボタンを消すと QHBoxLayout が詰まり、解析画面遷移時
        にウィンドウ操作ボタン群が左へ流れる動きが目立つ。これを避けるため、
        非アクティブ時もボタン領域(46×36)はレイアウトに残し、アイコンを
        半透明にして「見えるけれど操作できない」状態にする。

        active=True : 通常状態(不透明アイコン、クリック・ホバー有効、PointingHand)
        active=False: 非アクティブ(半透明アイコン、クリック・ホバー無効、Arrow)
        """
        self._panel_toggle_inactive = not active
        btn = self._btn_toggle_panel
        kind = getattr(self, "_panel_toggle_kind", "toggle_panel_open")
        if active:
            # 通常状態に復帰
            btn.setEnabled(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(self._make_window_btn_icon(kind, T().TEXT.name()))
        else:
            # 非アクティブ: 半透明アイコン + クリック不可 + 通常カーソル
            # ホバー疑似クラスは setEnabled(False) で発動しないので
            # QSS は変更不要(:hover ルールが :disabled 状態には適用されない)。
            btn.setEnabled(False)
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.setIcon(self._make_window_btn_icon(
                kind, T().TEXT.name(), opacity=self.INACTIVE_ICON_OPACITY))
    
    def update_max_restore_icon(self, is_maximized: bool):
        """最大化状態に応じて中央ボタンのアイコンを切り替える。"""
        kind = "restore" if is_maximized else "max"
        self._btn_max.setIcon(self._make_window_btn_icon(kind, T().TEXT.name()))
    
    def update_volume_icon(self, muted: bool):
        """音量0%/ミュート時のみ×マーク付き、それ以外は通常スピーカー。"""
        kind = "volume_off" if muted else "volume_on"
        self._btn_volume.setIcon(self._make_window_btn_icon(kind, T().TEXT.name()))

    def menu_just_closed(self, threshold_ms: int = 200) -> bool:
        """直近に何らかのメニューが閉じたばかりかを返す。
        メニュー popup 中にタイトルバー空きをクリックした際、ポップアップが
        閉じた直後にクリックが伝播してきてドラッグ移動が起動するのを防ぐ。
        """
        if self._last_any_menu_close_ms is None:
            return False
        from PyQt6.QtCore import QDateTime
        return (QDateTime.currentMSecsSinceEpoch()
                - self._last_any_menu_close_ms) < threshold_ms

    def menu_active(self) -> bool:
        """いずれかのメニューが現在 popup 表示中か。"""
        return getattr(self, "_active_menu_index", -1) >= 0
    
    def hit_button_at(self, pos: QPoint) -> bool:
        """指定位置(タイトルバー座標系)がボタンやメニュー領域上にあるかを返す。
        True ならドラッグ等の親側ハンドラはこの位置を無視すべき。
        """
        # メニューボタン領域
        for btn in self._menu_buttons:
            if btn.geometry().contains(pos):
                return True
        # 右端ウィンドウ操作ボタン(音量・右パネルトグル含む)
        # ただし無効化(setEnabled(False))されているボタンはドラッグを妨げない
        # よう hit 判定から外す。例: ウェルカム画面でのトグルパネルボタンは
        # 領域だけ確保されている状態で、その上でもウィンドウをドラッグできる
        # ようにする。
        for btn in (self._btn_volume, self._btn_toggle_panel,
                    self._btn_min, self._btn_max, self._btn_close):
            if not btn.isEnabled():
                continue
            if btn.geometry().contains(pos):
                return True
        return False

    def set_menu(self, index: int, qmenu):
        """指定インデックスのメニューボタンに QMenu を紐付ける。
        ボタンクリック時にボタンの直下から popup() を呼び出す。
        既に開いている状態でボタンを再クリックした場合は閉じる(トグル動作)。
        いずれかのメニューが開いている間は、他のメニューボタンに
        マウスホバーするだけで自動的にそちらへ切り替わる(Windows標準挙動)。

        実装上の注意: Qt は QMenu が popup 状態のとき、ボタン上のクリックを
        「ポップアップ外クリック扱い」で先に処理し、メニューを閉じてから
        ボタンの clicked シグナルを発火させる。そのため clicked 受信時には
        qmenu.isVisible() == False になっており、単純に isVisible で判定
        できない。直前のクローズ時刻を記録して「ごく直近に閉じたなら
        再表示しない」というガード方式を使う。

        ホバー切替の実装: QMenu が popup 表示中はマウスを grab するため、
        タイトルバーのボタン側 enterEvent は発火しない。代わりに QMenu の
        eventFilter で MouseMove を監視し、マウスがタイトルバーのいずれかの
        メニューボタン上に来たら手動で切替を起動する。
        """
        from PyQt6.QtCore import QPoint
        if not (0 <= index < len(self._menu_buttons)):
            return
        # メニューと index を保持(ホバー切替用)
        if self._menus is None:
            self._menus: list = [None] * len(self._menu_buttons)
            # 「現在開いているメニュー index」を追跡する状態
            self._active_menu_index: int = -1
        self._menus[index] = qmenu

        btn = self._menu_buttons[index]
        # 既存の clicked シグナルを念のため切断してから接続(再呼び出し対応)
        try:
            btn.clicked.disconnect()
        except TypeError:
            pass

        # 直近のクローズ時刻を保持(ボタンごと)
        # 200ms 以内のクリックは「閉じる動作の続き」とみなして再表示を抑制
        close_state = {"last_close_ms": 0}

        def _on_about_to_show(idx=index):
            # アクティブインデックスを更新
            self._active_menu_index = idx
            # ボタンの active プロパティを切り替え(:hover が効かない期間の代替)
            self._set_menu_btn_active(idx)

        def _on_about_to_hide(idx=index):
            from PyQt6.QtCore import QDateTime
            now_ms = QDateTime.currentMSecsSinceEpoch()
            close_state["last_close_ms"] = now_ms
            # 「いずれかのメニューが閉じた直近時刻」を共通で記録(全インスタンスで参照)
            self._last_any_menu_close_ms = now_ms
            # 自身が閉じる場合のみアクティブ解除(他メニューへの切替時は
            # _switch_menu_to が即座に新しい about_to_show で上書きする)
            if self._active_menu_index == idx:
                self._active_menu_index = -1
                self._set_menu_btn_active(-1)

        try:
            qmenu.aboutToShow.disconnect()
        except TypeError:
            pass
        qmenu.aboutToShow.connect(_on_about_to_show)
        try:
            qmenu.aboutToHide.disconnect()
        except TypeError:
            pass
        qmenu.aboutToHide.connect(_on_about_to_hide)

        def _show_menu(_checked=False, b=btn, m=qmenu, st=close_state):
            from PyQt6.QtCore import QPoint, QDateTime
            now = QDateTime.currentMSecsSinceEpoch()
            # 直前(200ms以内)に閉じたばかりなら再表示せず終了(=トグルで閉じる)
            if now - st["last_close_ms"] < 200:
                return
            # ボタン左下から表示(最近のアプリ標準)
            m.popup(b.mapToGlobal(QPoint(0, b.height())))

        btn.clicked.connect(_show_menu)
        # ホバー切替用: QMenu に eventFilter をインストール(初回のみ)
        # popup 中はマウスを QMenu が grab するため、ボタン側 Enter は発火しない。
        # QMenu の MouseMove から「マウスがタイトルバーの別ボタン上か」を判定する。
        if not getattr(qmenu, "_kizuki_hover_filter_installed", False):
            qmenu.installEventFilter(self)
            qmenu._kizuki_hover_filter_installed = True
            # ポップアップ枠外の塗り残し対策(共通ヘルパー使用)
            from gui.menus import style_qmenu
            style_qmenu(qmenu)

    def attach_submenu_filter(self, qmenu) -> None:
        """指定 QMenu にも MouseMove eventFilter を仕込み、ホバー切替の
        対象に含める。サブメニュー (= setting_menu 配下の rank_menu 等) や
        ファイル直下の new_menu などが現在 grab しているとき、別のトップ
        レベルメニュー (ファイル/表示/設定) ボタンへホバーで切り替わる。
        """
        if not getattr(qmenu, "_kizuki_hover_filter_installed", False):
            qmenu.installEventFilter(self)
            qmenu._kizuki_hover_filter_installed = True

    def _set_menu_btn_active(self, idx: int):
        """メニューボタンの active プロパティを切り替えて、
        QSS の [active="true"] スタイルを発動させる。
        idx == -1 で全ボタン非アクティブ。
        QMenu 表示中は Qt の :hover が効かないため、明示的なフラグで
        ホバー相当の見た目を維持する。
        """
        if self._menu_buttons is None:
            return
        for i, btn in enumerate(self._menu_buttons):
            new_state = (i == idx)
            if btn.property("active") != new_state:
                btn.setProperty("active", new_state)
                # プロパティ変更を反映させるため style を再適用
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def _switch_menu_to(self, new_index: int):
        """現在開いているメニューを閉じ、指定インデックスのメニューを開く。
        メニュー間ホバー切替で使用される。
        """
        from PyQt6.QtCore import QPoint
        if self._menus is None:
            return
        if not (0 <= new_index < len(self._menus)):
            return
        new_menu = self._menus[new_index]
        if new_menu is None:
            return
        # 現在開いているメニューを閉じる
        cur_idx = self._active_menu_index
        if 0 <= cur_idx < len(self._menus) and cur_idx != new_index:
            cur_menu = self._menus[cur_idx]
            if cur_menu is not None and cur_menu.isVisible():
                cur_menu.close()
        # 新しいメニューを開く
        new_btn = self._menu_buttons[new_index]
        new_menu.popup(new_btn.mapToGlobal(QPoint(0, new_btn.height())))

    def eventFilter(self, obj, ev):
        """メニュー popup 表示中のマウス移動を監視し、マウスが
        タイトルバーの別ボタン上に来たら自動的にそのメニューへ切り替える。
        トップレベルメニューだけでなくサブメニュー (rank_menu/rule_menu 等)
        が grab している場合も同様に切り替えできるよう、obj は QMenu で
        あれば判定対象とする。サブメニューには attach_submenu_filter で
        eventFilter を仕込む必要がある。
        """
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QMenu
        # メニュー popup 中は QMenu がマウスを grab しているので
        # MouseMove イベントが QMenu に届く。グローバル座標で判定する。
        if (ev.type() == QEvent.Type.MouseMove
                and getattr(self, "_active_menu_index", -1) >= 0
                and isinstance(obj, QMenu)):
            try:
                gpos = ev.globalPosition().toPoint()
                # 各メニューボタンの矩形(グローバル座標)上にあるか確認
                for i, btn in enumerate(self._menu_buttons):
                    btn_top = btn.mapToGlobal(btn.rect().topLeft())
                    from PyQt6.QtCore import QRect
                    btn_rect = QRect(btn_top, btn.size())
                    if btn_rect.contains(gpos):
                        if i != self._active_menu_index:
                            self._switch_menu_to(i)
                        break
            except Exception:
                pass
        return super().eventFilter(obj, ev)
    
    def apply_theme(self):
        t = T()
        # タイトルバーの下端ライン:
        #   親 #custom_titlebar に border-bottom を指定すると、子(ボタン群・
        #   ドラッグエリア・アプリアイコン)が 32px フル高さで描画されるため
        #   親の最下段 1px が完全に覆い隠されてしまう。
        # 対策として、全ての子要素に直接 border-bottom を持たせて統一する。
        # ObjectName セレクタで個別指定しているのは、クラス名セレクタ
        # (_CustomTitleBar)がサブクラス解決でマッチしない場合があるため。
        bd = t.BORDER.name()
        self.setStyleSheet(
            f"#custom_titlebar{{background:{t.TOOLBAR.name()};}}"
            f"#titlebar_app_icon{{background:{t.TOOLBAR.name()};"
            f"border-bottom:1px solid {bd};}}"
            f"#titlebar_drag_area{{background:{t.TOOLBAR.name()};"
            f"border-bottom:1px solid {bd};}}"
            # タイトルバーメニューボタン:
            # テキスト色は常に TEXT(メイン)で固定。ホバー/アクティブ時は
            # 背景色だけ変わり、テキスト色は変えない仕様。
            f"#titlebar_menu_btn{{background:transparent;"
            f"border:none;border-bottom:1px solid {bd};"
            f"color:{t.TEXT.name()};"
            f"padding:0 {self.MENU_ITEM_PADDING}px;font-size:14px;text-align:center;}}"
            # アクティブ(ポップアップ表示中)の見た目はホバーと同じ。
            # QMenu が表示中はマウスを grab するため Qt の :hover は効かない。
            # _set_menu_btn_active で active プロパティを切り替える。
            f"#titlebar_menu_btn:hover,#titlebar_menu_btn[active=\"true\"]{{"
            f"background:{t.BORDER2.name()};"
            f"border-bottom:1px solid {bd};}}"
            f"#titlebar_window_btn_min,#titlebar_window_btn_max,#titlebar_window_btn_close,"
            f"#titlebar_window_btn_toggle_panel,#titlebar_window_btn_volume{{"
            f"background:transparent;border:none;border-bottom:1px solid {bd};}}"
            f"#titlebar_window_btn_min:hover,#titlebar_window_btn_max:hover,"
            f"#titlebar_window_btn_toggle_panel:hover,#titlebar_window_btn_volume:hover{{"
            f"background:{t.BORDER2.name()};border-bottom:1px solid {bd};}}"
            f"#titlebar_window_btn_close:hover{{background:{t.RED.name()};"
            f"border-bottom:1px solid {t.RED.name()};}}"
        )
        # ── アプリアイコン: ロゴSVG(スプラッシュと共通)をテーマに応じて描画 ─────
        # 配置: gui/assets/logo_mark_{light,dark}.svg
        #   ※ スプラッシュ画面のマーク部分と同一ファイルを使用(共通化済み)
        # サイズ: 14×14(タイトルバー高さ36に対し控えめサイズ)
        # SVG読み込み失敗時はフォールバックで ACCENT色の角丸矩形を描く。
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        from PyQt6.QtCore import QRectF as _QRectF
        from pathlib import Path
        ICON_PX = 14
        # 高DPI対応のためデバイスピクセル比を反映
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm_size = max(1, int(round(ICON_PX * dpr)))
        pm = QPixmap(pm_size, pm_size)
        pm.fill(QColor(0, 0, 0, 0))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # テーマに応じて light/dark 版を選択
        theme_mode = "dark" if t.BG.lightness() < 128 else "light"
        svg_path = Path(__file__).parent.parent / "assets" / f"logo_mark_{theme_mode}.svg"
        rendered = False
        try:
            if svg_path.exists():
                renderer = QSvgRenderer(str(svg_path))
                if renderer.isValid():
                    renderer.render(p, _QRectF(0, 0, ICON_PX, ICON_PX))
                    rendered = True
        except Exception:
            pass
        if not rendered:
            # フォールバック: ACCENT色の角丸矩形
            p.setBrush(QColor(t.ACCENT.name()))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(0, 0, ICON_PX, ICON_PX, 2, 2)
        p.end()
        self._app_icon_lbl.setPixmap(pm)
        # ウィンドウ操作ボタンのアイコン色を更新
        self._btn_min.setIcon(self._make_window_btn_icon("min", t.TEXT.name()))
        # 最大化ボタンは現在の状態を維持(自前最大化フラグを優先)
        win = self.window()
        if win is None:
            is_max = False
        elif hasattr(win, "_is_pseudo_maximized"):
            is_max = win._is_pseudo_maximized() or win.isMaximized()
        else:
            is_max = win.isMaximized()
        self.update_max_restore_icon(is_max)
        self._btn_close.setIcon(self._make_window_btn_icon("close", t.TEXT.name()))
        # 右パネルトグルボタンも現在の状態を保ったまま色を更新
        # (MainWindow側の _right_panel_collapsed を参照、無ければopen扱い)
        if self._btn_toggle_panel is not None:
            is_collapsed = bool(getattr(win, "_right_panel_collapsed", False)) if win else False
            self.update_panel_toggle_icon(is_open=not is_collapsed)
        # 音量ボタンも現在の状態を保ったまま色を更新
        # (MainWindow側の _sound.muted / volume を参照、無ければON扱い)
        if self._btn_volume is not None:
            is_muted = False
            if win is not None and hasattr(win, "_sound"):
                snd = win._sound
                is_muted = bool(getattr(snd, "_muted", False)) or float(getattr(snd, "_volume", 1.0)) <= 0.0
            self.update_volume_icon(muted=is_muted)
