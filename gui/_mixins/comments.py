"""
gui/_mixins/comments.py — コメントオーバーレイの開閉・編集・保存を MainWindow に提供する Mixin。

依存: PyQt6 のみ (MainWindowProto は TYPE_CHECKING 専用)。

提供メソッド:
- _write_comment_to_node / _save_comment_if_editing: SGFNode へのコメント保存
- _on_node_comment_requested: BranchTreeWidget からの編集要求ハンドラ
- _toggle_comment_overlay / _open_comment_overlay / _close_comment_overlay: 開閉
- _on_comment_overlay_open_done / _on_comment_overlay_close_done / _cancel_comment_overlay_anim: アニメ完了/中断
- _prewarm_comment_overlay: 起動時の予熱
- _load_comment_to_overlay: 現ノードのコメントを overlay に流し込む
- _on_comment_overlay_changed: テキスト編集時のハンドラ
- _is_inside_comment_overlay: ウィジェットが overlay 内かの判定
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtCore import (
    QObject, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QRect,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QTextEdit, QGraphicsOpacityEffect,
)


class CommentsMixin:
    """コメントオーバーレイの開閉・編集・保存を MainWindow に提供する Mixin。"""

    def _write_comment_to_node(self: "MainWindowProto", node, text: str):
        """指定ノードへコメントを書き込み、内容変化があれば dirty フラグを立てる。

        コメント編集の経路は複数あり (オーバーレイのテキスト変更即時保存、
        オーバーレイ閉じる時、コメント欄フォーカスアウト時、ノード移動前の
        保存等)、それぞれが個別に node.comment = ... を実行していた。これら
        を一元化し、内容変化があった場合だけ dirty 反映するようにする。

        Kizuki の dirty 判定は「コメント編集のみ」に絞ってある。盤面クリックや
        ノード削除は分岐探索の一部であり、ユーザーが意図して「保存したい変更」
        ではないため、dirty にしない。コメント編集は明確な意図を持って書く
        ものなので、これだけは保護する。
        """
        if node is None:
            return
        old_comment = getattr(node, "comment", "") or ""
        node.comment = text
        if text != old_comment:
            self._is_dirty = True

    def _save_comment_if_editing(self: "MainWindowProto", node=None):
        """コメント欄に未保存の入力があれば指定ノード（省略時は現在ノード）に保存する。"""
        if not self._game_state: return
        if not self._comment.document().isModified(): return
        target = node if node is not None else self._game_state.current_node
        new_comment = self._comment_textedit.toPlainText()
        # 内容変化があれば dirty フラグを立てる(_write_comment_to_node が判定)
        self._write_comment_to_node(target, new_comment)
        self._comment.document().setModified(False)

    def _on_node_comment_requested(self: "MainWindowProto", node):
        """ノードの右クリックメニューからコメント追加/編集が選ばれた。
        該当ノードに移動してからコメントオーバーレイを開く。
        ノード移動の手順は _on_branch_node_clicked と同じ流れ
        (現在コメントの保存 → go_to_node → 盤面・グラフ更新)。"""
        if self._game_state and node is not self._game_state.current_node:
            self._save_comment_if_editing(self._game_state.current_node)
            self._game_state.go_to_node(node)
            self._refresh_board()
            self._update_graph()
        self._open_comment_overlay()

    def _toggle_comment_overlay(self: "MainWindowProto"):
        """ナビバーのコメントボタン: オーバーレイを開閉する。
        アニメ進行中は現在のアニメ方向と逆方向に切り替える。
        """
        # アニメ中: 進行方向で開閉判定
        kind = getattr(self, "_comment_anim_kind", None)
        if kind == "open":
            self._close_comment_overlay()
            return
        if kind == "close":
            self._open_comment_overlay()
            return
        # 通常時: 表示状態で判定
        if self._comment_overlay.isVisible():
            self._close_comment_overlay()
        else:
            self._open_comment_overlay()

    def _open_comment_overlay(self: "MainWindowProto"):
        """コメントオーバーレイを表示してフォーカスを当てる。
        スライドイン + opacity フェードのアニメで滑らかに表示する。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect, QApplication

        ov = self._comment_overlay
        # 既にアニメ中なら停止して状態をリセット
        self._cancel_comment_overlay_anim()

        # 最終ジオメトリ (=_place_panels が決めた正しい位置) を取得
        final_geom = ov.geometry()
        # 開始位置: 最終位置から SLIDE_PX 下にずらす(下から上へスライドイン)
        start_geom = QRect(final_geom.x(),
                           final_geom.y() + self._COMMENT_ANIM_SLIDE_PX,
                           final_geom.width(),
                           final_geom.height())

        # opacity effect をセット (完了時に解除する)
        eff = QGraphicsOpacityEffect(ov)
        eff.setOpacity(0.0)
        ov.setGraphicsEffect(eff)
        # ジオメトリは開始位置に置いてから show
        ov.setGeometry(start_geom)
        ov.show()
        ov.raise_()

        # アニメ: ジオメトリ (位置) と opacity の同時アニメ
        geom_anim = QPropertyAnimation(ov, b"geometry")
        geom_anim.setDuration(self._COMMENT_ANIM_DURATION)
        geom_anim.setStartValue(start_geom)
        geom_anim.setEndValue(final_geom)
        geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        op_anim = QPropertyAnimation(eff, b"opacity")
        op_anim.setDuration(self._COMMENT_ANIM_DURATION)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(geom_anim)
        group.addAnimation(op_anim)
        group.finished.connect(self._on_comment_overlay_open_done)
        self._comment_anim = group
        self._comment_anim_effect = eff
        self._comment_anim_running = True
        self._comment_anim_kind = "open"
        group.start()

        # アプリ全体のマウスクリックを監視してオーバーレイ外で閉じる
        QApplication.instance().installEventFilter(self)

    def _on_comment_overlay_open_done(self: "MainWindowProto"):
        """開くアニメ完了: opacity effect を外してフォーカスを当てる。"""
        ov = getattr(self, "_comment_overlay", None)
        if ov is not None:
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
        self._comment_anim = None
        self._comment_anim_effect = None
        self._comment_anim_running = False
        self._comment_anim_kind = None
        # フォーカスをテキストエディットに
        if self._comment_textedit is not None:
            self._comment_textedit.setFocus()

    def _cancel_comment_overlay_anim(self: "MainWindowProto"):
        """進行中のコメントオーバーレイアニメを破棄してリセットする。
        連打や開→閉の急な切替で呼ばれる。"""
        anim = getattr(self, "_comment_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
        ov = getattr(self, "_comment_overlay", None)
        if ov is not None:
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
        self._comment_anim = None
        self._comment_anim_effect = None
        self._comment_anim_running = False
        self._comment_anim_kind = None

    def _prewarm_comment_overlay(self: "MainWindowProto"):
        """コメントオーバーレイの初回 show() に約300ms かかる問題を緩和する。
        起動直後にユーザーから見えない位置で一度 show()/hide() を実行し、
        Qt 内部のスタイル解決・paint キャッシュ・フォントメトリクス計算等を
        済ませておく。これにより実際にユーザーがコメントボタンを押した時の
        初回表示も 2回目以降と同じ速度(<1ms)で開く。
        """
        if self._comment_overlay is None:
            return
        ov = self._comment_overlay
        # 元の geometry を保存(_place_panels 後で正しい位置にあるはず)
        orig_geom = ov.geometry()
        # 画面外の負座標へ移動 → 物理的に見えない
        ov.setGeometry(-10000, -10000, max(100, orig_geom.width()),
                       max(100, orig_geom.height()))
        ov.show()
        # paint 系の初期化を確実に走らせるためイベントを処理
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        ov.hide()
        # eventFilter の installEventFilter は _open_comment_overlay 側でやるので
        # ここでは入れない(プリウォーム後にユーザーがボタンを押すまでは
        # オーバーレイ外クリック監視を起動しない)
        # geometry を元に戻す(次の _place_panels で再計算されるが念のため)
        ov.setGeometry(orig_geom)

    def _load_comment_to_overlay(self: "MainWindowProto", text: str):
        """手が切り替わったときにオーバーレイのテキストを更新する。
        オーバーレイが開いていれば内容を更新、閉じていればそのまま。"""
        self._comment_textedit.blockSignals(True)
        self._comment_textedit.setPlainText(text or "")
        self._comment_textedit.blockSignals(False)

    def _close_comment_overlay(self: "MainWindowProto"):
        """コメントオーバーレイを閉じて現在ノードに保存。
        スライドアウト + opacity フェードのアニメで滑らかに閉じる。
        """
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect, QApplication

        # 内容の保存は閉じ始めの時点で行う(アニメ中にノードが変わる可能性に備える)
        # _write_comment_to_node を経由することで dirty フラグも適切に立つ。
        if self._game_state:
            node = self._game_state.current_node
            if node:
                self._write_comment_to_node(node, self._comment_textedit.toPlainText())

        # eventFilter は即座に外す(アニメ中の外側クリックで再度 close が
        # 呼ばれて重複しないように)
        QApplication.instance().removeEventFilter(self)

        ov = self._comment_overlay
        if not ov.isVisible():
            return

        # 進行中のアニメ(開く中の可能性)を破棄してから閉じるアニメを開始
        self._cancel_comment_overlay_anim()

        cur_geom = ov.geometry()
        end_geom = QRect(cur_geom.x(),
                         cur_geom.y() + self._COMMENT_ANIM_SLIDE_PX,
                         cur_geom.width(),
                         cur_geom.height())

        eff = QGraphicsOpacityEffect(ov)
        eff.setOpacity(1.0)
        ov.setGraphicsEffect(eff)

        geom_anim = QPropertyAnimation(ov, b"geometry")
        geom_anim.setDuration(self._COMMENT_ANIM_DURATION)
        geom_anim.setStartValue(cur_geom)
        geom_anim.setEndValue(end_geom)
        geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        op_anim = QPropertyAnimation(eff, b"opacity")
        op_anim.setDuration(self._COMMENT_ANIM_DURATION)
        op_anim.setStartValue(1.0)
        op_anim.setEndValue(0.0)
        op_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(geom_anim)
        group.addAnimation(op_anim)
        group.finished.connect(self._on_comment_overlay_close_done)
        self._comment_anim = group
        self._comment_anim_effect = eff
        self._comment_anim_running = True
        self._comment_anim_kind = "close"
        group.start()

    def _on_comment_overlay_close_done(self: "MainWindowProto"):
        """閉じるアニメ完了: hide() + opacity effect 解除 + ジオメトリ復元。"""
        ov = getattr(self, "_comment_overlay", None)
        if ov is not None:
            ov.hide()
            try:
                ov.setGraphicsEffect(None)
            except Exception:
                pass
        self._comment_anim = None
        self._comment_anim_effect = None
        self._comment_anim_running = False
        self._comment_anim_kind = None
        # ジオメトリを正規位置に戻す: _place_panels が hidden 状態でも
        # 正しい位置を再計算してくれる(_comment_anim_running = False になった
        # 後に呼ぶことで上書きスキップを回避)
        self._place_panels()

    def _on_comment_overlay_changed(self: "MainWindowProto"):
        """テキスト変更時に即時保存。
        コメントオーバーレイ内のテキスト変更で毎キーストローク呼ばれる主経路。
        _write_comment_to_node 経由で内容変化があれば dirty フラグも立てる。
        """
        if self._game_state:
            node = self._game_state.current_node
            if node:
                self._write_comment_to_node(node, self._comment_textedit.toPlainText())

    def _is_inside_comment_overlay(self: "MainWindowProto", obj) -> bool:
        """obj がコメントオーバーレイ自身またはその子孫かを判定。
        オーバーレイ表示中の入力遮断で、オーバーレイ内部の操作だけを通すために使う。
        以下2方式を OR で組み合わせて堅牢化:
          (1) QWidget なら isAncestorOf で素直に判定
          (2) QObject.parent() チェーンを最大64階層辿る
              ※ QTextEdit の viewport 等、内部の特殊な子では (1) が
                False を返すケースがあるため、(2) を保険として併用する。
        """
        ov = getattr(self, "_comment_overlay", None)
        if ov is None or obj is None:
            return False
        # (1) QWidget は isAncestorOf で直接判定
        if isinstance(obj, QWidget):
            if obj is ov or ov.isAncestorOf(obj):
                return True
        # (2) QObject.parent() チェーンで保険判定
        node = obj
        for _ in range(64):
            if node is ov:
                return True
            try:
                parent = node.parent()
            except Exception:
                return False
            if parent is None:
                return False
            node = parent
        return False
