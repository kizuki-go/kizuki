"""
gui/dialogs.py — モーダル/モードレスダイアログ群。

依存: gui.theme, gui.fonts, gui.icons, PyQt6.
他の gui 層から参照される側で、上層 (gui._mixins.*, gui.main_window) は import しない。

提供:
- ColorAdjustmentDialog: 評価色のリアルタイム調整(開発用)
- _WarningIconWidget: 警告アイコン (?, ! 等) の小ウィジェット
- _UnsavedChangesDialog: 未保存変更の確認ダイアログ
- _FirstLaunchRankDialog: 初回起動時の棋力選択モーダル (ESC無効)
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QListView,
    QFrame, QGroupBox, QScrollArea, QScrollBar, QSizePolicy,
    QColorDialog, QApplication, QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QPropertyAnimation, QEasingCurve, QSettings,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QPixmap,
    QFontMetrics, QRegion,
)
from PyQt6.QtSvg import QSvgRenderer

from gui.theme import T, EVAL_COLORS, LIGHT_BLUNDER_COLORS
from gui.fonts import Font_XS, Font_SM, Font_LG, Font_XL
from gui.icons import icon_button_qss, _get_chevron_down_path


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

        # Phase 7: 履歴ボタンは後段で生成。先に None で置いて
        # _update_history_buttons の hasattr() ガードを `is not None` にする。
        self._btn_undo = None
        self._btn_redo = None

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
        if self._btn_undo is not None:
            self._btn_undo.setEnabled(len(self._undo_stack) > 0)
        if self._btn_redo is not None:
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

        # Phase 7: × ボタンは後段で生成。先に None で置いて
        # _reposition_close_x の hasattr() ガードを `is not None` にする。
        self._close_x_btn = None

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
        if self._close_x_btn is None:
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

