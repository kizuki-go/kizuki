"""
gui/startup.py — アプリ起動シーケンス。

依存: gui.theme, gui.fonts, core.katago_engine, PyQt6.
MainWindow への参照は循環防止のため main() 内で lazy import する。

提供:
- _check_models_or_exit: katago/models/ 配下のモデル数を確認、不正ならダイアログ表示で終了
- _SplashScreen: 起動中スプラッシュ画面
- main: アプリのメイン エントリポイント
- _build_startup_engine: 起動時に KataGoEngine を構築 (start() は呼ばない)
- _EngineStartupWorker: KataGoEngine.start() をバックグラウンドで実行する QThread
"""
from __future__ import annotations
import sys
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication, QWidget, QMessageBox
from PyQt6.QtCore import (
    Qt, QEventLoop, QPropertyAnimation, QEasingCurve, QSettings, QThread, QTimer,
    QVariantAnimation, QPointF, QRectF,
)
from PyQt6.QtGui import QPainter, QBrush, QPen
from PyQt6.QtSvg import QSvgRenderer

from core.katago_engine import KataGoEngine
from gui.theme import T, _theme
from gui.fonts import Font_XS, Font_XL

logger = logging.getLogger(__name__)


def _check_models_or_exit():
    """katago/models/ 直下の .bin.gz の個数をチェックする。

    ちょうど1個ならそのまま return。
    0個または複数個ならエラーダイアログを表示してアプリを終了する。
    QApplication が既に生成されている前提で呼ばれる。
    """
    from pathlib import Path
    from PyQt6.QtWidgets import QMessageBox
    from gui.main_window import MainWindow

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
    from gui.main_window import MainWindow
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
    from gui.main_window import MainWindow
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




if __name__ == "__main__":
    main()
