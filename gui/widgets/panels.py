"""
gui/widgets/panels.py — 右パネル系のウィジェット群。

依存: gui.theme, gui.fonts, gui.infra, gui.widgets.graph, core.analyzer, PyQt6.
panels は同一 widgets レイヤから graph.py のみ参照する。

提供:
- ScoreBoard: アゲハマ・プレイヤー名・目差・勝率バー
- _make_card: パネルカード生成ヘルパ
- InfoPanel: 右パネル全体(ScoreBoard + WinRateGraph + コメント領域)
- MetricLabel: 大きい数値表示ラベル
- BadgeWidget: 評価バッジ (最善/良手/緩手/疑問/悪手)
- _StoneIcon: 黒石/白石アイコン (小)
- _CrossFadeLabel: テキスト変更時にクロスフェードする QLabel
- MoveInfoCard: 着手情報カード(候補手・勝率・目差差・バッジ等)
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QScrollArea, QScrollBar, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QSize, QEvent,
    QVariantAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QPainterPathStroker,
    QPen, QBrush, QColor, QPixmap, QFont, QFontMetrics, QPalette,
)

from gui.theme import (
    T, EVAL_COLORS, LIGHT_BLUNDER_COLORS,
    SP_XS, SP_SM, SP_MD, SP_LG, SP_XL,
    PAD_CARD,
)
from gui.fonts import (
    F, Font_XS, Font_SM, Font_MD, Font_XXL,
    FontMono_SM, FontMono_LG,
)
from gui.infra import _profile, _profile_method, eval_badge_tuple
from gui.widgets.graph import WinRateGraph
from core.analyzer import MoveAnalysis


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

