"""
gui/_mixins/theme_ctrl.py — テーマ切替・ウェルカム遷移を MainWindow に提供する Mixin。

依存: gui.theme, gui.icons, gui.widgets.*, gui.dialogs, PyQt6.
実行時には pure Python class として動作。MainWindowProto は TYPE_CHECKING
専用で型補完目的のみ。

提供メソッド:
- apply_theme: テーマ切替のフェードトリガ
- _apply_theme_immediate: 全コンポーネントへの配色再適用 (アニメ無し本体)
- _on_theme_fade_done / _cancel_theme_fade: フェードアニメコールバック
- _apply_comment_close_btn_qss: コメント × ボタンの QSS 適用
- _set_welcome_mode: ウェルカム⇔盤面モード切替
- _prepare_welcome_to_board_fade / _animate_welcome_to_board: 遷移アニメ
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtWidgets import (
    QWidget,
)

from gui.theme import T, _theme
from gui.icons import menu_qss, rank_list_qss
from gui.infra import _profile_method


class ThemeCtrlMixin:
    """テーマ切替・ウェルカム遷移を MainWindow に提供する Mixin。"""

    def apply_theme(self: "MainWindowProto", mode: str):
        """テーマを即時切り替えする。mode = 'light' or 'dark'。

        切替演出: 旧テーマ画面を QPixmap でキャプチャしてオーバーレイし、
        裏で新テーマを適用、オーバーレイを 250ms かけてフェードアウトする
        ことでクロスフェードを実現する。
        """
        # フェード中に重複呼び出しが来た場合は、進行中のフェードを破棄して
        # 新しい mode で再開する(連打しても破綻しないように)。
        if getattr(self, "_theme_fade_running", False):
            self._cancel_theme_fade()

        # ── 旧テーマ画面のキャプチャ ──
        # centralWidget をキャプチャしてオーバーレイ表示する。
        # キャプチャはタイトルバー含む centralWidget 全体。
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtCore import Qt as _Qt
        cw = self.centralWidget()
        overlay = None
        if cw is not None and cw.width() > 0 and cw.height() > 0:
            try:
                pix = cw.grab()  # 旧テーマでの現状描画をキャプチャ
                overlay = QLabel(cw)
                overlay.setObjectName("theme_fade_overlay")
                overlay.setPixmap(pix)
                overlay.setGeometry(0, 0, cw.width(), cw.height())
                overlay.setAttribute(
                    _Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                overlay.raise_()
                overlay.show()
                self._theme_fade_overlay = overlay
                # オーバーレイを実際の描画パイプラインに反映させる。
                # processEvents を呼ばないと、後続の _apply_theme_immediate
                # (大量のスタイルシート更新で時間がかかる) の処理中、
                # オーバーレイがまだ描画されていない状態で中身の色が変わる
                # → ちらつきになる可能性がある。
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
            except Exception:
                # grab 失敗時はフェードなしで即時切替にフォールバック
                overlay = None
                self._theme_fade_overlay = None
        else:
            self._theme_fade_overlay = None

        # ── 新テーマを即時適用(オーバーレイで隠蔽されているため画面には出ない) ──
        self._apply_theme_immediate(mode)

        # オーバーレイがなければ通常の即時切替で終了
        if overlay is None:
            return

        # ── オーバーレイをフェードアウト ──
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        eff = QGraphicsOpacityEffect(overlay)
        eff.setOpacity(1.0)
        overlay.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity")
        anim.setDuration(250)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_theme_fade_done)
        self._theme_fade_anim = anim
        self._theme_fade_effect = eff
        self._theme_fade_running = True
        anim.start()

    def _on_theme_fade_done(self: "MainWindowProto"):
        """テーマフェード完了時の後処理: オーバーレイ破棄。"""
        ov = getattr(self, "_theme_fade_overlay", None)
        if ov is not None:
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
            ov.hide()
            ov.deleteLater()
        self._theme_fade_overlay = None
        self._theme_fade_anim = None
        self._theme_fade_effect = None
        self._theme_fade_running = False

    def _cancel_theme_fade(self: "MainWindowProto"):
        """進行中のテーマフェードを即座に終了する。"""
        anim = getattr(self, "_theme_fade_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
        self._on_theme_fade_done()

    def _apply_theme_immediate(self: "MainWindowProto", mode: str):
        """テーマを即時切り替えする(フェードなし)。
        apply_theme から呼ばれる本体ロジック。
        """
        from PyQt6.QtCore import QSettings
        _theme.set_mode(mode)
        QSettings("Kizuki", "Kizuki").setValue("theme", mode)

        # ── メニューのチェック状態を同期 ──
        if self._action_light is not None:
            self._action_light.setChecked(mode == "light")
            self._action_dark.setChecked(mode == "dark")

        # ── 1. MainWindow・共通スタイル ──────────────────────────────────
        self.setStyleSheet(
            f"QMainWindow{{background:{T().BG.name()};}}"
            + menu_qss()
        )

        # ── 2. centralWidget ──────────────────────────────────────────────
        cw = self.centralWidget()
        if cw:
            cw.setStyleSheet(f"background:{T().BG.name()};")
            from PyQt6.QtGui import QPalette as _QPalette
            _cw_pal = _QPalette()
            _cw_pal.setColor(_QPalette.ColorRole.Window, T().BG)
            _cw_pal.setColor(_QPalette.ColorRole.Base, T().BG)
            cw.setPalette(_cw_pal)
            cw.setAutoFillBackground(True)
        # _root_widget(outer 内のコンテンツ領域) も同様に。setStyleSheet だけ
        # では子ウィジェットを越えてリフレッシュされず、テーマ切替直後に右
        # カラムを開閉した時に旧テーマ色が一瞬見えることがある。QPalette +
        # autoFillBackground を併用して確実に背景を更新する。
        rw = getattr(self, "_root_widget", None)
        if rw:
            rw.setStyleSheet(f"background:{T().BG.name()};")
            from PyQt6.QtGui import QPalette as _QPalette
            _rw_pal = _QPalette()
            _rw_pal.setColor(_QPalette.ColorRole.Window, T().BG)
            _rw_pal.setColor(_QPalette.ColorRole.Base, T().BG)
            rw.setPalette(_rw_pal)
            rw.setAutoFillBackground(True)
        # カスタムタイトルバーのテーマ追従
        if self._titlebar is not None:
            self._titlebar.apply_theme()
        # タイトルバーに紐付けた各 QMenu(ポップアップ)に menu_qss を再適用。
        # QMenu は top-level popup として表示されるため、MainWindow の
        # setStyleSheet で書いた menu_qss は継承されない。個別に設定が必要。
        for menu_attr in ("_komi_menu", "_rank_menu", "_volume_menu"):
            m = getattr(self, menu_attr, None)
            if m is not None:
                m.setStyleSheet(menu_qss())
        # コミメニューの「その他」インラインウィジェットも apply_theme
        if self._komi_custom_widget is not None:
            self._komi_custom_widget.apply_theme()
        # トースト通知のテキスト色もテーマに追従させる
        # (背景カードは paintEvent で毎回 T() を参照するため自動追従するが、
        #  QLabel の文字色は固定スタイルシートのため明示的な再設定が必要)。
        if self._toast is not None:
            self._toast.update_theme()
        # 棋力メニュー内の QListWidget にもテーマ追従の QSS を再適用
        # (menu_qss は QListWidget には効かないため別途必要)。
        # 描画は _RankItemDelegate が担当し、テーマ色は delegate 内部の
        # _cached_color と一致しなければ自動再生成されるため、ここでは
        # キャッシュ無効化と viewport 再描画のみ実行。
        rl = getattr(self, "_rank_list_widget", None)
        if rl is not None:
            rl.setStyleSheet(rank_list_qss())
            d = getattr(self, "_rank_item_delegate", None)
            if d is not None:
                d._cached_pix = None
                d._cached_color = ""
            rl.viewport().update()
        # その他のメニュー(ファイル/表示/設定とその配下のサブメニュー)は
        # findChildren(QMenu) で全部スキャンして適用する。
        from PyQt6.QtWidgets import QMenu
        for m in self.findChildren(QMenu):
            m.setStyleSheet(menu_qss())

        # ── 3. 左カラム・QStackedWidget ────────────────────────────────
        from PyQt6.QtGui import QPalette as _QPalette
        # palette を Window/Base 両方に設定 + setAutoFillBackground(True) を併用
        # することで、setStyleSheet だけでは取りこぼしがある領域も確実に塗られる。
        # 特に「ダーク → ライト切替直後に右カラム閉じる時」など、隠れていた
        # 親領域が突然見える瞬間に旧テーマ色 (黒) が露出する問題の対策。
        _bg_pal = _QPalette()
        _bg_pal.setColor(_QPalette.ColorRole.Window, T().BG)
        _bg_pal.setColor(_QPalette.ColorRole.Base, T().BG)
        for attr in ("_left_col", "_left_stack", "_board_container"):
            w = getattr(self, attr, None)
            if w is None:
                continue
            w.setStyleSheet(f"background:{T().BG.name()};")
            w.setPalette(_bg_pal)
            w.setAutoFillBackground(True)

        # ── 4. フローティングパネル背景 ──────────────────────────────────
        if self._right_col is not None:
            self._right_col.setStyleSheet(
                f"QWidget#floating_panel {{"
                f"  background:{T().PANEL.name()};"
                f"  border-radius:16px;"
                f"}}"
            )
            # _right_col は border-radius:16px の角丸ウィジェット。QSS の
            # background は角丸の内側しか塗らないため、リサイズ中など再描画
            # タイミングで角丸の外(角の三角形領域)に古いフレームの色が残る。
            # そこで palette を BG 色に揃えておく。
            # NOTE: setAutoFillBackground(True) はテーマ切替時には呼ばない。
            # 初期化時に True 設定済みで、その状態は維持される。テーマ切替後に
            # 再呼び出しすると、続けて発火する右パネル開閉アニメの
            # QGraphicsOpacityEffect と相互作用して、右カラム全体に黒い
            # オーバーレイが乗る現象が発生するため(原因はおそらく
            # autoFillBackground 再設定が effect の合成バッファに影響する)。
            self._right_col.setPalette(_bg_pal)

        # NavBar: スライダースタイル更新 + palette を BG に揃える
        # (palette がデフォルト黒のまま残ると、ライトモードでリサイズ時に
        #  一瞬黒い領域が見える原因となる)
        if self._navbar is not None:
            self._navbar._apply_slider_style()
            self._navbar.setPalette(_bg_pal)

        # 音量スライダー（メニュー内）
        if self._volume_slider is not None:
            self._volume_slider.update()  # FlatSlider は paintEvent で描画
        if self._volume_label is not None:
            self._volume_label.setStyleSheet(
                f"color:{T().TEXT.name()}; background:transparent; font-size:14px;"
            )

        # ── 6. スクロールエリア + カード背景 ──────────────────────────────
        if self._cards_scroll is not None:
            self._cards_scroll.setStyleSheet(
                f"QScrollArea {{ border:none; background:{T().BG.name()}; }}"
                f"QScrollArea > QWidget > QWidget {{ background:{T().BG.name()}; }}"
            )
            # NOTE: setAutoFillBackground(True) はテーマ切替時には呼ばない。
            # 初期化時に True 設定済みで、その状態は維持される。
            # 詳細は _right_col の同様コメント参照(右パネル開閉アニメの
            # QGraphicsOpacityEffect と相互作用して黒オーバーレイが出る)。
            from PyQt6.QtGui import QPalette as _QPalette
            # viewport
            _vp = self._cards_scroll.viewport()
            _pal = _QPalette()
            _pal.setColor(_QPalette.ColorRole.Window, T().BG)
            _pal.setColor(_QPalette.ColorRole.Base, T().BG)
            _vp.setPalette(_pal)
            # cards_widget
            w = self._cards_scroll.widget()
            if w:
                w.setStyleSheet(f"background:{T().BG.name()};")
                _wpal = _QPalette()
                _wpal.setColor(_QPalette.ColorRole.Window, T().BG)
                _wpal.setColor(_QPalette.ColorRole.Base, T().BG)
                w.setPalette(_wpal)

        # ── 7. コメントカード ────────────────────────────────────────────
        if self._comment is not None:
            # ObjectName ベースで親カードを探す
            card = self._comment.parent()
            if card:
                card.setStyleSheet(
                    f"QWidget#comment_card {{"
                    f"  background:{T().PANEL.name()};"
                    f"  border:1px solid {T().BORDER2.name()};"
                    f"  border-radius:12px;"
                    f"}}"
                )
            self._comment.setStyleSheet(
                f"QTextEdit {{ background:transparent; color:{T().TEXT.name()}; "
                f"border:none; font-size:16px;"
                f"font-family:'Yu Gothic UI','BIZ UDGothic'; }}"
            )

        # ── 8. セパレータ ─────────────────────────────────────────────────
        from PyQt6.QtWidgets import QFrame
        for frame in self.findChildren(QFrame):
            frame.styleSheet()
            # BORDER2 色のセパレータを更新（高さ1pxの区切り線）
            if frame.maximumHeight() == 1 and frame.minimumHeight() == 1:
                frame.setStyleSheet(f"background:{T().BORDER2.name()};")

        # ── 9. paintEvent 系ウィジェット（update() で自動再描画） ─────────
        if self._board is not None:
            self._board.update()
        if self._branch_tree is not None:
            self._branch_tree.update()
        if self._info is not None:
            self._info.apply_theme()

        # ── 10. pyqtgraph グラフ（InfoPanel.apply_theme 内で処理済み） ────

        # ── 11. ウェルカム画面 ───────────────────────────────────────────
        if self._welcome_pane is not None:
            self._welcome_pane.apply_theme()

        # ── 12. D&D オーバーレイ内のカード ───────────────────────────────
        # 半透明背景はテーマに依らず固定だが、中央のカード(_WelcomeCard)は
        # T().TEXT/BORDER 等を paintEvent で参照するためテーマ追従が必要
        if self._drop_card is not None:
            self._drop_card.update()

        # ── 13. 手の情報カード・トグルバー ───────────────────────────────
        if self._move_card is not None:
            self._move_card.apply_theme()
        if self._toggle_bar is not None:
            self._toggle_bar.apply_theme()

        # ── 14. スライダーマーカーオーバーレイ ───────────────────────────
        if self._navbar is not None and hasattr(self._navbar, "_marker_overlay"):
            self._navbar._marker_overlay.update()

        # ── 15. コメントオーバーレイ ─────────────────────────────────────
        if self._comment_textedit is not None:
            self._comment_textedit.setStyleSheet(
                f"QTextEdit {{ background:transparent; color:{T().TEXT.name()};"
                f" border:none; font-size:14px;"
                f" font-family:'Yu Gothic UI','BIZ UDGothic'; }}"
            )
            # フェードオーバーレイの色も追従させる(再描画で次の paintEvent に反映)
            if hasattr(self._comment_textedit, "_fade_overlay"):
                self._comment_textedit._fade_overlay.update()
        if self._comment_overlay is not None:
            self._comment_overlay.update()
        # ✕ボタンの色(T().TEXT)もテーマ追従させる
        self._apply_comment_close_btn_qss()

        # 全体に強制 update を発行。setStyleSheet/setPalette だけでは
        # 子ウィジェットの再描画が起きない領域があり、テーマ切替直後に
        # アニメで「隠れていた領域が見える」場面で旧テーマ色が露出する
        # ことがあるため、_root_widget 配下を含めて明示的に update する。
        self.update()
        if self._root_widget is not None:
            self._root_widget.update()
            for child in self._root_widget.findChildren(QWidget):
                child.update()

    def _apply_comment_close_btn_qss(self: "MainWindowProto"):
        """コメントオーバーレイの ✕ ボタンに専用スタイルを適用する。
        色は T().TEXT(ダーク=#fff、ライト=#333)。ホバー時も背景は変えない。
        テーマ切替時にも再適用するため _apply_theme_immediate から呼ばれる。
        """
        if self._comment_close_btn is None:
            return
        color = T().TEXT.name()
        self._comment_close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:4px;"
            f"color:{color};font-size:12px;padding:0px 0px 2px 0px;}}"
            f"QPushButton:hover{{background:transparent;color:{color};}}"
        )

    @_profile_method("_set_welcome_mode")
    def _set_welcome_mode(self: "MainWindowProto", welcome: bool):
        """ウェルカム画面 ↔ 碁盤画面の表示モードを切り替える。
        ウェルカム時は右カラム（情報パネル）も非表示にして、最初の選択
        （SGFを開く / 新規作成）に集中できるようにする。
        ウェルカム → 碁盤への遷移時はフェードインアニメで自然に切り替える。

        右パネルトグルボタン:
          ・ウェルカム時 → 非表示(押せない)
          ・碁盤時      → 表示。表示状態は _right_panel_collapsed に従う
            (前回の開閉状態を保持したまま遷移する)
        """
        from PyQt6.QtCore import QTimer
        # 切り替え方向を判定(現在ウェルカム表示中か?)
        was_welcome = (self._left_stack.currentIndex() == 1)
        is_transition_to_board = was_welcome and not welcome
        # ウェルカム → 碁盤遷移時は、setCurrentIndex で碁盤画面に切り替わる
        # 「前」に対象ウィジェットを opacity=0 にしておく。これにより、
        # 切替直後の 1 フレームで碁盤画面が完全表示されてからフェードイン
        # アニメで再び見えてくる、という「明滅」を防ぐ。
        if is_transition_to_board:
            self._prepare_welcome_to_board_fade()
        # 左カラム: ウェルカム=index 1、碁盤=index 0
        self._left_stack.setCurrentIndex(1 if welcome else 0)
        # ナビバー（碁盤の下のスライダー＋進む/戻る）
        self._navbar.setVisible(not welcome)
        # 右カラム（情報パネル）の表示制御
        # ウェルカム時: 強制非表示
        # 碁盤時:       _right_panel_collapsed に従う(前回状態を尊重)
        if self._right_col is not None:
            if welcome:
                self._right_col.setVisible(False)
            else:
                collapsed = getattr(self, "_right_panel_collapsed", False)
                self._right_col.setVisible(not collapsed)
        # 右パネルトグルボタン: ウェルカム時は非アクティブ(レイアウト幅は維持)
        # setVisible(False) を使うとレイアウトが詰まって解析画面遷移時にボタン群が
        # 左へ流れる動きが目立つので、アイコン透明化 + クリック無効化で代替する。
        if self._titlebar is not None and hasattr(self._titlebar, "set_panel_toggle_active"):
            self._titlebar.set_panel_toggle_active(not welcome)
        # スクリーンショット・保存・コピー: ウェルカム時は無効化
        if self._ss_act is not None:
            self._ss_act.setEnabled(not welcome)
        if self._ss_win_act is not None:
            self._ss_win_act.setEnabled(not welcome)
        if self._save_act is not None:
            self._save_act.setEnabled(not welcome)
        if self._copy_act is not None:
            self._copy_act.setEnabled(not welcome)
        # 表示モード変更後にパネル配置を再計算
        # setStyleSheet 後に Qt がレイアウトを再計算するため 1 フレーム遅らせる
        QTimer.singleShot(0, self._place_panels)
        # ウェルカム → 碁盤遷移時はフェードインアニメを実行
        if is_transition_to_board:
            # _place_panels が完了してから(レイアウトが確定してから)アニメ開始
            QTimer.singleShot(10, self._animate_welcome_to_board)

    def _prepare_welcome_to_board_fade(self: "MainWindowProto"):
        """ウェルカム → 碁盤遷移時のフェードイン事前準備。
        対象ウィジェット (碁盤コンテナ / ナビバー / 右パネル) に
        QGraphicsOpacityEffect を opacity=0 で適用する。

        この関数は _set_welcome_mode 内で setCurrentIndex(0) を呼ぶ
        「前」に呼び出される。これにより、QStackedWidget の表示が
        碁盤画面に切り替わった瞬間、対象ウィジェットは既に opacity=0
        の透明状態になっており、「一瞬完全表示されてからフェードイン
        が始まる」というチラつきが防げる。

        実際のアニメーション開始は _animate_welcome_to_board が
        QTimer.singleShot(10, ...) 経由で後から行う。両方の関数が
        同じ effect インスタンスに対して動作するよう、self に
        _welcome_fade_effects として保持する。

        備考: スタイル制約 (QGraphicsOpacityEffect 禁止) は通常 UI 用で、
        ウェルカム → 碁盤の一回限りの遷移演出は既に存在する例外的な
        使用 (windowOpacity ではウィンドウ全体になってしまうため)。
        既存の _animate_welcome_to_board と同じ方針を踏襲している。
        """
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        targets = []
        if self._board_container is not None:
            targets.append(self._board_container)
        if self._navbar is not None:
            targets.append(self._navbar)
        if self._right_col is not None:
            targets.append(self._right_col)

        # 各ウィジェットに opacity=0 の effect を即時適用しておく。
        # アニメ開始時 (_animate_welcome_to_board) でこの effect を
        # そのまま使ってフェードイン実行する。
        effects = []
        for w in targets:
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
            effects.append((w, eff))
        # アニメ実行側に渡すための保持
        self._welcome_fade_effects = effects

    def _animate_welcome_to_board(self: "MainWindowProto"):
        """ウェルカム画面から碁盤画面への遷移アニメーション。
        碁盤(_board_container)、ナビバー、右パネルをまとめて opacity 0 → 1 で
        フェードインさせる。

        opacity effect の適用は事前に _prepare_welcome_to_board_fade で
        行われており、ここではアニメ起動と完了時クリーンアップだけを行う。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QParallelAnimationGroup

        # 事前準備で適用した effects を取得。なければ後方互換として
        # ここで作成する(直接呼ばれた場合の保険)。
        effects = getattr(self, "_welcome_fade_effects", None)
        if not effects:
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            targets = []
            if self._board_container is not None:
                targets.append(self._board_container)
            if self._navbar is not None:
                targets.append(self._navbar)
            if self._right_col is not None:
                targets.append(self._right_col)
            if not targets:
                return
            effects = []
            for w in targets:
                eff = QGraphicsOpacityEffect(w)
                eff.setOpacity(0.0)
                w.setGraphicsEffect(eff)
                effects.append((w, eff))

        group = QParallelAnimationGroup()
        for _w, eff in effects:
            anim = QPropertyAnimation(eff, b"opacity")
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(anim)

        def _cleanup():
            # アニメ完了後に opacity effect を外す(描画パイプラインの常時負荷回避)
            for w, _eff in effects:
                try:
                    w.setGraphicsEffect(None)
                except Exception:
                    pass
            self._welcome_fade_effects = None

        group.finished.connect(_cleanup)
        self._welcome_to_board_anim = group  # GC対策
        group.start()
