"""
gui/_mixins/window_mgmt.py — ウィンドウ管理・サイズ変更・最大化/最小化・パネル開閉アニメを
MainWindow に提供する Mixin。

依存: gui.theme, gui.widgets.navbar, ctypes (Win11 角丸 DWM API), PyQt6.

提供メソッド: ウィンドウのライフサイクル系全般。closeEvent / resizeEvent /
changeEvent / showEvent といった Qt イベントハンドラも含む。Mixin が MRO の
QMainWindow より前に並ぶため、Qt はこれらを通常通り検出する。
"""
from __future__ import annotations
import ctypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtCore import (
    Qt, QEvent, QPoint, QRect, QSettings, QTimer,
    QEasingCurve, QPropertyAnimation, QParallelAnimationGroup,
    QVariantAnimation,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QScrollArea, QMainWindow,
    QGraphicsEffect, QGraphicsOpacityEffect,
)

from gui.theme import T, SP_XS, R_MD
from gui.widgets.navbar import NavBar


class WindowMgmtMixin:
    """ウィンドウ管理・サイズ変更・最大化/最小化・パネル開閉アニメを MainWindow に提供する Mixin。"""

    def closeEvent(self: "MainWindowProto", ev):
        """ウィンドウを閉じる際にスレッドとエンジンを安全に停止する。
        棋譜に未保存の変更がある場合はユーザーに確認ダイアログを出し、
        キャンセルが選ばれたらクローズを中止する。

        経路ごとの確認タイミング:
          ・× ボタン経由: _animated_close で先に確認済み (_close_confirmed=True)。
            ここでは確認スキップ。closeEvent ではエンジン停止のみ実行する。
          ・Alt+F4 / OS シャットダウン経由: closeEvent が直接呼ばれるため、
            ここで確認ダイアログを出す。

        補足: × ボタン経由でアニメ後の closeEvent 時に確認すると、ウィンドウは
        既に opacity=0 + 縮小済みで「閉じた」と見える状態のため、ダイアログだけ
        がデスクトップに浮いて見える不具合が起きる。それを避けるため、
        × ボタン経路では _animated_close で先に確認するように分離してある。
        """
        # × ボタン経由で既に確認済みならスキップ。直接 closeEvent が
        # 呼ばれた経路 (Alt+F4 等) のみここで確認する。
        if not getattr(self, "_close_confirmed", False):
            # 編集中のコメントを先にノードへ反映(これで dirty が確定する)
            if self._game_state is not None:
                try:
                    self._save_comment_if_editing(self._game_state.current_node)
                except Exception:
                    pass
            # 未保存の変更があれば確認(中止された場合はクローズも中止)
            if not self._confirm_discard_or_save():
                ev.ignore()
                return
        # プロファイラ有効時は最後の集計をフラッシュしてスレッド停止
        try:
            _profiler.stop()
        except Exception:
            pass
        # シグナルを切断してC++オブジェクト破棄後のemitを防ぐ
        try:
            self._ponder_result_signal.disconnect()
        except Exception:
            pass
        # エンジン停止
        if hasattr(self, '_engine') and self._engine:
            try:
                self._engine.stop_pondering()
                self._engine.stop()
            except Exception:
                pass
        ev.accept()

    def _apply_min_window_size(self: "MainWindowProto", panel_open: bool) -> None:
        """右パネル開閉状態に応じて setMinimumSize を切り替える。

        - panel_open=True : (_MIN_WIN_OPEN_W, _MIN_WIN_OPEN_H)
        - panel_open=False: 幅をパネル分(=_FP_MIN_W + _FP_MARGIN_X*2)縮める

        通常時の開閉アニメ呼び出し規約:
          - 閉じる時: アニメ前にこのメソッド(open=False)を呼ぶ
                     (Qt がアニメ後の小さい幅を阻止しないように先に下げる)
          - 開く時:  アニメ後にこのメソッド(open=True)を呼ぶ
                     (アニメ前に上げると現在幅 < 新min となり Qt がいきなり
                      ウィンドウをリサイズしてしまう)
        """
        if panel_open:
            new_w = self._MIN_WIN_OPEN_W
        else:
            new_w = self._MIN_WIN_OPEN_W - self._FP_MIN_W - self._FP_MARGIN_X * 2
        new_h = self._MIN_WIN_OPEN_H
        self.setMinimumSize(new_w, new_h)

    def _place_panels(self: "MainWindowProto"):
        """left の幅を碁盤用に設定し、情報パネル・ナビバーを配置する。
        情報パネルは left の右隣（碁盤とは重ならない）、
        ナビバーは碁盤エリア下部中央にフローティング。

        新2層構造: centralWidget(outer) 内の VBoxLayout で
        [_titlebar(36), _root_widget(残り)] と縦並び。
        本メソッドは _root_widget 内で絶対配置を行うため、
        rw/rh は _root_widget のサイズを使う。
        """
        root = getattr(self, "_root_widget", None)
        if root is None:
            return

        rw = root.width()
        rh = root.height()

        # 右パネルアニメ中は、最終形のroot幅を使ってレイアウト計算する。
        # これがないと開く時、アニメ初期(ウィンドウまだ狭い)に
        # max_board_w_by_panel = rw - _FP_MIN_W が小さくなり碁盤が一瞬縮む問題が起きる。
        rw_override = getattr(self, "_panel_anim_target_rw", None)
        if rw_override is not None and rw_override > 0:
            rw = rw_override

        if rw <= 0 or rh <= 0:
            return



        fp_m = self._FP_MARGIN          # 縦方向(上下)
        fp_mx = self._FP_MARGIN_X       # 横方向(左右)
        # 情報パネルの表示状態に応じて碁盤エリア幅を決定
        # ウェルカム画面では情報パネルが非表示 → left_col を全幅に広げる
        # 右パネル開閉アニメ中は、最終形の状態(target_visible)を優先する。
        # フェードアニメ中は実際にはまだ visible だが、レイアウト的には目標状態で
        # 計算しないと、閉じる時に碁盤が一瞬縮むなどの揺れが起きる。
        anim_target_visible = getattr(self, "_panel_anim_target_visible", None)
        if anim_target_visible is not None:
            panel_visible = anim_target_visible
        else:
            panel_visible = (hasattr(self, "_right_col")
                             and self._right_col.isVisible())

        # 碁盤エリアの利用可能高さ: rh - ナビバー領域
        nb_h_reserve = NavBar.NB_HEIGHT + NavBar.NB_MARGIN  # ナビバー + 下マージン
        avail_board_h = rh - nb_h_reserve

        if panel_visible:
            # ── レスポンシブ配置 (戦略B寄り) ──
            # 1) 碁盤エリアを正方形に保つ(board_w == board_h)。
            #    幅と高さの小さい方に合わせる。
            #    ・縦方向の上限: avail_board_h
            #    ・横方向の上限: rw - _FP_MIN_W - fp_mx*2 (パネル MIN を確保)
            # 2) パネルは余り横幅すべてを引き取る(MIN 以上、上限なし)。
            #    ウィンドウが横に広い場合、パネルが画面右端まで広がる。
            max_board_w_by_panel = rw - self._FP_MIN_W - fp_mx * 2
            board_size = min(avail_board_h, max_board_w_by_panel)
            board_w = board_size
            board_h = board_size
            # 余り幅をパネルに全て割り当て(MIN 以上を保証)
            remaining = rw - board_w - fp_mx * 2
            fp_w = max(self._FP_MIN_W, remaining)
            # right_x は画面右端から逆算(右側 fp_mx の余白を保証)
            right_x = rw - fp_w - fp_mx
            _fp_inner_w = fp_w
        else:
            # パネル非表示時は全幅を碁盤エリアに使う(従来通り)
            board_w = rw
            board_h = avail_board_h

        # ── left（碁盤エリア）─────────────────────────────────────────────
        # 碁盤+ナビバーをひとまとめにして上下中央に配置する。
        # 横方向制約で board_h が avail_board_h より小さくなった場合、
        # 余った縦スペースを上下に等分して上に top_offset を確保する。
        board_block_h = board_h + nb_h_reserve  # 碁盤 + ナビバー領域
        top_offset = max(0, (rh - board_block_h) // 2)
        if hasattr(self, "_left_col"):
            self._left_col.setGeometry(0, 0, board_w, rh)
        if hasattr(self, "_left_stack"):
            self._left_stack.setGeometry(0, top_offset, board_w, board_h)

        # ── 情報パネル ────────────────────────────────────────────────────
        if panel_visible and hasattr(self, "_right_col"):
            fp_h = max(200, rh - fp_m * 2)
            self._right_col.setGeometry(right_x, fp_m, fp_w, fp_h)
            self._right_col.raise_()
            if hasattr(self, "_cards_scroll"):
                self._cards_scroll.setMaximumWidth(_fp_inner_w)
                w = self._cards_scroll.widget()
                if w:
                    w.setMaximumWidth(_fp_inner_w)

        # ── ナビバー（碁盤に少し食い込ませる） ────────────────────────────
        # 碁盤エリアの下端はそのままだが、ナビバーを 8px 上にオフセットして
        # 碁盤の下マージン部分(罫線より外側の木目余白)に重ねる。
        # これによりスライダーが碁盤の罫線にもう少し近づいて見える。
        # 値は NavBar.NB_OVERLAP として定数化。
        nb_overlap = NavBar.NB_OVERLAP
        if hasattr(self, "_navbar"):
            nb_h = NavBar.NB_HEIGHT
            nb_y = top_offset + board_h - nb_overlap  # 碁盤に食い込ませる
            self._navbar.setGeometry(0, nb_y, board_w, nb_h)
            self._navbar.raise_()

        # ── コメントオーバーレイ（ナビバーの直上） ───────────────────────
        if hasattr(self, "_comment_overlay"):
            ov_h = 100
            ov_w = board_w - 60  # 碁盤エリア幅 - 60px
            ov_x = (board_w - ov_w) // 2  # 中央揃え
            # ナビバーが上にオフセットしたので、オーバーレイ下端 = ナビバー上端
            ov_y = top_offset + board_h - nb_overlap - ov_h
            # コメントオーバーレイの開閉アニメ中はジオメトリをアニメに任せる
            # (setGeometry で上書きするとアニメが破綻する)
            if not getattr(self, "_comment_anim_running", False):
                self._comment_overlay.setGeometry(ov_x, ov_y, ov_w, ov_h)
            self._comment_overlay.raise_()
            # フェードオーバーレイをコメントオーバーレイ全体に追従させる
            if hasattr(self, "_comment_fade_overlay"):
                self._comment_fade_overlay.setGeometry(0, 0, ov_w, ov_h)
                self._comment_fade_overlay.raise_()
                self._comment_fade_overlay.update_visibility()
            # 閉じるボタンを右上に配置(フェードより手前)
            if hasattr(self, "_comment_close_btn"):
                btn = self._comment_close_btn
                btn.move(ov_w - btn.width() - SP_XS, SP_XS)
                btn.raise_()

        # ── D&D オーバーレイの追従 ─────────────────────────────
        # _root_widget の子なのでサイズ更新だけ行えばよい(タイトルバーは
        # 自動で除外される)。表示中のときのみカード位置も再計算する。
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.setGeometry(0, 0, rw, rh)
            if self._drop_overlay.isVisible():
                cw = self._drop_card.width()
                ch = self._drop_card.height()
                self._drop_card.move((rw - cw) // 2, (rh - ch) // 2)
                self._drop_overlay.raise_()

    def showEvent(self: "MainWindowProto", ev):
        """初回表示時にフローティングパネルを配置する。"""
        super().showEvent(ev)
        # 0ms: イベントループ開始直後（ウィンドウサイズ確定後）に配置
        # 50ms: DWM / ウィンドウマネージャのフレーム確定後に再配置（Windows対策）
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0,  self._place_panels)
        QTimer.singleShot(50, self._place_panels)
        # コメントオーバーレイのプリウォームは起動アニメ完了後に遅らせる
        # (アニメ中に走るとカクつくため)。500ms 後 = アニメ完了後。
        if not getattr(self, "_overlay_prewarmed", False):
            QTimer.singleShot(500, self._do_prewarm_overlay_once)
        # Windows 11 ネイティブ角丸を有効化(初回のみ)。
        # winId() がここで確定しているため、 __init__ ではなく showEvent で行う。
        if not getattr(self, "_win11_corners_applied", False):
            self._win11_corners_applied = True
            self._enable_win11_rounded_corners()
        # 初回表示時にフェードインアニメを実行(初回のみ、最小化からの
        # 復元では呼ばない)。
        # showEvent 直後だと _place_panels(0ms,50ms) と競合してカクつくため、
        # 80ms 遅延させてレイアウト確定後に開始する。
        if not getattr(self, "_startup_anim_done", False):
            self._startup_anim_done = True
            QTimer.singleShot(80, self._animated_show_on_startup)

    def _enable_win11_rounded_corners(self: "MainWindowProto"):
        """Windows 11 で window corner を rounded にする(OS側で描画)。
        Win10 では attribute_key=33 が認識されず E_INVALIDARG が返るだけで、
        例外も視覚効果の変化もない(矩形のまま)。Win11 build 22000 以降で
        効果が現れる。Mac/Linux では win32 ではないので即時 return。
        """
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2  # 値 2 = 通常の角丸(他: 0=Default, 1=DoNotRound, 3=RoundSmall)
            hwnd = int(self.winId())
            value = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            # dwmapi.dll が無い環境(Wine 等)でも害なくスルー
            pass

    def _do_prewarm_overlay_once(self: "MainWindowProto"):
        """プリウォームを最初の1回だけ実行する。"""
        if getattr(self, "_overlay_prewarmed", False):
            return
        self._overlay_prewarmed = True
        self._prewarm_comment_overlay()

    def _toggle_maximized(self: "MainWindowProto"):
        """最大化↔復元 を切り替える。タイトルバーの[□]ボタンから呼ばれる。

        実装方針: QPropertyAnimation で geometry を300msかけて補間 +
        アニメ中はウィンドウ最前面に背景色のオーバーレイをかぶせて、
        ウィンドウの中身を完全に隠蔽する。

        - frameless ウィンドウでは Qt の showMaximized() は OS のアニメが
          動かないため、自前で補間アニメを行う。
        - ジオメトリ変化に伴う子ウィジェットの再描画タイミングのズレが
          カクつきとして見えるため、アニメ中は背景色オーバーレイで画面
          を覆って中身を見えなくする。
        - オーバーレイは MainWindow の centralWidget の子にして、
          ウィンドウリサイズに valueChanged で追従させる。
        - 連打防止: _anim_in_progress フラグで保護
        - 角丸切替: 完了時に実行
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QEasingCurve, QRect,
        )
        # 既にアニメ中なら無視(連打対策)
        if getattr(self, "_anim_in_progress", False):
            return
        if self._is_pseudo_maximized():
            # 復元
            target_geom = getattr(self, "_pre_max_geometry", None)
            if target_geom is None:
                # 念のためのフォールバック(復元先がない場合は中央配置)
                screen = self.screen() or QApplication.primaryScreen()
                avail = screen.availableGeometry()
                w = min(self.width(), int(avail.width() * 0.7))
                h = min(self.height(), int(avail.height() * 0.7))
                target_geom = QRect(
                    avail.x() + (avail.width() - w) // 2,
                    avail.y() + (avail.height() - h) // 2,
                    w, h,
                )
            new_max_state = False
            self._pseudo_max_active = False
        else:
            # 最大化: 現在のジオメトリを保存し、availableGeometry へ拡大
            self._pre_max_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            target_geom = screen.availableGeometry()
            new_max_state = True
            self._pseudo_max_active = True

        # アニメ中フラグ
        self._anim_in_progress = True
        # タイトルバーアイコンは即時更新(視覚的な応答性のため)
        if hasattr(self, "_titlebar"):
            self._titlebar.update_max_restore_icon(new_max_state)

        # ── 不透明オーバーレイを作成(背景色で塗りつぶし) ──
        # アニメ中ずっと完全不透明で表示し、ウィンドウ中身を見えなくする。
        # ウィンドウと同じ角丸 R_MD(12px) を指定して、ウィンドウの角と
        # オーバーレイの角がぴったり一致するようにする。
        from PyQt6.QtWidgets import QWidget
        cw = self.centralWidget()
        if cw is not None:
            overlay = QWidget(cw)
            overlay.setObjectName("max_anim_overlay")
            theme_bg = T().BG
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            # StyleSheet で背景色 + 角丸を設定
            overlay.setStyleSheet(
                f"#max_anim_overlay {{"
                f"  background-color: rgb({theme_bg.red()},{theme_bg.green()},{theme_bg.blue()});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )
            overlay.setGeometry(0, 0, cw.width(), cw.height())
            overlay.raise_()
            overlay.show()
            self._max_overlay = overlay
        else:
            self._max_overlay = None

        # ── アニメ中、_root_widget の再描画を停止して滑らかさを確保 ──
        # ジオメトリアニメ中は中身(碁盤・パネル・ナビバー等)の resizeEvent /
        # paintEvent が毎フレーム走り、これがCPU/GPU負荷とフレームスキップの
        # 原因になる。オーバーレイで完全に隠蔽している間は中身を描画する
        # 必要がないので、_root_widget を setUpdatesEnabled(False) にして
        # 内部の再描画を抑制する。
        # _titlebar とオーバーレイは _root_widget の兄弟(centralWidget の子)
        # なので、この設定の影響を受けず引き続き描画される。
        # アニメ完了時に setUpdatesEnabled(True) + update() で復帰する。
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(False)

        # ── ジオメトリアニメ(300ms、OutCubic) ──
        DURATION = 300
        geom_anim = QPropertyAnimation(self, b"geometry")
        geom_anim.setDuration(DURATION)
        geom_anim.setStartValue(self.geometry())
        geom_anim.setEndValue(target_geom)
        geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # フレームごとにオーバーレイをウィンドウサイズへ追従
        geom_anim.valueChanged.connect(self._update_overlay_geometry)
        geom_anim.finished.connect(lambda: self._on_maximize_anim_done(new_max_state))
        self._max_anim = geom_anim
        geom_anim.start()

    def _update_overlay_geometry(self: "MainWindowProto", _v=None):
        """オーバーレイをウィンドウサイズに追従させる。"""
        ov = getattr(self, "_max_overlay", None)
        if ov is not None:
            cw = self.centralWidget()
            if cw is not None:
                ov.setGeometry(0, 0, cw.width(), cw.height())
                ov.raise_()

    def _on_maximize_anim_done(self: "MainWindowProto", is_now_maximized: bool = False):
        """最大化/復元アニメ完了時の後処理。
        ジオメトリアニメは完了したが、オーバーレイをフェードアウトさせる。
        フェードアウト完了後にオーバーレイを破棄する。
        """
        # 最終形状で _place_panels を1回実行(レイアウト確定)
        # この時点では _root_widget は setUpdatesEnabled(False) のままなので
        # 内部の paintEvent はまだ走らない。
        self._place_panels()
        # 角丸切替: 最大化中は矩形、復元なら角丸復活
        self._set_win11_corner_round(not is_now_maximized)

        # ── _root_widget の再描画を再開 ──
        # _toggle_maximized でアニメ開始時に setUpdatesEnabled(False) にして
        # いるので、ここで True に戻して update() で再描画を強制する。
        # オーバーレイはまだ完全不透明で被さっているので、再描画中の
        # ちらつきは見えない(これからフェードアウトしていく間に下に
        # 整った中身が表示される)。
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(True)
            self._root_widget.update()

        # オーバーレイのフェードアウト(150ms)
        ov = getattr(self, "_max_overlay", None)
        if ov is None:
            self._anim_in_progress = False
            return

        # rgba の alpha 値を 255 → 0 にアニメ
        # alpha値を動的に変えるため、Qオブジェクトのプロパティを介して
        # QPropertyAnimation で操作する。シンプルにタイマー駆動でstyleSheet
        # を再設定する方式の方が確実なので、そちらを採用する。
        from PyQt6.QtCore import QTimer
        FADE_OUT_MS = 150
        FRAME_INTERVAL_MS = 16  # 約60fps
        STEPS = max(1, FADE_OUT_MS // FRAME_INTERVAL_MS)
        theme_bg = T().BG
        bg_r, bg_g, bg_b = theme_bg.red(), theme_bg.green(), theme_bg.blue()

        self._max_overlay_fade_step = 0
        self._max_overlay_fade_timer = QTimer(self)
        self._max_overlay_fade_timer.setInterval(FRAME_INTERVAL_MS)

        def _fade_step():
            self._max_overlay_fade_step += 1
            t = self._max_overlay_fade_step / STEPS
            if t >= 1.0:
                # フェードアウト完了
                self._max_overlay_fade_timer.stop()
                self._max_overlay_fade_timer.deleteLater()
                ov2 = getattr(self, "_max_overlay", None)
                if ov2 is not None:
                    ov2.hide()
                    ov2.deleteLater()
                    self._max_overlay = None
                self._anim_in_progress = False
                return
            # OutCubic イージング: 1 - (1-t)^3
            eased = 1 - (1 - t) ** 3
            alpha = int(255 * (1 - eased))  # 255 → 0
            ov.setStyleSheet(
                f"#max_anim_overlay {{"
                f"  background-color: rgba({bg_r},{bg_g},{bg_b},{alpha});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )

        self._max_overlay_fade_timer.timeout.connect(_fade_step)
        self._max_overlay_fade_timer.start()

    def _detect_taskbar_direction(self: "MainWindowProto") -> str:
        """タスクバーの位置(画面のどの辺にあるか)を推定する。
        戻り値: "bottom" / "top" / "left" / "right"
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return "bottom"
        avail = screen.availableGeometry()
        full = screen.geometry()
        # avail と full の差からタスクバーの場所を判定
        if avail.bottom() < full.bottom():
            return "bottom"
        elif avail.top() > full.top():
            return "top"
        elif avail.x() > full.x():
            return "left"
        elif avail.right() < full.right():
            return "right"
        return "bottom"

    def _toggle_right_panel(self: "MainWindowProto"):
        """右パネルの開閉をトグルする。
        - 通常時: ウィンドウ自体の横幅を 300ms かけて変化させる(左端固定)
        - 最大化中: ウィンドウは変えず、パネル領域だけ即座に hide/show する
        - アニメ中の連打は無視
        """
        if getattr(self, "_panel_anim_running", False):
            return
        if not hasattr(self, "_right_col"):
            return

        # 現在の状態を反転
        currently_collapsed = getattr(self, "_right_panel_collapsed", False)
        new_collapsed = not currently_collapsed
        self._right_panel_collapsed = new_collapsed

        # アイコン即座に更新
        if hasattr(self, "_titlebar"):
            self._titlebar.update_panel_toggle_icon(is_open=not new_collapsed)

        # QSettings に保存
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("right_panel_collapsed", new_collapsed)

        # 最大化中: ウィンドウ幅は変えず、内部要素だけアニメ
        # - _right_col の opacity をフェード
        # - _left_stack の幅を新しい board_w へアニメ
        # で「パネル消失 + 碁盤の中央移動」を滑らかに演出する。
        if self._is_pseudo_maximized() or self.isMaximized():
            self._animate_max_panel_toggle(new_collapsed)
            return

        # 通常時: ウィンドウ幅をアニメで変える(左端固定)
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect
        cur_geo = self.geometry()
        # パネル幅を計算(現在の panel 幅 or 既定値)
        panel_w = self._right_col.width() if self._right_col.isVisible() else self._last_panel_width
        if panel_w <= 0:
            panel_w = self._FP_MIN_W + self._FP_MARGIN_X * 2

        if new_collapsed:
            # 閉じる: パネル分縮める
            # 閉じた状態の最小ウィンドウ幅(_apply_min_window_size と整合)
            closed_min_w = (self._MIN_WIN_OPEN_W
                            - self._FP_MIN_W - self._FP_MARGIN_X * 2)
            # cur_geo.width() - panel_w が closed_min_w を下回ると、
            # アニメ後に Qt が min サイズに強制リサイズして「再オープン時に
            # ウィンドウ幅が想定より広くなる → パネル幅も広がる」事故が起きる。
            # よってここでは _FP_MIN_W ではなく closed_min_w を下限にする。
            new_w = max(closed_min_w, cur_geo.width() - panel_w)
            target_geo = QRect(cur_geo.x(), cur_geo.y(), new_w, cur_geo.height())
            # 復元用に保存するのは「実際にアニメで縮めた幅」(= cur幅 - new_w)。
            # panel_w 自体ではなく、ウィンドウ幅の差を記録することで、
            # closed_min_w で頭打ちされた場合でも開いた時に元のウィンドウ幅
            # に戻れるようにする。
            self._last_panel_width = cur_geo.width() - new_w
            # 閉じる時はパネルをすぐ隠さず、フェードアウトしてから完了時に隠す
            # 最小ウィンドウサイズを「閉じた状態」のサイズに先に下げる
            # (Qt がアニメ後の小さい幅を阻止しないように)
            self._apply_min_window_size(panel_open=False)
        else:
            # 開く: パネル分広げる + パネル再表示
            new_w = cur_geo.width() + panel_w
            # 画面端を超えないようクリップ
            from PyQt6.QtWidgets import QApplication
            screen = self.screen() or QApplication.primaryScreen()
            avail = screen.availableGeometry()
            max_w = avail.x() + avail.width() - cur_geo.x()
            new_w = min(new_w, max_w)
            target_geo = QRect(cur_geo.x(), cur_geo.y(), new_w, cur_geo.height())
            # パネルを表示してからアニメ
            self._right_col.setVisible(True)

        # アニメ中は _place_panels の rw 計算を最終形ウィンドウ幅で固定する。
        # 開く時、アニメ初期でウィンドウがまだ狭いと碁盤が一瞬縮んで見えるのを防ぐ。
        # root_widget の幅 = ウィンドウ幅(タイトルバー以外なので親と同じ横幅)
        self._panel_anim_target_rw = new_w
        # アニメ中の論理的な panel_visible 状態(目標状態)。
        # フェードアウト中は実際にはまだ visible だが、レイアウト計算では
        # 目標状態(=非表示)を使う。
        self._panel_anim_target_visible = (not new_collapsed)

        self._panel_anim_running = True

        # ── ジオメトリアニメ(ウィンドウ幅) ──
        from PyQt6.QtCore import QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        geo_anim = QPropertyAnimation(self, b"geometry")
        geo_anim.setDuration(300)
        geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        geo_anim.setStartValue(cur_geo)
        geo_anim.setEndValue(target_geo)
        # アニメ中は毎フレーム _place_panels で内部レイアウトを追従
        geo_anim.valueChanged.connect(lambda _v: self._place_panels())

        # ── 右パネル opacity アニメ ──
        # 過去 QGraphicsOpacityEffect 単独では黒帯バグが発生したが、
        # アニメ中だけ autoFillBackground=False にすることで合成挙動を変え、
        # 黒帯を回避できないか検証する。
        # 閉じる: 1.0 → 0.0、開く: 0.0 → 1.0
        self._right_col.setAutoFillBackground(False)
        opacity_eff = QGraphicsOpacityEffect(self._right_col)
        opacity_eff.setOpacity(1.0 if new_collapsed else 0.0)
        self._right_col.setGraphicsEffect(opacity_eff)
        self._panel_opacity_effect = opacity_eff  # 完了時に解除するため保持
        opacity_anim = QPropertyAnimation(opacity_eff, b"opacity")
        opacity_anim.setDuration(300)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity_anim.setStartValue(1.0 if new_collapsed else 0.0)
        opacity_anim.setEndValue(0.0 if new_collapsed else 1.0)

        # ── 並列実行 ──
        group = QParallelAnimationGroup(self)
        group.addAnimation(geo_anim)
        group.addAnimation(opacity_anim)
        group.finished.connect(self._on_panel_anim_done)
        self._panel_anim = group
        group.start()

    def _animate_max_panel_toggle(self: "MainWindowProto", new_collapsed: bool):
        """最大化中の右パネル開閉アニメ。
        ウィンドウ幅は変えられないので、内部要素を 300ms でアニメする。
        - 開く時: パネルをfade in、_left_stack を新 board_w に縮める
        - 閉じる時: パネルをfade out、_left_stack を全幅に広げる

        注意: アニメ中、_left_stack のジオメトリ補間で内部の碁盤に水平方向の
        ティアリング(絵が分割表示される現象)が発生することがある。これは
        Qt + Windows DWM の組み合わせにおけるフレーム合成タイミングの制約
        によるもので、setUpdatesEnabled / setFixedSize / QPixmap オーバーレイ
        など複数の対策を試したが完全に消すのは困難なため、現状は受容する。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        # 閉じる時はアニメ前に最小ウィンドウサイズを下げる
        # (通常時の挙動と一貫させる)
        if new_collapsed:
            self._apply_min_window_size(panel_open=False)

        # 現在のジオメトリを保持
        cur_left_stack_geo = self._left_stack.geometry() if hasattr(self, "_left_stack") else None
        if cur_left_stack_geo is None:
            # フォールバック: アニメせず即時切替
            self._right_col.setVisible(not new_collapsed)
            # 開く時の min 拡大は _on_panel_anim_done で行うが、ここを通った場合は
            # _on_panel_anim_done が呼ばれないので直接適用する
            if not new_collapsed:
                self._apply_min_window_size(panel_open=True)
            self._place_panels()
            return

        # 開く時はパネルを先に表示してフェードイン
        if not new_collapsed:
            self._right_col.setVisible(True)

        # 最終形のレイアウトを計算するため、target_visible を仮設定して
        # _place_panels の panel_visible 判定を上書き → 最終形の board_w を取得
        prev_target_visible = getattr(self, "_panel_anim_target_visible", None)
        self._panel_anim_target_visible = (not new_collapsed)
        # 一度通常の _place_panels を呼んで「最終形のジオメトリ」を取得する
        # → 直後にアニメ開始時点に戻すため、現状のジオメトリを保存しておく
        cur_lc_geo = self._left_col.geometry() if hasattr(self, "_left_col") else None
        cur_nb_geo = self._navbar.geometry() if hasattr(self, "_navbar") else None
        self._place_panels()
        target_left_stack_geo = self._left_stack.geometry()
        target_lc_geo = self._left_col.geometry() if hasattr(self, "_left_col") else None
        target_nb_geo = self._navbar.geometry() if hasattr(self, "_navbar") else None
        # アニメ開始時点(現状)のジオメトリに戻す
        # ※ _left_stack だけでなく _left_col / _navbar も戻さないと、最終形の
        #    全幅で配置されたままになり、パネルが裏に隠れてフェードが見えなくなる。
        self._left_stack.setGeometry(cur_left_stack_geo)
        if cur_lc_geo is not None:
            self._left_col.setGeometry(cur_lc_geo)
        if cur_nb_geo is not None:
            self._navbar.setGeometry(cur_nb_geo)

        # ── Z順序制御 ──
        # 閉じる時: 碁盤エリアを前面に出す。アニメで碁盤が広がるとき、その
        #          下にあるパネルが「碁盤に少しずつ覆い被されながらフェード
        #          アウト」する見え方になる。重なり時に色が混ざることもない。
        # 開く時:   パネルを前面に出す。フェードインしながら碁盤の上に正しく
        #          描画される。
        if new_collapsed:
            if hasattr(self, "_left_col"):
                self._left_col.raise_()
            if hasattr(self, "_left_stack"):
                self._left_stack.raise_()
            if hasattr(self, "_navbar"):
                self._navbar.raise_()
        else:
            if hasattr(self, "_right_col"):
                self._right_col.raise_()

        self._panel_anim_running = True

        # ── _left_stack の geometry アニメ ──
        ls_anim = QPropertyAnimation(self._left_stack, b"geometry")
        ls_anim.setDuration(300)
        ls_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        ls_anim.setStartValue(cur_left_stack_geo)
        ls_anim.setEndValue(target_left_stack_geo)

        # ── _left_col(碁盤エリアの背景)の geometry も連動させる ──
        anims = [ls_anim]
        if hasattr(self, "_left_col"):
            cur_lc_geo = self._left_col.geometry()
            target_lc_geo = QRect(0, 0, target_left_stack_geo.width(), cur_lc_geo.height())
            lc_anim = QPropertyAnimation(self._left_col, b"geometry")
            lc_anim.setDuration(300)
            lc_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            lc_anim.setStartValue(cur_lc_geo)
            lc_anim.setEndValue(target_lc_geo)
            anims.append(lc_anim)

        # ── ナビバーも碁盤幅に合わせて連動 ──
        if hasattr(self, "_navbar") and self._navbar.isVisible():
            cur_nb_geo = self._navbar.geometry()
            # ナビバーは碁盤エリア下端中央(0オフセットで board_w 全幅)
            target_nb_geo = QRect(0, cur_nb_geo.y(),
                                  target_left_stack_geo.width(), cur_nb_geo.height())
            nb_anim = QPropertyAnimation(self._navbar, b"geometry")
            nb_anim.setDuration(300)
            nb_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            nb_anim.setStartValue(cur_nb_geo)
            nb_anim.setEndValue(target_nb_geo)
            anims.append(nb_anim)

        # ── パネルの opacity アニメ ──
        opacity_eff = QGraphicsOpacityEffect(self._right_col)
        opacity_eff.setOpacity(1.0 if new_collapsed else 0.0)
        self._right_col.setGraphicsEffect(opacity_eff)
        self._panel_opacity_effect = opacity_eff
        op_anim = QPropertyAnimation(opacity_eff, b"opacity")
        op_anim.setDuration(300)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        op_anim.setStartValue(1.0 if new_collapsed else 0.0)
        op_anim.setEndValue(0.0 if new_collapsed else 1.0)
        anims.append(op_anim)

        # ── パネルの位置スライドアニメ ──
        # フェードと並列に位置を動かすことで「スライドイン/アウト + フェード」を演出する。
        # 閉じる時: 現在位置 → 画面右端の外側へ右にスライドアウト
        # 開く時:   画面右端の外側 → 最終位置へ左にスライドイン
        cur_panel_geo = self._right_col.geometry()
        # 画面外右側の位置(rw 相当を root_widget の右端から取得)
        rw_outside = (self._root_widget.width()
                      if hasattr(self, "_root_widget") and self._root_widget
                      else cur_left_stack_geo.x() + cur_left_stack_geo.width())
        if new_collapsed:
            # 閉じる: 現在位置から画面外へスライドアウト
            start_panel_geo = cur_panel_geo
            target_panel_geo = QRect(rw_outside, cur_panel_geo.y(),
                                     cur_panel_geo.width(), cur_panel_geo.height())
        else:
            # 開く: 画面外から最終位置へスライドイン
            # 最終位置は _place_panels(panel_visible=True) 時の right_x
            # = root幅 - パネル幅 - fp_mx
            final_right_x = rw_outside - cur_panel_geo.width() - self._FP_MARGIN_X
            start_panel_geo = QRect(rw_outside, cur_panel_geo.y(),
                                    cur_panel_geo.width(), cur_panel_geo.height())
            target_panel_geo = QRect(final_right_x, cur_panel_geo.y(),
                                     cur_panel_geo.width(), cur_panel_geo.height())
            # 開始位置に即座に配置
            self._right_col.setGeometry(start_panel_geo)
        slide_anim = QPropertyAnimation(self._right_col, b"geometry")
        slide_anim.setDuration(300)
        slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide_anim.setStartValue(start_panel_geo)
        slide_anim.setEndValue(target_panel_geo)
        anims.append(slide_anim)

        # ── 並列実行 ──
        group = QParallelAnimationGroup(self)
        for a in anims:
            group.addAnimation(a)
        # 最大化中は target_rw オーバーライドは使わない
        self._panel_anim_target_rw = None
        group.finished.connect(self._on_panel_anim_done)
        self._panel_anim = group
        group.start()

    def _on_panel_anim_done(self: "MainWindowProto"):
        """右パネルアニメ完了時の後処理。
        - 幅オーバーライド・visible オーバーライドを解除
        - 閉じた場合は実際に setVisible(False)
        - opacity effect を解除(描画パイプラインの常時負荷を避ける)
        - 最小ウィンドウサイズを最終形に合わせて更新
          (開いた時のみ。閉じた時はアニメ前に既に下げてある)
        - アニメ中に False にした autoFillBackground を True に戻す
        """
        self._panel_anim_running = False
        # アニメ用のオーバーライドを解除
        self._panel_anim_target_rw = None
        target_vis = getattr(self, "_panel_anim_target_visible", None)
        self._panel_anim_target_visible = None
        # 閉じた場合: ここで初めて実際に非表示にする
        if hasattr(self, "_right_col") and target_vis is False:
            self._right_col.setVisible(False)
        # 開いた場合: 最小ウィンドウサイズを「開いた状態」に上げる
        # (閉じる時はアニメ前に既に下げているのでここでの操作不要)
        if target_vis is True:
            self._apply_min_window_size(panel_open=True)
        # opacity effect を解除して描画パイプラインの常時負荷を回避
        if hasattr(self, "_panel_opacity_effect") and self._panel_opacity_effect is not None:
            try:
                if hasattr(self, "_right_col"):
                    self._right_col.setGraphicsEffect(None)
            except Exception:
                pass
            self._panel_opacity_effect = None
        # アニメ中 False にしていた autoFillBackground を True に戻す
        # (角丸の外側に旧色が残らないようにするため)。
        if hasattr(self, "_right_col"):
            self._right_col.setAutoFillBackground(True)
        self._place_panels()

    def _animated_minimize(self: "MainWindowProto"):
        """最小化ボタン用カスタムアニメ。
        最大化アニメと同じ「不透明オーバーレイで中身を隠蔽 + 内部再描画停止 +
        ウィンドウのジオメトリ/透明度をアニメ」方式で、ガタつきを抑える。

        フロー:
          1. 不透明オーバーレイを最前面に被せ、ウィンドウ中身を隠蔽
          2. _root_widget.setUpdatesEnabled(False) で内部の再描画を停止
          3. windowOpacity 1.0→0.0 と geometry を「下方向に縮小+移動(沈む)」で
             同時アニメ(200ms / InCubic)
          4. 完了したら showMinimized() を呼び、ジオメトリ/透明度を復元、
             オーバーレイ破棄、内部再描画を再開
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect,
        )
        # 既にアニメ中なら無視(連打対策)
        if getattr(self, "_anim_in_progress", False):
            return
        # 既に最小化中なら何もしない
        if self.windowState() & Qt.WindowState.WindowMinimized:
            return

        self._anim_in_progress = True
        # 最小化前のジオメトリ・透明度を保存(復元時に元に戻すため)
        self._pre_minimize_geometry = self.geometry()
        self._pre_minimize_opacity = self.windowOpacity()

        # ── 縮小先ジオメトリの計算 ───────────────────────────────────
        cur = self.geometry()
        # 縮小率 75%
        target_w = int(cur.width() * 0.75)
        target_h = int(cur.height() * 0.75)
        cx = cur.x() + (cur.width() - target_w) // 2
        cy = cur.y() + (cur.height() - target_h) // 2
        # 「下に沈む」演出: 常に下方向へ +300px オフセット
        offset = 300  # px
        cy += offset
        target_geom = QRect(cx, cy, target_w, target_h)

        # ── 不透明オーバーレイを作成(最大化アニメと同じ作り) ──────────
        # アニメ中ずっと完全不透明で表示し、ウィンドウ中身を見えなくする。
        # ウィンドウと同じ角丸 R_MD(12px) を指定して、ウィンドウの角と
        # オーバーレイの角がぴったり一致するようにする。
        from PyQt6.QtWidgets import QWidget
        cw = self.centralWidget()
        if cw is not None:
            overlay = QWidget(cw)
            overlay.setObjectName("min_anim_overlay")
            theme_bg = T().BG
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            overlay.setStyleSheet(
                f"#min_anim_overlay {{"
                f"  background-color: rgb({theme_bg.red()},{theme_bg.green()},{theme_bg.blue()});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )
            overlay.setGeometry(0, 0, cw.width(), cw.height())
            overlay.raise_()
            overlay.show()
            self._min_overlay = overlay
        else:
            self._min_overlay = None

        # ── アニメ中、_root_widget の再描画を停止 ──────────────────────
        # 最大化アニメと同じ。ジオメトリ変化に伴う子ウィジェットの
        # resizeEvent / paintEvent を抑制し、ガタつきの原因になる
        # CPU/GPU 負荷とフレームスキップを回避する。
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(False)

        # ── アニメーション(200ms / InCubic) ───────────────────────────
        DURATION = 200

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(DURATION)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)

        shrink = QPropertyAnimation(self, b"geometry")
        shrink.setDuration(DURATION)
        shrink.setStartValue(cur)
        shrink.setEndValue(target_geom)
        shrink.setEasingCurve(QEasingCurve.Type.InCubic)
        # フレームごとにオーバーレイをウィンドウサイズへ追従させる
        shrink.valueChanged.connect(self._update_min_overlay_geometry)

        group = QParallelAnimationGroup()
        group.addAnimation(fade)
        group.addAnimation(shrink)
        group.finished.connect(self._on_minimize_anim_done)
        self._min_anim = group  # GC対策で保持
        group.start()

    def _update_min_overlay_geometry(self: "MainWindowProto", _v=None):
        """最小化アニメ中のオーバーレイをウィンドウサイズに追従させる。"""
        ov = getattr(self, "_min_overlay", None)
        if ov is not None:
            cw = self.centralWidget()
            if cw is not None:
                ov.setGeometry(0, 0, cw.width(), cw.height())
                ov.raise_()

    def _on_minimize_anim_done(self: "MainWindowProto"):
        """最小化アニメ完了時:
        1. showMinimized() で OS の最小化状態へ
        2. ジオメトリ・透明度を元に戻す(復元時にこの状態が見える)
        3. オーバーレイ破棄、再描画再開、フラグ解除
        """
        # OS の最小化を呼ぶ
        self.showMinimized()
        # 元のジオメトリ・透明度に戻す(タスクバーから復元した時にこの状態で出る)
        if hasattr(self, "_pre_minimize_geometry"):
            self.setGeometry(self._pre_minimize_geometry)
        if hasattr(self, "_pre_minimize_opacity"):
            self.setWindowOpacity(self._pre_minimize_opacity)
        else:
            self.setWindowOpacity(1.0)

        # _root_widget の再描画を再開
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(True)
            self._root_widget.update()

        # オーバーレイ破棄
        ov = getattr(self, "_min_overlay", None)
        if ov is not None:
            try:
                ov.hide()
                ov.setParent(None)
                ov.deleteLater()
            except Exception:
                pass
            self._min_overlay = None

        self._anim_in_progress = False

    def _animated_close(self: "MainWindowProto"):
        """閉じるボタン用: Windows標準と同様、その場でわずかに縮みながら
        フェードアウトしてから close() を呼ぶ。

        重要: dirty 確認 (未保存コメントの警告ダイアログ) は、フェードアウト
        アニメ「前」に行う必要がある。アニメ後の closeEvent 内で確認すると、
        ウィンドウは opacity=0 + 縮小で実質「消えた」状態になった後に
        ダイアログだけがデスクトップに浮いて見えるため。
        ここで確認 OK だった場合は _close_confirmed フラグを立て、
        後続の closeEvent では二重ダイアログを避けるためスキップする。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect
        if getattr(self, "_close_anim_started", False):
            return  # 二重起動防止

        # 未保存のコメントを先にノードへ反映してから dirty 確認
        if self._game_state is not None:
            try:
                self._save_comment_if_editing(self._game_state.current_node)
            except Exception:
                pass
        # 確認ダイアログ: キャンセルが選ばれたらアニメも何もしない
        if not self._confirm_discard_or_save():
            return
        # 続行確定: closeEvent 側での重複確認を防ぐフラグを立てる
        self._close_confirmed = True

        self._close_anim_started = True

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(300)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)

        # 中央に向けてわずかに縮小(98% → 軽い "縮む" 感)
        cur = self.geometry()
        target_w = int(cur.width() * 0.98)
        target_h = int(cur.height() * 0.98)
        target_geom = QRect(
            cur.x() + (cur.width() - target_w) // 2,
            cur.y() + (cur.height() - target_h) // 2,
            target_w, target_h,
        )
        shrink = QPropertyAnimation(self, b"geometry")
        shrink.setDuration(300)
        shrink.setStartValue(cur)
        shrink.setEndValue(target_geom)
        shrink.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QParallelAnimationGroup()
        group.addAnimation(fade)
        group.addAnimation(shrink)
        # アニメ完了後にウィンドウを閉じる
        # アニメ完了後の close は QMainWindow.close を直接呼んで closeEvent
        # ループを避ける (元コードは super(MainWindow, self).close() — Mixin
        # 移動で MainWindow がスコープ外になったため明示形に変更)
        group.finished.connect(lambda: QMainWindow.close(self))
        self._close_anim = group  # GC対策
        group.start()

    def _animated_show_on_startup(self: "MainWindowProto"):
        """初回表示時のアニメ。
        windowOpacity のフェードインに加えて、ウィンドウ自体を 85%→100% に
        スケールイン(中心固定)することで「ふわっと拡大して現れる」演出を
        スプラッシュ画面と同じトーンで実現する。
        __init__ で setWindowOpacity(0.0) 済み。

        スケールについての注意:
        ・geometry を毎フレーム変更するため、初回起動の重い処理が
          終わった後にこの関数を呼ぶ前提(showEvent から QTimer.singleShot
          で 80ms 遅延済み)。
        ・最小サイズ(_MIN_WIN_OPEN_W/H 等)に近いウィンドウサイズだと、
          85% に縮めても minimum で押し戻されてアニメが効かない。
          その場合はスケールアニメを省略して opacity のみで表現する。

        中身フェードイン(QGraphicsOpacityEffect)は描画パイプライン常時負荷で
        カクつきの原因となるため使用しない。windowOpacity だけで全体フェードする。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect
        # アニメ中フラグ(_place_panels スキップ用)
        self._startup_anim_running = True

        # アニメ時間(スプラッシュ画面のフェードインと揃えて 400ms)
        ANIM_MS = 400

        # ウィンドウ全体のフェードイン(opacity)
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(ANIM_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(self._on_startup_anim_done)
        self._startup_anim = fade  # GC対策
        fade.start()

        # ── スケールイン(geometry を中心固定で 85%→100%) ────────
        # 現在のジオメトリを正規(終了)状態として保存し、開始時は
        # 中心位置を保ったまま 85% サイズに縮めてからアニメする。
        target_geom = self.geometry()
        scale_from = 0.85
        # 96% にしたサイズが minimum を下回るかチェック
        # (下回る場合は minimum で抑えられてアニメが効かないので、
        #  スケールアニメ自体を省略する)
        min_w = self.minimumWidth()
        min_h = self.minimumHeight()
        start_w = max(int(round(target_geom.width()  * scale_from)), min_w)
        start_h = max(int(round(target_geom.height() * scale_from)), min_h)
        # 実際の縮小率(minimum で押し戻された場合は target に近づく)
        actual_scale_w = start_w / target_geom.width()  if target_geom.width()  > 0 else 1.0
        actual_scale_h = start_h / target_geom.height() if target_geom.height() > 0 else 1.0
        # 縮小率が両方向で十分小さい(=スケールが視認できる)場合のみ実行
        if min(actual_scale_w, actual_scale_h) < 0.99:
            # 中心固定で配置
            cx_t = target_geom.center().x()
            cy_t = target_geom.center().y()
            start_geom = QRect(
                cx_t - start_w // 2,
                cy_t - start_h // 2,
                start_w, start_h,
            )
            # アニメ前に開始ジオメトリへ瞬時移動(描画ちらつき防止のため
            # opacity=0 のうちに行う)
            self.setGeometry(start_geom)
            scale_anim = QPropertyAnimation(self, b"geometry")
            scale_anim.setDuration(ANIM_MS)
            scale_anim.setStartValue(start_geom)
            scale_anim.setEndValue(target_geom)
            scale_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._startup_scale_anim = scale_anim  # GC対策
            scale_anim.start()

    def _on_startup_anim_done(self: "MainWindowProto"):
        """起動アニメ完了時の後処理: フラグ解除 + 必要なら _place_panels 1回。"""
        self._startup_anim_running = False
        # 念のため最終形状で 1 回再配置
        self._place_panels()

    def _cleanup_root_opacity_effect(self: "MainWindowProto"):
        """起動時アニメ完了後、_root_widget の QGraphicsOpacityEffect を解除する。
        QGraphicsEffect は設定したままだと描画パイプラインに常時オーバーヘッドが
        生じる(子ウィジェットの再描画ごとに合成処理が走る)ため、不要になった
        時点で setGraphicsEffect(None) で外す。
        """
        try:
            if hasattr(self, "_root_widget"):
                self._root_widget.setGraphicsEffect(None)
            self._root_opacity_effect = None
        except Exception:
            pass

    def _is_pseudo_maximized(self: "MainWindowProto") -> bool:
        """自前最大化中かどうか。QPropertyAnimation 方式では _pseudo_max_active
        フラグで管理する(showMaximized は使わないので isMaximized() は常にFalse)。
        """
        return getattr(self, "_pseudo_max_active", False)

    def _set_win11_corner_round(self: "MainWindowProto", rounded: bool):
        """Windows 11 のネイティブ角丸を切り替える。
        rounded=False(最大化中) → 矩形、rounded=True → 通常の角丸。
        Win10 や非Windowsでは何もしない(API呼び出しが無視されるだけ)。
        """
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            DWMWCP_DONOTROUND = 1
            hwnd = int(self.winId())
            value = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    def changeEvent(self: "MainWindowProto", ev):
        """OSからウィンドウ状態が変わった場合の処理。
        ・自前最大化フラグに合わせてタイトルバーアイコン更新
        ・最小化状態から通常状態へ戻った瞬間に復元アニメを実行
        """
        from PyQt6.QtCore import QEvent
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, "_titlebar"):
                # 自前最大化フラグ優先で判定
                is_max = self._is_pseudo_maximized()
                self._titlebar.update_max_restore_icon(is_max)
            # 最小化からの復元を検知:
            # ev.oldState() に Minimized が含まれていて、現在は含まれていない
            try:
                old_minimized = bool(ev.oldState() & Qt.WindowState.WindowMinimized)
                cur_minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
                if old_minimized and not cur_minimized:
                    self._animated_show_from_minimized()
            except Exception:
                pass

    def _animated_show_from_minimized(self: "MainWindowProto"):
        """最小化からの復元時のカスタムアニメ。
        最大化アニメ・最小化アニメと同じ「不透明オーバーレイ + 内部再描画停止 +
        ウィンドウのジオメトリ/透明度をアニメ」方式。最小化アニメと完全に対称
        (逆再生)で、最小化で沈んだ位置から元位置へ上昇しながら現れる演出。

        フロー:
          1. ウィンドウを縮小+透明状態にいったんセット(元位置より下にオフセット)
          2. 不透明オーバーレイを最前面に被せ、ウィンドウ中身を隠蔽
          3. _root_widget.setUpdatesEnabled(False) で内部の再描画を停止
          4. windowOpacity 0.0→1.0 と geometry を「下から元位置へ上昇+拡大」で
             同時アニメ(200ms / OutCubic)
          5. 完了したらオーバーレイ破棄、内部再描画を再開
        """
        from PyQt6.QtCore import (
            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QRect,
        )
        # アニメ中の二重起動を防止
        if getattr(self, "_anim_in_progress", False):
            return

        # ── 開始ジオメトリ・最終ジオメトリの計算 ──────────────────────
        # 最終位置(復元後の本来あるべき場所): 現在のジオメトリ
        final_geom = self.geometry()
        # 開始サイズ 75% スケール
        start_w = int(final_geom.width() * 0.75)
        start_h = int(final_geom.height() * 0.75)
        cx = final_geom.x() + (final_geom.width() - start_w) // 2
        cy = final_geom.y() + (final_geom.height() - start_h) // 2
        # 「下から上昇する」演出: 最小化で沈んだ位置(元位置より下 +300px)から
        # 元位置へ上昇しながら拡大する。最小化アニメと完全に対称(逆再生)。
        offset = 300  # px
        cy += offset
        start_geom = QRect(cx, cy, start_w, start_h)

        self._anim_in_progress = True

        # ── 不透明オーバーレイを作成(最大化・最小化アニメと同じ作り) ───
        from PyQt6.QtWidgets import QWidget
        cw = self.centralWidget()
        if cw is not None:
            overlay = QWidget(cw)
            overlay.setObjectName("min_anim_overlay")
            theme_bg = T().BG
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            overlay.setStyleSheet(
                f"#min_anim_overlay {{"
                f"  background-color: rgb({theme_bg.red()},{theme_bg.green()},{theme_bg.blue()});"
                f"  border-radius: {R_MD}px;"
                f"}}"
            )
            overlay.setGeometry(0, 0, cw.width(), cw.height())
            overlay.raise_()
            overlay.show()
            self._min_overlay = overlay
        else:
            self._min_overlay = None

        # ── 内部再描画を停止(最大化・最小化アニメと同じ) ──────────────
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(False)

        # ── ウィンドウを開始状態にセット ────────────────────────────────
        # 透明度 0、サイズ 75%、タスクバー方向にオフセット位置
        self.setWindowOpacity(0.0)
        self.setGeometry(start_geom)

        # ── アニメーション(200ms / OutCubic、最小化と対称) ────────────
        DURATION = 200

        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(DURATION)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        grow = QPropertyAnimation(self, b"geometry")
        grow.setDuration(DURATION)
        grow.setStartValue(start_geom)
        grow.setEndValue(final_geom)
        grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        # フレームごとにオーバーレイをウィンドウサイズへ追従させる
        grow.valueChanged.connect(self._update_min_overlay_geometry)

        group = QParallelAnimationGroup()
        group.addAnimation(fade)
        group.addAnimation(grow)
        group.finished.connect(self._on_restore_anim_done)
        self._restore_anim = group  # GC対策で保持
        group.start()

    def _on_restore_anim_done(self: "MainWindowProto"):
        """最小化からの復元アニメ完了時:
        1. オーバーレイ破棄
        2. 内部再描画を再開
        3. アニメ中フラグ解除
        """
        # _root_widget の再描画を再開
        if hasattr(self, "_root_widget") and self._root_widget is not None:
            self._root_widget.setUpdatesEnabled(True)
            self._root_widget.update()

        # オーバーレイ破棄
        ov = getattr(self, "_min_overlay", None)
        if ov is not None:
            try:
                ov.hide()
                ov.setParent(None)
                ov.deleteLater()
            except Exception:
                pass
            self._min_overlay = None

        self._anim_in_progress = False

    def _edge_at(self: "MainWindowProto", pos: QPoint) -> Qt.Edge | None:
        """マウス位置がウィンドウ端のリサイズホットエリアにあれば、
        対応する Qt.Edge(複数辺なら OR 結合)を返す。なければ None。
        最大化中はリサイズ無効。
        """
        if self.isMaximized() or self._is_pseudo_maximized():
            return None
        b = self._RESIZE_BORDER
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        edge = Qt.Edge(0)
        if x <= b:
            edge |= Qt.Edge.LeftEdge
        elif x >= w - b:
            edge |= Qt.Edge.RightEdge
        if y <= b:
            edge |= Qt.Edge.TopEdge
        elif y >= h - b:
            edge |= Qt.Edge.BottomEdge
        # PyQt6 では Qt.Edge を int() に渡せないので .value で判定
        return edge if edge.value else None

    def resizeEvent(self: "MainWindowProto", ev):
        super().resizeEvent(ev)
        if getattr(self, "_startup_anim_running", False):
            return
        self._place_panels()

    def _animate_unified(self: "MainWindowProto", scroll_area, target_x: int, target_y: int,
                          tree_widget, end_marker_xy):
        """スクロール位置と分岐ツリーのリング絶対座標を **1つの QVariantAnimation で**
        同時に駆動する。
        各フレームの valueChanged で:
          - hbar.setValue(...), vbar.setValue(...) でスクロール更新
          - tree_widget._cur_marker_xy = (...) でリング絶対座標更新
          - tree_widget.update() で再描画
        を順に行う。同フレーム内で両方が更新されるためタイミングずれは発生しない。
        進行中のアニメがあれば停止して、現在の値を起点に新しい目標値へ再開する
        (連続操作時にリングがズレない)。

        - target_x, target_y: スクロール目標値(クランプ前の値、内部でクランプする)
        - end_marker_xy: アニメ終了時のリング絶対座標(=新ノード絶対座標)
        """
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if scroll_area is None or tree_widget is None:
            return
        hbar = scroll_area.horizontalScrollBar()
        vbar = scroll_area.verticalScrollBar()
        # 目標値はクランプ
        tx = max(hbar.minimum(), min(int(target_x), hbar.maximum()))
        ty = max(vbar.minimum(), min(int(target_y), vbar.maximum()))

        # 既存のスクロールアニメ(同じ scroll_area)を停止 → 破棄
        if not hasattr(self, "_scroll_anims"):
            self._scroll_anims = {}
        key = id(scroll_area)
        prev = self._scroll_anims.get(key)
        if prev is not None:
            try:
                prev.valueChanged.disconnect()
            except Exception:
                pass
            try:
                prev.finished.disconnect()
            except Exception:
                pass
            try:
                prev.stop()
            except Exception:
                pass
            try:
                prev.deleteLater()
            except Exception:
                pass

        # 旧アニメの停止後に **現在の値** を起点にする(連続操作で
        # 旧アニメが進行中だった場合、 hbar.value() と _cur_marker_xy は
        # その途中値になっているので、 それを新アニメの起点とする)
        sx = hbar.value()
        sy = vbar.value()
        # リング絶対座標の起点は _cur_marker_xy
        cur_marker = tree_widget._cur_marker_xy
        if cur_marker is None:
            # リング非表示状態(ルート等): リング側はアニメせず終点を確定
            cur_marker = end_marker_xy
        sax, say = float(cur_marker[0]), float(cur_marker[1])
        eax, eay = float(end_marker_xy[0]), float(end_marker_xy[1])

        # スクロールが動かず、 リングも動かないなら何もしない
        if sx == tx and sy == ty and sax == eax and say == eay:
            return

        anim = QVariantAnimation(self)
        anim.setDuration(self._SCROLL_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        # クロージャでキャプチャ
        def _on_changed(t, _hbar=hbar, _vbar=vbar,
                        _sx=sx, _sy=sy, _tx=tx, _ty=ty,
                        _sax=sax, _say=say, _eax=eax, _eay=eay,
                        _tree=tree_widget):
            try:
                t = float(t)
            except (TypeError, ValueError):
                return
            try:
                # ここで両方を **同じ valueChanged の中** で setValue する
                # ことで、スクロール位置とリング絶対座標は完全同フレーム同期。
                # 画面上のリング位置 = リング絶対 - スクロール = (sax + (eax-sax)*t)
                # - (sx + (tx-sx)*t)。 sax-sx と eax-tx が同じなら画面位置は不動。
                # 中央付近なら sax-sx == eax-tx == 200(中央)で常に固定。
                # 端付近なら sax-sx と eax-tx が異なるので画面位置が滑らかに変わる。
                _hbar.setValue(int(round(_sx + (_tx - _sx) * t)))
                _vbar.setValue(int(round(_sy + (_ty - _sy) * t)))
                _tree._cur_marker_xy = (
                    _sax + (_eax - _sax) * t,
                    _say + (_eay - _say) * t,
                )
                _tree.update()
            except RuntimeError:
                # スクロールエリアが破棄された場合は無視
                pass

        # 終了時にリング絶対座標を確定値で固定(浮動小数誤差を吸収)
        def _on_finished(_tree=tree_widget, _eax=eax, _eay=eay):
            try:
                _tree._cur_marker_xy = (_eax, _eay)
                _tree.update()
            except RuntimeError:
                pass

        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._scroll_anims[key] = anim
        anim.start()

    def _smooth_scroll_to(self: "MainWindowProto", scroll_area, target_x: int, target_y: int):
        """QScrollArea の水平/垂直スクロールバーを (target_x, target_y) へ
        QVariantAnimation で滑らかに移動させる。アニメ時間 280ms / OutCubic。

        既存の進行中アニメがあれば停止して、現在のスクロール値を起点に
        新しい目標値へ再開する(=ユーザー操作と競合してもアニメ側は破綻しない)。
        ユーザー側のホイール/ドラッグ中はアニメと値が衝突する可能性があるが、
        着手時に呼ばれる用途では稀なケースとして許容する。
        """
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        if scroll_area is None:
            return
        hbar = scroll_area.horizontalScrollBar()
        vbar = scroll_area.verticalScrollBar()
        # 目標値はスクロール範囲にクランプ
        tx = max(hbar.minimum(), min(int(target_x), hbar.maximum()))
        ty = max(vbar.minimum(), min(int(target_y), vbar.maximum()))

        # アニメ保持用辞書
        if not hasattr(self, "_scroll_anims"):
            self._scroll_anims = {}
        key = id(scroll_area)
        prev = self._scroll_anims.get(key)
        if prev is not None:
            try:
                prev.stop()
            except RuntimeError:
                pass

        sx = hbar.value()
        sy = vbar.value()
        if sx == tx and sy == ty:
            # 移動不要
            return

        anim = QVariantAnimation(self)
        anim.setDuration(self._SCROLL_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_changed(t, _hbar=hbar, _vbar=vbar,
                        _sx=sx, _sy=sy, _tx=tx, _ty=ty):
            try:
                t = float(t)
            except (TypeError, ValueError):
                return
            try:
                _hbar.setValue(int(round(_sx + (_tx - _sx) * t)))
                _vbar.setValue(int(round(_sy + (_ty - _sy) * t)))
            except RuntimeError:
                # スクロールエリアが破棄された場合は無視
                pass

        anim.valueChanged.connect(_on_changed)
        self._scroll_anims[key] = anim
        anim.start()
