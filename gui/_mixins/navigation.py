"""
gui/_mixins/navigation.py — 棋譜の手戻り・分岐操作・盤面状態の更新を MainWindow に提供する Mixin。

依存: core.sgf_parser, core.game_state, core.katago_engine, gui.infra,
       gui.widgets.{board,panels}, PyQt6, time.

提供メソッド: スライダ・ホイール・分岐ノード・盤面クリック・盤面再描画など、
ユーザー操作と内部状態の同期にかかわるメソッド群。
"""
from __future__ import annotations
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtCore import QTimer

from core.sgf_parser import sgf_coord_to_pos, sgf_coord_to_human
from core.game_state import GameState
from core.katago_engine import KataGoEngine

from gui.infra import _profile, _profile_method
from gui.widgets.board import BoardWidget
from gui.widgets.panels import MoveInfoCard


class NavigationMixin:
    """棋譜の手戻り・分岐操作・盤面状態の更新を MainWindow に提供する Mixin。"""

    def _goto(self: "MainWindowProto", idx):
        """指定インデックスへ移動（ポンダリングあり）。
        メインライン上にいる場合: idx はメインライン上の打ち手インデックス。
        サブ分岐上にいる場合: idx は現在のパス(ルート→現在ノード)内の
                              インデックス。これによりスライダーや目差グラフで
                              サブ分岐内を辿る操作が可能になる。"""
        if not self._game_state: return
        self._save_comment_if_editing(self._game_state.current_node)
        target = self._resolve_goto_target(idx)
        if target is None: return
        self._game_state.go_to_node(target)
        self._refresh_board()

    def _goto_no_ponder(self: "MainWindowProto", idx):
        """ドラッグ中の盤面表示のみ更新（ポンダリングなし、軽量更新版）。
        stop_pondering の IPC コストを避けるため、ドラッグ開始時に
        _on_slider_pressed で1度だけ停止する設計。各フレームでは
        _refresh_board_minimal で最小限の更新のみ行う。
        idx の解釈は _goto と同じ(現在のパス上 or メインライン上)。
        """
        if not self._game_state: return
        target = self._resolve_goto_target(idx)
        if target is None: return
        self._game_state.go_to_node(target)
        self._refresh_board_minimal()

    def _resolve_goto_target(self: "MainWindowProto", idx):
        """スライダー値 idx を「移動先ノード」に変換する共通ヘルパー。
        idx は 0 始まり: 0 = ルート(初期局面)、N = N手目。

        - メインライン上にいる場合: main_line[idx] へ
        - サブ分岐上にいる場合: 現在パスの「ルート + 打ち手」内に閉じる
          (idx がパス長を超えたらサブ分岐の末尾でクランプ)。
          メインラインに戻るには分岐ツリーから操作する必要がある。
        """
        if not self._game_state or not self._game:
            return None
        cur = self._game_state.current_node
        main_line = self._game.main_line()
        on_main_line = cur in main_line
        if on_main_line:
            i = max(0, min(idx, len(main_line) - 1))
            return main_line[i] if main_line else None
        else:
            # サブ分岐上: 「ルート → サブ分岐の真の末尾」までのパスを構築する。
            # スライダー/グラフの total は同じ範囲で計算されているため、
            # idx が現在ノードを超えてサブ分岐の末尾まで指せるようにするため、
            # path_to_root(現在ノードまで)ではなく主分岐の子を末端まで辿る。
            tail = cur
            while tail.children:
                tail = tail.children[0]
            # tail からルートまで遡って逆順にする
            extended_path = []
            n = tail
            while n is not None:
                extended_path.append(n)
                n = n.parent
            extended_path.reverse()  # ルート → tail
            # 「ルート + 打ち手」で構成(コメントノード等の move_color なしを除外)
            move_path = [n for n in extended_path
                         if n is extended_path[0] or n.move_color]
            # サブ分岐内に閉じる: idx を範囲内にクランプ
            i = max(0, min(idx, len(move_path) - 1))
            return move_path[i] if move_path else None

    def _goto_first(self: "MainWindowProto"): self._goto(0)

    def _goto_last(self: "MainWindowProto"):  self._goto(len(self._game.main_line()) - 1 if self._game else 0)

    def _prev(self: "MainWindowProto"):
        if self._game_state:
            # ルートノード（親なし）では何もしない
            if self._game_state.current_node.parent is None:
                return
            self._save_comment_if_editing(self._game_state.current_node)
            self._game_state.backward()
            self._refresh_board()

    def _next(self: "MainWindowProto"):
        if self._game_state:
            # 最終手（子ノードなし）では何もしない
            if not self._game_state.current_node.children:
                return
            self._save_comment_if_editing(self._game_state.current_node)
            cap_before = (self._game_state._black_captures + self._game_state._white_captures)
            self._game_state.forward()
            cap_after = (self._game_state._black_captures + self._game_state._white_captures)
            if cap_after > cap_before:
                self._sound.play_capture()
            elif self._game_state.current_node.move_color:
                self._sound.play_place()
            self._refresh_board()

    def _on_slider_value_changed(self: "MainWindowProto", v):
        """クリックによる値変化時のみ処理（ドラッグ中は sliderMoved が担当）。"""
        if not self._navbar.slider.isSliderDown():
            self._goto(v)

    def _on_slider_pressed(self: "MainWindowProto"):
        """ドラッグ開始時に1度だけポンダリングを停止する（追従性能のため）。
        ドラッグ中の各フレームで stop_pondering を呼ぶと IPC コストでマウスに
        ついていけなくなるため、開始時に1度だけ停止し、リリース時に再開する。
        """
        if self._engine and self._engine.is_running():
            self._engine.stop_pondering()

    def _on_slider_drag(self: "MainWindowProto", v):
        """ドラッグ中: 盤面表示のみ更新、ポンダリングなし。"""
        self._goto_no_ponder(v)

    def _on_slider_released(self: "MainWindowProto"):
        """ドラッグ完了: 現在のスライダー値でポンダリング付き移動。"""
        v = self._navbar.slider.value()
        self._goto(v)

    def _on_graph_dragged(self: "MainWindowProto", idx: int):
        """目差グラフのクリック/ドラッグ中: 盤面表示のみ更新（ポンダリングなし）。
        idx の解釈は _goto と同じ(メインライン上なら main_line のインデックス、
        サブ分岐上なら path_to_root のインデックス)。"""
        if not self._game_state:
            return
        self._goto_no_ponder(idx)

    def _on_graph_released(self: "MainWindowProto", idx: int):
        """目差グラフのクリック完了: その手に確定移動（ポンダリング再開）。"""
        if not self._game_state:
            return
        self._goto(idx)

    def _wheel_step(self: "MainWindowProto", forward: bool):
        """ホイール 1 段あたりの処理。
        GameState の進行・着手音は常に即時実行。
        _refresh_board() は 「先頭即時 + 後続スロットル」 で振り分け:
          ・直前のホイールから _WHEEL_THROTTLE_MS 以上経過 → 即時実行
          ・閾値未満で連続発火 → タイマー予約(中間間引き)、最終位置で1回実行
        """
        if not self._game_state:
            return
        gs = self._game_state
        # 端での発火は即終了(タイマー・状態変更ともになし)
        if forward:
            if not gs.current_node.children:
                return
        else:
            if gs.current_node.parent is None:
                return
        # コメント編集中なら保存(キーボード操作の _next/_prev と同じ扱い)
        self._save_comment_if_editing(gs.current_node)
        # 状態進行 + 着手音は即時(ホイール連続時にも音は鳴らしておく)
        if forward:
            cap_before = (gs._black_captures + gs._white_captures)
            gs.forward()
            cap_after = (gs._black_captures + gs._white_captures)
            if cap_after > cap_before:
                self._sound.play_capture()
            elif gs.current_node.move_color:
                self._sound.play_place()
        else:
            gs.backward()

        # ── _refresh_board の「先頭即時 + 後続スロットル」振り分け ──
        # 初回呼び出し用にタイマーを準備
        if self._wheel_refresh_timer is None or self._wheel_refresh_timer is None:
            from PyQt6.QtCore import QTimer
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._wheel_trailing_refresh)
            self._wheel_refresh_timer = t
        if self._wheel_last_refresh_t is None:
            self._wheel_last_refresh_t = 0.0

        now_t = time.monotonic()
        elapsed_ms = (now_t - self._wheel_last_refresh_t) * 1000.0
        if elapsed_ms >= self._WHEEL_THROTTLE_MS:
            # 先頭(または十分間隔があいた次の回): 即時実行
            # 通常の _refresh_board() を呼ぶ(skip_ponder=False)。 これにより、
            # 末尾で _start_pondering_current() が走って ponder が即時開始される。
            # 既解析の手に進んだ場合は、 _refresh_board 内で _node_analyses から
            # ma を取得して set_position(stones, ma.best_moves, ...) として
            # 候補手が即時表示される。
            # 連続ホイール時の中間結果は _on_ponder_result の node 不一致チェック
            # (L11627) で捨てられるため、 ponder の起動コスト(IPC ~1.5ms)以外の
            # オーバーヘッドはない。
            self._wheel_last_refresh_t = now_t
            self._refresh_board()
        else:
            # 連続発火中: 最終位置で1回だけ実行するようタイマー予約
            self._wheel_refresh_timer.start(self._WHEEL_THROTTLE_MS)

    def _wheel_trailing_refresh(self: "MainWindowProto"):
        """ホイールスロットル中の最終 _refresh_board 実行(タイマー発火経由)。
        ここで ponder を開始する(skip_ponder=False、 デフォルト)。
        次の単発ホイールが直後に来たときに即時実行と誤判定しないよう、
        _wheel_last_refresh_t をここでも更新する。
        """
        self._wheel_last_refresh_t = time.monotonic()
        # 末尾は通常の _refresh_board(=skip_ponder=False) で ponder 再開
        self._refresh_board()

    def wheelEvent(self: "MainWindowProto", ev):
        if self._left_stack.currentIndex() == 1:  # ウェルカム画面
            ev.ignore()
            return
        delta = ev.angleDelta().y()
        if delta < 0:
            self._wheel_step(forward=True)
        elif delta > 0:
            self._wheel_step(forward=False)

    def _on_branch_node_clicked(self: "MainWindowProto", node):
        """分岐ツリーのノードをクリック → そこへジャンプ。"""
        if not self._game_state: return
        with _profile("node_clicked.save_comment"):
            self._save_comment_if_editing(self._game_state.current_node)
        with _profile("node_clicked.goto_node"):
            self._game_state.go_to_node(node)
        # _refresh_board と _update_graph はそれぞれ自身に @_profile_method 付き
        self._refresh_board()
        #  分岐ノードが対象に含まれるよう _update_graph も明示的に呼ぶ)
        self._update_graph()

    def _on_delete_branch_node(self: "MainWindowProto", node):
        """分岐ツリーの右クリックメニューから指定ノードを削除する。"""
        if not self._game_state:
            return
        parent = node.parent
        if parent is None:
            return  # ルートは削除不可（念のため）

        # 削除対象ノードが現在カーソル位置か、その子孫にいる場合は親へ移動
        cur = self._game_state.current_node
        def _is_descendant(target, ancestor):
            n = target
            while n is not None:
                if n is ancestor:
                    return True
                n = n.parent
            return False

        if cur is node or _is_descendant(cur, node):
            self._game_state.go_to_node(parent)

        # 親の children から除去
        if node in parent.children:
            parent.children.remove(node)

        # 解析キャッシュから再帰削除
        def _remove_from_cache(n):
            self._node_analyses.pop(id(n), None)
            for c in n.children:
                _remove_from_cache(c)
        _remove_from_cache(node)

        # 注: ノード削除は分岐探索の一部とみなし、dirty フラグは立てない。
        # 詳細は _write_comment_to_node の docstring 参照。

        # スライダー最大値を再計算
        main_line = self._game.main_line()
        total = max(0, len(main_line) - 1)
        self._navbar.slider.setMaximum(total)
        self._info.get_graph().set_total_moves(total)

        self._refresh_board()
        self._status_bar.showMessage("ノードを削除しました")

    def _on_move_number_anchor_requested(self: "MainWindowProto", node):
        """分岐ツリーの右クリックメニュー
        「この手以降の手順を表示」(通常ノード) / 「全手の手順を表示」(ルート) が
        選ばれたとき。

        指定ノードを起点として 1, 2, 3, ... の番号を表示する（振り直し方式）。
        - 通常ノード: クリックした手自身が "1"、以降が 2, 3, ...
        - ルートノード: 着手ではないため、1手目を "1" として 2, 3, ...
          （= 結果的に全手表示）

        内部状態 self._move_number_anchor は「番号 1 を振る手の path
        インデックス - 1」として保持する。これにより _refresh_board の
        ループ (range(anchor+1, len(path)) で i-anchor を番号にする) と整合する。
        """
        if not self._game:
            return
        # ルートから node までの距離を計算（root = 0、1手目 = 1, ...）
        depth = 0
        n = node.parent
        while n is not None:
            depth += 1
            n = n.parent
        # ルートクリック時は path[1] (=1手目) を "1" にしたいので anchor=0。
        # 通常ノード(depth>=1)クリック時は path[depth] (=その手) を "1" に
        # したいので anchor=depth-1。
        if depth == 0:
            self._move_number_anchor = 0
        else:
            self._move_number_anchor = depth - 1
        # 右クリックで起点設定したら手順トグルをONにする。
        # ToggleBar 側のスイッチを ON にすれば move_numbers_toggled シグナル経由で
        # _on_move_numbers_toggled が呼ばれ、_move_numbers_enabled の更新・永続化・
        # 盤面再描画まで一括で行われる（既に ON の場合はシグナル不発）。
        if not self._toggle_bar._sw_mn.isChecked():
            self._toggle_bar._sw_mn.setChecked(True)
        else:
            # 既に ON の場合（または ToggleBar 未生成の念のため）は手動で同期
            self._move_numbers_enabled = True
            self._refresh_board()

    def _build_moves_to_node(self: "MainWindowProto", node) -> list[tuple[str, str]]:
        """指定ノードまでの手順を [(color, gtp_coord), ...] で返す。"""
        # ルートから現在ノードまでのパスを取得
        path = []
        n = node
        while n is not None:
            path.append(n)
            n = n.parent
        path.reverse()  # ルートから順に並べる

        moves = []
        bs = self._game.board_size
        for n in path:
            color = n.move_color
            if not color:
                continue
            coord = n.get(color, "")
            if not coord or coord == "tt":
                moves.append((color, "pass"))
                continue
            pos = sgf_coord_to_pos(coord)
            if pos is None:
                moves.append((color, "pass"))
                continue
            col2, row2 = pos
            gtp = f"{'ABCDEFGHJKLMNOPQRST'[col2]}{bs - row2}"
            moves.append((color, gtp))

        return moves

    def _on_delete_node(self: "MainWindowProto"):
        """現在のノードを削除して1手戻る。
        現在ノードを親の children から除去し、親ノードへ移動する。
        ルートノード（親なし）では何もしない。
        """
        if not self._game_state:
            return
        node = self._game_state.current_node
        parent = node.parent
        if parent is None:
            # ルートは削除不可
            return

        # 親の children から現在ノードを除去（子孫も同時に消える）
        if node in parent.children:
            parent.children.remove(node)

        # 解析キャッシュから現在ノード以降を削除（メモリ節約）
        # ノードIDベースなので親を残して子孫だけ消す
        def _remove_from_cache(n):
            self._node_analyses.pop(id(n), None)
            for c in n.children:
                _remove_from_cache(c)
        _remove_from_cache(node)

        # 1手戻る（親へ移動）
        self._game_state.go_to_node(parent)

        # 注: ノード削除は分岐探索の一部とみなし、dirty フラグは立てない。
        # 詳細は _write_comment_to_node の docstring 参照。

        # スライダー最大値を再計算
        main_line = self._game.main_line()
        total = max(0, len(main_line) - 1)
        self._navbar.slider.setMaximum(total)
        self._info.get_graph().set_total_moves(total)

        self._refresh_board()
        self._status_bar.showMessage("手を削除しました")

    def _on_board_click(self: "MainWindowProto", col: int, row: int):
        """碁盤クリック。解析モード: 石を打つ/右クリックで1手戻る。"""
        # 右クリック: 1手戻る
        if col == -1 and row == -1:
            self._prev()
            return
        # 解析モード: 石を打つ
        if not self._game_state: return
        self._save_comment_if_editing(self._game_state.current_node)
        cap_before = (self._game_state._black_captures + self._game_state._white_captures)
        node = self._game_state.play(col, row)
        if node is None:
            self._status_bar.showMessage("着手不可（既に石がある、または自殺手）")
            return
        cap_after = (self._game_state._black_captures + self._game_state._white_captures)
        if cap_after > cap_before:
            self._sound.play_capture()
        else:
            self._sound.play_place()
        # 注: 盤面クリック (分岐探索) は dirty フラグを立てない。
        # 詳細は _write_comment_to_node の docstring 参照。
        self._refresh_board()

    def _build_states(self: "MainWindowProto"):
        """GameState を初期化し、スライダーの最大値を設定する。
        加えて、現在のソフト側ルール/コミ設定と棋譜の置き石を KataGo に伝える。
        SGF の KM/RU は信頼しない方針なので、ここでは無視する。
        盤面サイズ（SZ）のみ SGF から取得して BoardWidget と KataGoEngine に反映する。"""
        if not self._game: return
        self._node_analyses = {}       # ノード解析結果をリセット
        # 棋譜切替時に手順番号起点をリセット。
        # ただし「手順」トグルが ON のままなら、リセット先は None ではなく
        # 0 (= ルートから全手順) にする。None にしてしまうと、トグルが ON
        # のままでも _refresh_board で手順番号辞書が空になり、棋譜を開いた
        # 直後に手順が表示されない不具合になる。
        self._move_number_anchor = 0 if self._move_numbers_enabled else None
        if self._info is not None:
            self._info.get_graph().clear_data()  # グラフデータをリセット
        self._game_state = GameState(self._game)
        # 盤面サイズを反映（19路以外の棋譜にも対応）
        bs = self._game.board_size
        if bs != self._board.board_size:
            self._board.board_size = bs
            self._board.update()
        if self._engine and bs != self._engine.board_size:
            # ポンダリング中の古いクエリを止めてからサイズ変更（次回クエリで反映される）
            try:
                self._engine.stop_pondering()
            except Exception:
                pass
            self._engine.board_size = bs
        # 手数カウント（メインライン）
        main_line = self._game.main_line()
        total = max(0, len(main_line) - 1)
        self._navbar.slider.setMaximum(total)
        self._info.get_graph().set_total_moves(total)
        # ソフト側設定値 + 新しい棋譜の置き石をエンジンへ伝達。
        # ポンダリング再開は呼び出し側（_load_demo / _open_sgf_path 等）が
        # _goto_first → _refresh_board 経由で行うので、ここでは再開しない。
        self._apply_rules_komi_to_engine(restart_pondering=False)

    @_profile_method("_refresh_board")
    def _refresh_board(self, skip_ponder: bool = False):
        """現在の GameState の状態を盤面・UIに反映する。"""
        if not self._game_state: return
        # ノード移動・着手追加・分岐切替などのタイミングで呼ばれるので、
        # _update_graph の構造キャッシュを無効化しておく。
        # （cache_key は (cur_node, root) の id で構成されるため、ノード追加・
        # 削除では id が変わらず自動再構築されないケースがあり、明示的に無効化
        # する必要がある。）
        self._invalidate_graph_struct_cache()
        gs = self._game_state
        node = gs.current_node
        stones = gs.stones
        last_move = node.move
        sgf_comment = node.comment

        # 解析OFFかつキャッシュなしの手に移動した場合のみ_label_scoreをリセット
        # → ハイフン表示（解析ONの場合は前回値保持でチラつき防止）
        if self._info is not None:
            graph = self._info.get_graph()
            node_ma = self._node_analyses.get(id(node))
            if node_ma is None and not self._ai_enabled:
                graph._label_score = None

        # 手数インデックス（メインライン上の位置）
        path = gs.path_to_root()
        idx = len(path) - 1
        self._current_idx = idx

        # 解析結果: ノード単位で取得（分岐対応）
        ma = self._node_analyses.get(id(node), None)
        candidates = ma.best_moves if ma else []
        blunder = ma.blunder if ma else None

        # 次の手・分岐を収集
        next_moves = []
        for i, child in enumerate(node.children):
            if child.move_color and child.move:
                col2, row2 = child.move
                next_moves.append((col2, row2, child.move_color, i == 0))

        # 手順番号辞書を構築:
        #   self._move_number_anchor が None なら何も入れない（描画なし）
        #   anchor が指定されていれば、path[anchor+1] が "1"、以降を 2, 3, ... と
        #   振り直す（振り直し方式）。anchor は「番号 1 を振る手の path インデックス
        #   - 1」を保持しており、_on_move_number_anchor_requested 側で計算される。
        #   現在位置が anchor より先(= path 長が anchor 以下)なら自動的に空辞書になる。
        move_numbers: dict = {}
        anchor = self._move_number_anchor
        if self._move_numbers_enabled and anchor is not None:
            for i in range(anchor + 1, len(path)):
                n = path[i]
                if n.move_color and n.move:
                    move_numbers[n.move] = i - anchor  # 1始まり
        self._board.move_numbers = move_numbers
        with _profile("_refresh_board.set_position"):
            self._board.set_position(stones, candidates, last_move, blunder, next_moves, turn=gs.turn)
        with _profile("_refresh_board.update_analysis"):
            self._info.update_analysis(ma, sgf_comment)
        with _profile("_refresh_board.load_comment"):
            self._load_comment_to_overlay(sgf_comment)
        # 手の情報カードを更新:
        # キャッシュあり → 即時表示
        # キャッシュなし＋AI解析OFF → 基本情報（石・手数・座標）+ 「不明」バッジ
        # キャッシュなし＋AI解析ON → 呼ばない（ポンダリング結果が来た時に更新）
        if ma is not None:
            self._move_card.update_card(ma, ma.color)
        elif not self._ai_enabled:
            # 第2引数には「現在ノードで打たれた手の色」を渡す。
            # gs.turn は「次に打つ手番」なので逆になる点に注意。
            # ルートノード等で node.move_color が空の場合のみ gs.turn にフォールバック。
            mc = node.move_color if node.move_color else gs.turn
            # move_number: ルートから現在ノードまでの打ち手数（move_color がある手のみ）
            _mn = 0
            _n = node
            while _n:
                if _n.move_color:
                    _mn += 1
                _n = _n.parent
            # 座標は SGF 形式で取得し、人間可読形式（例: G16）に変換
            _coord_sgf = node.get(node.move_color, "") if node.move_color else ""
            _human = (sgf_coord_to_human(_coord_sgf, self._game.board_size)
                      if _coord_sgf and self._game else "")
            self._move_card.update_card(None, mc, _mn, _human)
        # アゲハマ: GameState._black_captures = 白が取った黒石の数 = 白のアゲハマ
        # なので表示用には入れ替えて渡す
        self._info.update_captures(gs._white_captures, gs._black_captures)
        # グラフ更新は _update_graph に委譲（メインライン描画＋縦線位置＋ラベル）
        self._update_graph()

        # スライダー同期:
        # 案A: 現在ノードがメインライン上ならスライダー maximum はメインライン長、
        #      サブ分岐上ならスライダー maximum は「サブ分岐の真の末尾まで」の
        #      手数(現在ノードを起点に主分岐方向に最後まで辿ったノードまで)。
        #      これによりサブ分岐内でも cur_idx < total となるケースが生まれ、
        #      スライダーがサブ分岐内で意味を持って前後に動く。
        all_main_nodes = self._game.main_line() if self._game else []
        path_nodes = self._game_state.path_to_root() if self._game_state else []
        path_move_count = sum(1 for n in path_nodes if n.move_color)
        main_move_count = sum(1 for n in all_main_nodes if n.move_color)
        on_main_line = node in all_main_nodes
        if on_main_line:
            total = main_move_count
        else:
            # サブ分岐の真の末尾を求める: 現在ノードから主分岐の子(children[0])を
            # 末端まで辿る。子がなくなった時点で末尾。
            tail = node
            while tail.children:
                tail = tail.children[0]
            # ルートから tail までの打ち手数 = サブ分岐全体の手数
            t = tail
            tail_move_count = 0
            while t.parent is not None:
                if t.move_color:
                    tail_move_count += 1
                t = t.parent
            total = tail_move_count
        # 現在の手数 = 現在ノードまでの「打ち手」数(ルートは含まない)
        cur_idx = path_move_count
        if self._navbar.slider.maximum() != total:
            self._navbar.slider.blockSignals(True)
            self._navbar.slider.setMaximum(total)
            self._navbar.slider.blockSignals(False)
        # スライダー値を更新(枠外にならないよう maximum 内でクランプ)
        self._navbar.slider.blockSignals(True)
        self._navbar.slider.setValue(min(cur_idx, self._navbar.slider.maximum()))
        self._navbar.slider.blockSignals(False)

        # 分岐ツリー更新＋現在ノードへスクロール（中央寄せ）
        _active_tree   = self._branch_tree
        _active_scroll = self._branch_scroll
        _active_tree.update_tree(gs, self._node_analyses)
        # 同じ行の移動かを判定
        same_row = bool(getattr(_active_tree, "_last_move_same_row", False))
        new_xy = getattr(_active_tree, "_last_move_new_xy", None)
        def _do_scroll():
            pos = _active_tree.current_node_xy()
            if not pos:
                return
            vp_w = _active_scroll.viewport().width()
            vp_h = _active_scroll.viewport().height()
            target_x = pos[0] - vp_w // 2
            target_y = pos[1] - vp_h // 2
            if same_row and new_xy is not None and _active_tree._cur_marker_xy is not None:
                # 同じ行の移動: 統合アニメでスクロールとリング絶対座標を同期駆動
                # (1つの valueChanged の中で両方を setValue するためフレーム間
                # ズレなし。リング画面位置は完全に固定または滑らかに移動する)
                self._animate_unified(
                    _active_scroll, target_x, target_y,
                    _active_tree, new_xy,
                )
            else:
                # 通常: スクロールアニメのみ(リングは update_tree が絶対座標
                # アニメ or 瞬時切替で処理済み)
                self._smooth_scroll_to(_active_scroll, target_x, target_y)
        if same_row:
            # 統合アニメ起動のため即座に呼ぶ(QTimer 経由だと 1 フレーム遅延)
            _do_scroll()
        else:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, _do_scroll)

        # ステータス: 手番表示
        turn_str = "黒番" if gs.turn == "B" else "白番"
        self._status_bar.showMessage(f"手 {idx}  {turn_str}  {'分岐あり (' + str(len(node.children)) + ')' if len(node.children) > 1 else ''}")



        # 局面が変わるたびにポンダリングを再開（ドラッグ中はスキップ）
        if not skip_ponder:
            self._start_pondering_current()

    def _refresh_board_minimal(self: "MainWindowProto"):
        """ドラッグ中用の最小更新: 盤面 + アゲハマ + ステータスバー + グラフ縦線
        + 分岐ツリー更新＋スクロール + スライダー値追従。
        通常の _refresh_board が行う以下の処理は省略する（リリース時の _goto で復活）:
          - コメント欄更新
          - 移動カード更新(評価は省略、軽量更新のみ)
          - グラフ折れ線再描画（縦線位置のみ更新）
          - 候補手・悪手ハイライト（ドラッグ中は意味が薄い）
        スライダー値はスライダー自身がドラッグ中の場合のみ省略し、
        目差グラフ等の外部ソースからのドラッグ時は連動更新する。
        これにより1フレームあたりの処理量を大幅削減し、マウス追従性を改善する。
        """
        if not self._game_state: return
        gs = self._game_state
        node = gs.current_node

        # 次の手・分岐
        next_moves = []
        for i, child in enumerate(node.children):
            if child.move_color and child.move:
                col2, row2 = child.move
                next_moves.append((col2, row2, child.move_color, i == 0))

        # 1) 盤面更新（候補手・悪手ハイライトは省略）
        self._board.move_numbers = {}  # 手順番号もドラッグ中は描画しない
        self._board.set_position(
            gs.stones, [], node.move, None, next_moves, turn=gs.turn)

        # 2) アゲハマ更新（軽量）
        self._info.update_captures(gs._white_captures, gs._black_captures)

        # 3) 手数インデックス更新＋ステータスバー
        path = gs.path_to_root()
        idx = len(path) - 1
        self._current_idx = idx
        turn_str = "黒番" if gs.turn == "B" else "白番"
        self._status_bar.showMessage(f"手 {idx}  {turn_str}")

        # 4) MoveInfoCard の手番・手数・座標を軽量更新（評価は更新しない）
        mc = node.move_color or ""
        _mn = idx
        _coord = node.get(mc, "") if mc else ""
        _human = sgf_coord_to_human(_coord, self._game.board_size) if _coord else ""
        cur_ma = self._node_analyses.get(id(node))
        self._move_card.update_card(cur_ma, mc, _mn, _human)

        # 4) グラフ縦線のみ追従（折れ線データは更新しない）
        cur_ma = self._node_analyses.get(id(node))
        score = cur_ma.score_lead if cur_ma else None
        try:
            self._info.get_graph().set_current(idx, score)
        except Exception:
            pass

        # 4.5) スライダー値を追従（スライダー自身がドラッグ中の場合のみ省略）
        # 目差グラフや他のソースからのドラッグの場合、スライダーは
        # 動きを知らないため明示的に値を更新する必要がある。
        # 自身がドラッグ中の場合は値が既に正しいので setValue 不要。
        try:
            slider = self._navbar.slider
            if not slider.isSliderDown():
                slider.blockSignals(True)
                slider.setValue(min(idx, slider.maximum()))
                slider.blockSignals(False)
        except Exception:
            pass

        # 5) 分岐ツリー更新＋現在ノードへスクロール（中央寄せ）
        _active_tree   = self._branch_tree
        _active_scroll = self._branch_scroll
        _active_tree.update_tree(gs, self._node_analyses)
        same_row = bool(getattr(_active_tree, "_last_move_same_row", False))
        new_xy = getattr(_active_tree, "_last_move_new_xy", None)
        def _do_scroll_2():
            pos = _active_tree.current_node_xy()
            if not pos:
                return
            vp_w = _active_scroll.viewport().width()
            vp_h = _active_scroll.viewport().height()
            target_x = pos[0] - vp_w // 2
            target_y = pos[1] - vp_h // 2
            if same_row and new_xy is not None and _active_tree._cur_marker_xy is not None:
                self._animate_unified(
                    _active_scroll, target_x, target_y,
                    _active_tree, new_xy,
                )
            else:
                self._smooth_scroll_to(_active_scroll, target_x, target_y)
        if same_row:
            _do_scroll_2()
        else:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, _do_scroll_2)
