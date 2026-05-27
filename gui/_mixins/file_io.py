"""
gui/_mixins/file_io.py — SGF・ドラッグドロップ・コピペ・新規対局を MainWindow に提供する Mixin。

依存: core.sgf_parser, gui.theme, gui.widgets.board, gui.dialogs, PyQt6.

提供メソッド:
- _open_sgf_path / _open_sgf / _save_sgf: SGF 入出力
- _confirm_discard_or_save: 未保存変更確認ダイアログ
- _copy_sgf / _paste_sgf: クリップボード経由
- _save_board_screenshot: 盤面 PNG 保存
- _load_demo / _new_game: 起動時の初期局面
- _has_supported_kifu: D&D 対象判定
- _show_drop_overlay / _hide_drop_overlay: D&D オーバーレイ表示制御
- dragEnterEvent / dragMoveEvent / dragLeaveEvent / dropEvent: D&D イベント
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtCore import Qt, QRect, QRectF, QStandardPaths
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QFileDialog

from core.sgf_parser import load_sgf, save_sgf, parse_sgf

from gui.theme import R_MD
from gui.widgets.board import BoardWidget
from gui.dialogs import _UnsavedChangesDialog


class FileIOMixin:
    """SGF・ドラッグドロップ・コピペ・新規対局を MainWindow に提供する Mixin。"""

    def _open_sgf_path(self: "MainWindowProto", path: str):
        """指定パスの SGF 棋譜を開く。
        現在の棋譜に未保存の変更がある場合、ユーザーに確認ダイアログを出し、
        キャンセルが選ばれたら開く操作を中止する。
        """
        # 未保存の変更があれば確認(中止された場合は何もしない)
        if not self._confirm_discard_or_save():
            return
        # 確認通過後、編集中のコメントフラグをクリアする(_new_game と同様の理由)。
        self._comment.document().setModified(False)
        try:
            # 既に碁盤を表示中なら、データ差し替え前にクロスフェード起動
            was_welcome = (self._left_stack.currentIndex() == 1)
            if not was_welcome:
                self._board.start_content_change_anim()
            self._game = load_sgf(path)
            self._info.get_graph().clear_data()
            self._build_states()
            self._goto_first()
            self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            self._branch_tree.update_tree(self._game_state, self._node_analyses)
            # 碁盤へ切り替え
            self._set_welcome_mode(False)
            # 棋譜差し替え後の dirty / 保存先をリセット
            # (読込元のパスを保存先として記憶 → 次回保存はそのまま上書き)
            self._is_dirty = False
            self._current_sgf_path = path
            self._status_bar.showMessage(f"読み込み完了: {Path(path).name}")
        except Exception as e:
            self._status_bar.showMessage(f"読み込みエラー: {e}")

    def _open_sgf(self: "MainWindowProto"):
        # SGF ファイルのみに対応
        filters = "SGF Files (*.sgf);;All Files (*.*)"
        path,_=QFileDialog.getOpenFileName(self,"棋譜を開く","",filters)
        if not path: return
        self._open_sgf_path(path)

    def _save_sgf(self: "MainWindowProto") -> bool:
        """棋譜を SGF 形式で保存する。

        戻り値:
          True  = 保存成功(dirty フラグも False にリセット)
          False = キャンセル / 失敗(呼び出し側は「続行を中止」してよい)

        既存パスがある場合は上書き保存。無い場合 (新規作成・貼付の直後など) は
        QFileDialog で名前を尋ねる。
        """
        if not self._game:
            return False
        path = self._current_sgf_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "SGF を保存", "", "SGF Files (*.sgf)"
            )
            if not path:
                return False  # ユーザーがキャンセル
        try:
            save_sgf(self._game, path)
        except Exception as e:
            self._status_bar.showMessage(f"保存エラー: {e}")
            return False
        # 成功: パスを記憶し、dirty 解除
        self._current_sgf_path = path
        self._is_dirty = False
        self._status_bar.showMessage(f"保存: {Path(path).name}")
        return True

    def _confirm_discard_or_save(self: "MainWindowProto") -> bool:
        """棋譜に未保存の変更がある時、ユーザーに確認するヘルパー。

        戻り値:
          True  = 続行してよい (保存済み or 変更を破棄する選択)
          False = 続行を中止 (キャンセル or 保存失敗)

        呼び出し側 (closeEvent / 新規作成 / SGF を開く / 貼付) は、戻り値が
        False なら現在の操作を中止する。
        """
        # 棋譜が未読込、または dirty でない場合は確認不要
        if self._game is None:
            return True
        if not self._is_dirty:
            return True

        dlg = _UnsavedChangesDialog(self)
        dlg.exec()
        code = dlg.result_code()

        if code == _UnsavedChangesDialog.RESULT_SAVE:
            # 保存して続行: 保存に失敗 / キャンセルなら続行も中止
            return self._save_sgf()
        elif code == _UnsavedChangesDialog.RESULT_DISCARD:
            # 保存せず続行: 変更を破棄して続行
            return True
        else:
            # キャンセル: 操作を中止
            return False

    def _copy_sgf(self: "MainWindowProto"):
        """現在のゲーム内容を SGF 形式でクリップボードにコピーする。
        既存の save_sgf を一時ファイルに使い、その内容を読み込んでコピーする。
        """
        if not self._game:
            return
        tmp_path = None
        try:
            # delete=False で一時ファイル作成（Windowsの仕様で同時アクセス不可のため）
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".sgf", delete=False, encoding="utf-8"
            ) as tmp:
                tmp_path = Path(tmp.name)
            # 既存の save_sgf を流用
            save_sgf(self._game, str(tmp_path))
            # 内容を読み込み
            sgf_text = tmp_path.read_text(encoding="utf-8")
            # クリップボードへ
            QApplication.clipboard().setText(sgf_text)
            self._status_bar.showMessage("SGF をクリップボードにコピーしました")
        except Exception as e:
            self._status_bar.showMessage(f"コピーエラー: {e}")
        finally:
            # 一時ファイル削除
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _paste_sgf(self: "MainWindowProto"):
        """クリップボードのテキストを SGF 棋譜として読み込み、現在のゲームを置き換える。
        現在の棋譜に未保存の変更がある場合、ユーザーに確認ダイアログを出し、
        キャンセルが選ばれたら貼り付けを中止する。
        """
        text = QApplication.clipboard().text()
        if not text or not text.strip():
            self._status_bar.showMessage("クリップボードが空です")
            return
        # 未保存の変更があれば確認(中止された場合は何もしない)
        if not self._confirm_discard_or_save():
            return
        # 確認通過後、編集中のコメントフラグをクリアする(_new_game と同様の理由)。
        self._comment.document().setModified(False)
        try:
            # 既に碁盤を表示中なら、データ差し替え前にクロスフェード起動
            was_welcome = (self._left_stack.currentIndex() == 1)
            if not was_welcome:
                self._board.start_content_change_anim()
            self._game = parse_sgf(text)
            self._build_states()
            self._goto_first()
            self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            self._branch_tree.update_tree(self._game_state, self._node_analyses)
            # 碁盤へ切り替え
            self._set_welcome_mode(False)
            # 棋譜差し替え後の dirty / 保存先をリセット
            # (貼付は保存先未確定なので _current_sgf_path は None)
            self._is_dirty = False
            self._current_sgf_path = None
            self._status_bar.showMessage("クリップボードから棋譜を読み込みました")
        except Exception as e:
            self._status_bar.showMessage(f"貼り付けエラー: {e}")

    def _save_board_screenshot(self: "MainWindowProto"):
        """BoardWidget を PNG でファイル保存する。
        保存先のデフォルトはデスクトップ。
        """
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "盤面をスクリーンショットとして保存",
            desktop,
            "PNG画像 (*.png)",
        )
        if not path:
            return
        # ウェルカム画面では保存しない（index 1 = ウェルカム）
        if self._left_stack.currentIndex() == 1:
            self._status_bar.showMessage("棋譜を開いてからスクリーンショットを撮ってください")
            return
        # 拡張子が付いていない場合は補完
        if not path.lower().endswith(".png"):
            path += ".png"
        # BoardWidget をキャプチャし、碁盤（木材部分）のみ切り抜いて保存
        full = self._board.grab()
        # _bg() と同じ計算式で碁盤矩形を求める
        ox, oy = self._board._orig()
        c = self._board._cell()
        bw = c * (self._board.board_size - 1)
        m = c * 1.2 if self._board.show_coords else c * 0.6
        board_x = int(ox - m)
        board_y = int(oy - m)
        board_w = int(bw + 2 * m)
        board_rect = QRect(board_x, board_y, board_w, board_w)
        cropped = full.copy(board_rect)
        # 盤面が角丸描画になっているので、保存画像にも同じ角丸クリップを適用
        # （素直に矩形コピーすると四隅にウィンドウ背景が写り込んでしまう）
        pixmap = QPixmap(cropped.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        _p = QPainter(pixmap)
        _p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _clip = QPainterPath()
        _clip.addRoundedRect(QRectF(0, 0, cropped.width(), cropped.height()), R_MD, R_MD)
        _p.setClipPath(_clip)
        _p.drawPixmap(0, 0, cropped)
        _p.end()
        ok = pixmap.save(path, "PNG")
        if ok:
            self._status_bar.showMessage(f"スクリーンショットを保存しました: {path}")
        else:
            self._status_bar.showMessage("スクリーンショットの保存に失敗しました")

    def _load_demo(self: "MainWindowProto"):
        """起動時処理: ウェルカム画面を表示する（デモ棋譜は使用しない）。"""
        self._set_welcome_mode(True)
        self._status_bar.showMessage("SGF ファイルを開くか、新規作成してください")

    def _new_game(self: "MainWindowProto", size: int = 19):
        """新規作成: 空の盤面を開いて解析モードへ遷移する。
        size は盤面サイズ（9/13/19路を想定）。デフォルトは19路。

        現在の棋譜に未保存の変更がある場合、ユーザーに確認ダイアログを出し、
        キャンセルが選ばれたら新規作成を中止する。
        """
        # 未保存の変更があれば確認(中止された場合は何もしない)
        if not self._confirm_discard_or_save():
            return
        # 確認通過後、編集中のコメントフラグをクリアする。
        # これをしないと、後の _goto_first → _save_comment_if_editing で
        # コメント欄に残っていた古いテキストが「新棋譜のルートノード」へ
        # 書き込まれてしまい、新棋譜の0手目にコメントが残る不具合になる。
        self._comment.document().setModified(False)
        sz = int(size) if size in (9, 13, 19) else 19
        # 既に碁盤を表示中なら、データ差し替え前にクロスフェード起動
        # (ウェルカム → 碁盤の初回遷移は別途 _animate_welcome_to_board が担当)
        was_welcome = (self._left_stack.currentIndex() == 1)
        if not was_welcome:
            self._board.start_content_change_anim()
        sgf = f"(;FF[4]GM[1]SZ[{sz}]KM[6.5]PB[Black]PW[White])"
        self._game = parse_sgf(sgf)
        self._info.get_graph().clear_data()
        self._build_states()
        self._goto_first()
        self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
        self._branch_tree.update_tree(self._game_state, self._node_analyses)
        # 碁盤へ切り替え
        self._set_welcome_mode(False)
        # 棋譜差し替え後の dirty / 保存先をリセット
        # (新規作成は保存先未確定なので _current_sgf_path は None)
        self._is_dirty = False
        self._current_sgf_path = None
        self._status_bar.showMessage(f"新規棋譜({sz}路)を作成しました")

    def _has_supported_kifu(self: "MainWindowProto", ev) -> bool:
        if not ev.mimeData().hasUrls():
            return False
        return any(
            u.toLocalFile().lower().endswith(self._SUPPORTED_KIFU_EXTS)
            for u in ev.mimeData().urls()
        )

    def _show_drop_overlay(self: "MainWindowProto"):
        """D&D オーバーレイを root 全体に広げて表示する。
        _root_widget の子にしているため、タイトルバーは覆わない。
        """
        if not hasattr(self, "_drop_overlay"):
            return
        rw, rh = self._root_widget.width(), self._root_widget.height()
        self._drop_overlay.setGeometry(0, 0, rw, rh)
        cw = self._drop_card.width()
        ch = self._drop_card.height()
        self._drop_card.move((rw - cw) // 2, (rh - ch) // 2)
        self._drop_overlay.raise_()
        self._drop_overlay.show()

    def _hide_drop_overlay(self: "MainWindowProto"):
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.hide()

    def dragEnterEvent(self: "MainWindowProto", ev):
        if self._has_supported_kifu(ev):
            ev.acceptProposedAction()
            self._show_drop_overlay()
            return
        ev.ignore()

    def dragMoveEvent(self: "MainWindowProto", ev):
        # dragEnter で acceptProposedAction しても、Qt によっては
        # dragMove で再評価されるためここでも継続的に受け取る
        if self._has_supported_kifu(ev):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self: "MainWindowProto", ev):
        self._hide_drop_overlay()

    def dropEvent(self: "MainWindowProto", ev):
        self._hide_drop_overlay()
        paths = [u.toLocalFile() for u in ev.mimeData().urls()
                 if u.toLocalFile().lower().endswith(self._SUPPORTED_KIFU_EXTS)]
        if paths:
            ev.acceptProposedAction()
            # ウェルカム画面でも碁盤画面でも同じ _open_sgf_path に流す
            self._open_sgf_path(paths[0])
