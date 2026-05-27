"""
gui/_mixins/engine_ctrl.py — KataGo・pondering・棋力・ルール・コミ・音量・グラフ更新を
MainWindow に提供する Mixin。

依存: core (analyzer, katago_engine, sgf_parser, game_state),
       gui.infra, gui.widgets.*, gui.dialogs, PyQt6, pathlib, time.

提供メソッド: KataGo インスタンスのライフサイクル、ponder 結果反映、
棋力/ルール/コミ/音量の各種ハンドラ、グラフ再描画。
"""
from __future__ import annotations
import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gui._mixins._types import MainWindowProto

from PyQt6.QtCore import (
    Qt, QPoint, QSettings, QDateTime, QVariantAnimation,
)
from PyQt6.QtWidgets import (
    QApplication, QMenu, QSlider, QListWidget, QAbstractItemView,
    QWidgetAction,
)

from core.katago_engine import KataGoEngine
from core.analyzer import MoveAnalysis
from core.sgf_parser import sgf_coord_to_pos, sgf_coord_to_human
from core.game_state import GameState

from gui.infra import (
    BlunderInfo, SoundPlayer, set_player_rank, _profile, _profile_method,
)
from gui.widgets.board import BoardWidget
from gui.widgets.panels import InfoPanel, MoveInfoCard
from gui.widgets.common import _RankItemDelegate
from gui.dialogs import _FirstLaunchRankDialog

logger = logging.getLogger(__name__)


class EngineCtrlMixin:
    """KataGo・pondering・棋力・ルール・コミ・音量・グラフ更新を MainWindow に提供する Mixin。"""

    @classmethod
    def _scan_models(cls) -> list[str]:
        """katago/models/ 直下の .bin.gz ファイル名一覧を返す。

        サブフォルダは見ない。アルファベット順でソートして返す。
        起動時のモデル数チェック (_check_models_or_exit) でも使われる。
        """
        from pathlib import Path
        models_dir = Path(cls.KATAGO_DIR) / "models"
        if not models_dir.is_dir():
            return []
        try:
            return sorted(
                p.name for p in models_dir.iterdir()
                if p.is_file() and p.name.endswith(".bin.gz")
            )
        except OSError:
            return []

    def _create_engine(self: "MainWindowProto", model_file: str) -> "KataGoEngine":
        """指定モデルファイル名に対応する KataGoEngine インスタンスを生成して返す（起動はしない）。
        モデル数の事前チェックは main() 側の _check_models_or_exit() が担当しており、
        この関数に渡される model_file は katago/models/ 直下に存在することが保証されている。
        """
        from pathlib import Path
        models_dir = Path(self.KATAGO_DIR) / "models"
        model_path = models_dir / model_file

        return KataGoEngine(
            executable=str(Path(self.KATAGO_DIR) / "katago.exe"),
            model=str(model_path),
            config=str(Path(self.KATAGO_DIR) / "analysis.cfg"),
            human_model="",
            board_size=19, komi=6.5,
        )

    def _on_ai_toggle(self: "MainWindowProto", enabled: bool):
        """AIリアルタイム解析のON/OFFを切り替える。
        形勢判断が ON の場合は、AI 解析を OFF にしても ownership 取得用に
        ポンダリングは継続させる。
        """
        self._ai_enabled = enabled
        # BoardWidget の hints フェード判定で参照するため同期
        if self._board is not None:
            self._board.ai_enabled = enabled
        # 状態を永続化（再起動時に復元）
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("analysis_enabled", enabled)
        if enabled:
            self._start_pondering_current()
            self._status_bar.showMessage("AI解析 ON")
        else:
            # 形勢判断 ON なら ownership 取得用にポンダリングは継続
            if not self._ownership_enabled:
                self._engine.stop_pondering()
            self._status_bar.showMessage("AI解析 OFF")
            # AI 解析系の盤面情報（候補手・着手評価）をクリアして現局面を再描画
            self._refresh_board()

    def _on_ownership_toggle(self: "MainWindowProto", enabled: bool):
        """形勢判断オーバーレイのON/OFFを切り替える。
        AI 解析が OFF でも、形勢判断 ON なら ownership 取得用にポンダリングを走らせる。
        """
        self._ownership_enabled = enabled
        # 状態を永続化（再起動時に復元）
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("ownership_enabled", enabled)
        self._board.show_ownership = enabled
        if enabled:
            # 形勢判断 ON: ポンダリングを再起動する。
            # 以前は ownership を常時要求していたため「既存 ponder から自然に
            # 来るのを待つ」だけで済んでいたが、現在は includeOwnership を
            # _ownership_enabled に応じて条件的に送るため、 OFF→ON 切替時には
            # includeOwnership=True の新クエリを送り直す必要がある。
            # AI 解析 OFF でポンダリング未起動の場合も、ここで開始する。
            self._start_pondering_current()
        else:
            # 形勢判断 OFF: 表示中の ownership をクリアして盤面を再描画
            self._board.ownership = []
            # AI 解析も OFF ならポンダリング停止
            if not self._ai_enabled:
                self._engine.stop_pondering()
        self._board.update()

    def _on_move_numbers_toggled(self: "MainWindowProto", enabled: bool):
        """手順番号オーバーレイのON/OFFを切り替える（旧「表示」メニュー版を移設）。
        ON にしたとき起点未指定なら、ルートから全手順を表示する。
        """
        self._move_numbers_enabled = enabled
        # 状態を永続化（再起動時に復元）
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("move_numbers_enabled", enabled)
        if enabled and self._move_number_anchor is None:
            self._move_number_anchor = 0  # デフォルトはルートから全手順
        self._refresh_board()

    @_profile_method("_start_pondering_current")
    def _start_pondering_current(self: "MainWindowProto"):
        """現在局面のポンダリングを開始。
        AI 解析 または 形勢判断 のいずれかが ON なら走らせる
        （形勢判断のみ ON の場合は ownership 取得用）。
        加えて、直前手が未解析なら補助解析を発動して悪手判定の前手データを得る。
        """
        if not (self._ai_enabled or self._ownership_enabled): return
        if not self._game_state: return
        if not self._engine.is_running(): return

        node = self._game_state.current_node
        self._pondering_node = node
        # ── 案D: 各 ponder セッションの初回中間結果を判定するためのフラグ ──
        # ここでリセットすることで、 ノード切替/着手追加/分岐切替などで
        # _start_pondering_current が再度呼ばれるたびに「初回」が再定義される。
        # _on_ponder_result の冒頭スロットルで参照する。
        self._first_intermediate_received = False
        with _profile("_start_pondering_current.build_moves"):
            moves = self._build_moves_to_node(node)

        def _callback(result):
            # KataGo の受信スレッドから呼ばれるのでシグナル経由でUIスレッドへ渡す
            self._ponder_result_signal.emit(result, node)

        with _profile("_start_pondering_current.engine_call"):
            # ── GPU 負荷削減: 形勢判断 ON のときだけ ownership を要求 ─────
            # KataGo の includeOwnership=True はメモリ使用量を約2倍にし
            # GPU 負荷も上がる(公式 Analysis_Engine.md 参照)ため、
            # 形勢判断オーバーレイ表示中(_ownership_enabled)のときだけ True にする。
            # 形勢判断 OFF → ON 切替時は _on_ownership_toggle が
            # _start_pondering_current() を呼び直すため、ここで True に
            # 切り替わったクエリが送られる(古いクエリは terminate_all で破棄)。
            self._engine.start_pondering(
                moves=moves,
                on_result=_callback,
                include_ownership=self._ownership_enabled,
            )

    @_profile_method("_on_ponder_result")
    def _on_ponder_result(self: "MainWindowProto", result, node):
        """ポンダリング途中結果をUIスレッドで受け取る。"""
        if not self._game_state: return
        # 既に別の局面に移動していたら捨てる
        if node is not self._game_state.current_node: return

        # ── 中間結果の全体スロットリング ──
        # 中間結果(is_during_search=True)は 0.1 秒ごとに来るが、本メソッド全体の
        # コスト(勝率バー・MoveInfoCard・盤面再描画・ownership 361 セル更新等)を
        # 毎回走らせるのは重い。特に勝率バーと MetricLabel は値変更のたびに 300ms
        # の QVariantAnimation を再起動するため、0.1 秒ごとに呼ぶとアニメが常時
        # 進行中=60fps で update() が発火し続ける状態になる。
        # → 中間結果は _ponder_full_throttle_ms に1回までに間引く。
        # 最終結果(is_during_search=False)は必ず処理する(解析完了時の確定値表示)。
        # ただし「ノード切替直後の初回中間結果」だけは例外でスロットル免除する:
        # ホイール/キー操作で未解析の手に進んだ直後、 _refresh_board は ma=None
        # のため空 candidates で set_position して候補手が一旦消える。 ここで
        # スロットルが効いていると、 ponder の最初の中間結果 (~100ms 後) が
        # 早期 return されることがあり、 _ponder_full_throttle_ms 経過後の
        # 次の中間結果まで候補手が表示されない(=ユーザー証言のワンテンポ遅れ)。
        # 「初回中間結果」判定は _first_intermediate_received フラグで行う。
        # これは _start_pondering_current 呼び出し時に False にリセットされ、
        # 各 ponder セッションの最初の中間結果でのみ True に切り替わる。
        # 過去実装では _last_ponder_node_id で判定していたが、 これは最終結果側で
        # 先回り更新されてしまい、 中間結果の初回免除が一度も発火しなかった。
        is_first_intermediate = (
            result.is_during_search
            and not getattr(self, "_first_intermediate_received", False)
        )
        if result.is_during_search:
            if is_first_intermediate:
                # この ponder セッションの初回中間結果: 即時通過 + フラグ更新
                self._first_intermediate_received = True
                self._ponder_full_last_t = time.monotonic()
            else:
                now_full = time.monotonic()
                if (now_full - self._ponder_full_last_t) * 1000.0 < self._ponder_full_throttle_ms:
                    return
                self._ponder_full_last_t = now_full
        else:
            # 最終結果でもタイムスタンプは更新しておく(直後の中間結果が来た場合の
            # 二重実行を避ける)。
            self._ponder_full_last_t = time.monotonic()

        # ── AI 解析 OFF かつ 形勢判断 ON の場合: ownership だけ反映して終了 ──
        # AI 解析 OFF の意図を尊重し、候補手・勝率・MoveInfoCard・グラフ・解析キャッシュは
        # 一切更新しない。ownership オーバーレイのみ KataGo から取り込む。
        if not self._ai_enabled:
            if self._ownership_enabled and result.ownership:
                self._board.ownership = result.ownership
                self._board.update()
            return

        from core.analyzer import MoveAnalysis
        blunder = None

        color = node.move_color or self._game_state.turn
        coord = node.get(color, "") if node.move_color else ""
        human = sgf_coord_to_human(coord, self._game.board_size) if coord else "—"

        # ルート全体の visits（maxVisits の対象）を表示。best_moves[0].visits は
        # 1番候補手のみの visits なので、上限値と直接対応しない。
        visits = result.root_visits

        # ponder 結果で blunder の before/after を更新する。
        # after: ポンダリング（高visits）の root_win_rate / root_score_lead を使用
        # before: 直前ノード（parent）の解析結果のみを使用する（案B）
        #   → 直前ノードが未解析の場合は blunder を計算しない（異常値防止）
        if node.move_color:
            prev_node = node.parent
            prev_ma = self._node_analyses.get(id(prev_node)) if prev_node else None

            # after 値（ポンダリングの高品質値・黒視点→color視点変換）
            wr_after_black = result.root_win_rate
            sl_after_black = result.root_score_lead
            if color == "B":
                wr_after = wr_after_black
                sl_after = sl_after_black
            else:
                wr_after = 1.0 - wr_after_black
                sl_after = -sl_after_black

            # before 値: 直前ノードが解析済みの場合のみ計算する
            if prev_ma is not None:
                if color == "B":
                    wr_before = prev_ma.win_rate
                    sl_before = prev_ma.score_lead
                else:
                    wr_before = 1.0 - prev_ma.win_rate
                    sl_before = -prev_ma.score_lead
                wr_loss = max(0.0, wr_before - wr_after)
                sl_loss = max(0.0, sl_before - sl_after)
                best_move_str = prev_ma.best_moves[0].move if prev_ma.best_moves else ""
                best_move_wr  = prev_ma.best_moves[0].win_rate if prev_ma.best_moves else wr_before
                blunder = BlunderInfo(
                    win_rate_before=wr_before,
                    win_rate_after=wr_after,
                    win_rate_loss=wr_loss,
                    best_move=best_move_str,
                    best_move_wr=best_move_wr,
                    score_lead_before=sl_before,
                    score_lead_after=sl_after,
                    score_lead_loss=sl_loss,
                )
                blunder.played_move = human
            # 直前ノードが未解析の場合は blunder = None のまま（「—」表示）

        # move_number: ルートからノードまでの着手数（ルートノード自身は0）
        # node.move_color がある場合のみカウント対象。
        # 最適化: 過去に解析済みなら id(node) からキャッシュ、または親ノードの
        # move_number から差分計算する。これにより手数 N の親辿りループを回避。
        old_ma = self._node_analyses.get(id(node))
        if old_ma is not None:
            # 同じノードを再ポンダリング: move_number はノード固有なので流用
            _move_number = old_ma.move_number
        else:
            parent_node = node.parent
            parent_ma = self._node_analyses.get(id(parent_node)) if parent_node else None
            if parent_ma is not None:
                # 親が解析済み: 親の move_number に +1 (move_color あり) or +0
                _move_number = parent_ma.move_number + (1 if node.move_color else 0)
            else:
                # フォールバック: 親辿りで実数えする(初回のみのコスト)
                _path_count = 0
                _n = node
                while _n:
                    if _n.move_color:
                        _path_count += 1
                    _n = _n.parent
                _move_number = _path_count

        # win_rate: 常に黒視点で格納（勝率バー・グラフは黒視点を前提とする）
        # KataGo analysis モードの root_win_rate は常に黒視点で返る
        ma = MoveAnalysis(
            move_number=_move_number,
            color=color,
            coord=coord,
            human_coord=human,
            win_rate=result.root_win_rate,
            score_lead=result.root_score_lead,
            blunder=blunder,
            best_moves=result.best_moves,
            analysis_result=result,
            sgf_comment=node.comment,
        )
        # ── 既存解析の visits 劣化防止ガード ──
        # ホイール/キーボードで既解析の手に進むと、 _refresh_board で
        # 既存の高 visits の ma を使った set_position で候補手が即時表示される。
        # その直後に再 ponder が始まり、 ~100ms で最初の中間結果が来るが、
        # この時点では visits が低く(50〜200程度)、 上位候補手の精度が悪い。
        # この低 visits の ma で既解析データを上書きすると、 候補手の構成や
        # パーセント表示が一瞬「劣化した内容」に切り替わって見える(=ユーザー
        # 証言の「ワンテンポ遅れて出る」現象)。
        # → 既存の ma の visits >= 新しい visits なら、 既存を維持して
        #    set_position 等の後続処理にも既存の ma を渡す。
        old_ma2 = self._node_analyses.get(id(node))
        old_visits = (old_ma2.analysis_result.root_visits
                      if old_ma2 and old_ma2.analysis_result else 0)
        new_visits = result.root_visits
        if old_ma2 is not None and old_visits >= new_visits:
            # 既存の方が高 visits: ローカル変数も既存に差し替えて後続処理へ
            ma = old_ma2
        else:
            # 新しい方が高 visits: 通常通り保存
            self._node_analyses[id(node)] = ma

        # ── 値変化シグネチャ判定 ──
        # ポンダリング中は visits が増え続けて毎回ここまで来るが、上位の
        # 「実質的な値」(候補手の構成・勝率・目差)はある visits 数を超えると
        # ほとんど動かない。そのときに UI を毎回再描画するのは無駄なので、
        # シグネチャベースで「値の実質変化があった部分だけ」更新する。
        # シグネチャは粗い粒度(visits は 500 単位、勝率は 1% 単位、
        # score_lead は 0.5 目単位)にして、微小揺れでは更新が起きないように。
        # 同一ノード(node)内でのみ意味のある比較なので、ノードIDが変われば
        # 強制的に全更新(_last_ponder_sig_* をクリア)する。

        # 候補手シグネチャ(盤面候補手リング + next_moves)
        # 上位8手の (move, visits//500 単位, score_lead 0.5 目単位) のタプル。
        # blunder の有無/カテゴリも含める(blunder バッジが変わるなら更新が必要)。
        cand_sig = tuple(
            (mi.move, mi.visits // 500, round(mi.score_lead * 2) / 2.0)
            for mi in result.best_moves[:8]
        )
        # next_moves はノード構造由来なので id(node) で代替できる
        blunder_cat = blunder.category if blunder else None
        board_sig = (id(node), cand_sig, blunder_cat)

        # 解析カード/勝率シグネチャ(MoveInfoCard + ScoreBoard 勝率)
        # 勝率は 1% 単位、目差は 0.5 目単位、blunder カテゴリ。
        analysis_sig = (
            id(node),
            round(result.root_win_rate, 2),
            round(result.root_score_lead * 2) / 2.0,
            blunder_cat,
        )

        # 重い処理(グラフ + 分岐ツリー)用シグネチャ
        # 解析済みノード総数 + 現在ノードの ma の代表値。
        # ノード総数が増えるか現在 ma の値が変わったときだけ走らせる。
        heavy_sig = (
            id(node),
            len(self._node_analyses),
            round(result.root_score_lead * 2) / 2.0,
            blunder_cat,
        )

        # ノードが変わったら必ず全更新(シグネチャキャッシュをクリア)
        if getattr(self, "_last_ponder_node_id", None) != id(node):
            self._last_ponder_node_id = id(node)
            self._last_ponder_sig_board = None
            self._last_ponder_sig_analysis = None
            self._last_ponder_sig_heavy = None

        # 最終結果(is_during_search=False)はシグネチャに関わらず必ず全更新する
        # (確定値表示のため。確定値とアニメ起動でユーザーの注意を引きたい)。
        is_final = not result.is_during_search
        force_all = is_final

        # 候補手・盤面の更新(set_position は ownership も毎回渡している)
        # ownership 自体の差分比較はコストが高い(361セル)ので、ここでは
        # board_sig の他要素で代替判定する。ownership は scrolling 系の
        # アニメではないため、 1〜2 フレーム遅れても見た目の影響は小さい。
        if force_all or board_sig != self._last_ponder_sig_board:
            self._last_ponder_sig_board = board_sig
            # 候補手・勝率のみ更新（_refresh_board は重いので直接更新）
            next_moves = [(c.move[0], c.move[1], c.move_color, i == 0)
                          for i, c in enumerate(node.children) if c.move_color and c.move]
            self._board.set_position(
                self._game_state.stones, ma.best_moves, node.move, ma.blunder, next_moves,
                ownership=result.ownership if result.ownership else None,
                turn=self._game_state.turn)

        # 解析カード(MoveInfoCard) + 勝率バー更新
        if force_all or analysis_sig != self._last_ponder_sig_analysis:
            self._last_ponder_sig_analysis = analysis_sig
            self._info.update_analysis(ma, node.comment)
            # 手の情報カードをリアルタイム更新
            self._move_card.update_card(ma, ma.color)

        # コメントは(コメントオーバーレイが閉じている時)解析結果には依存
        # しないが、ノード切替時には更新したい。シグネチャ判定とは独立して
        # 「ノード切替直後(=force_all 時)のみ」更新する。
        if force_all and not self._comment_overlay.isVisible():
            self._load_comment_to_overlay(node.comment)
        # アゲハマも変化なし、毎回呼んでも軽いのでそのまま
        # アゲハマ: GameState._black_captures = 白のアゲハマ、_white_captures = 黒のアゲハマ
        self._info.update_captures(
            self._game_state._white_captures, self._game_state._black_captures)

        turn_str = "黒番" if self._game_state.turn == "B" else "白番"
        self._status_bar.showMessage(
            f"解析中  {turn_str}  勝率 {result.root_win_rate*100:.1f}%"
            f"  visits {visits}"
        )

        # ── 重い処理は中間結果ではスロットリング + シグネチャチェック ──
        # _update_graph() と _branch_tree.update_tree() はどちらも手数 N に比例する
        # コストがかかる(O(N) のレイアウトと再描画)。reportDuringSearchEvery=0.1 で
        # 0.1 秒ごとに本コールバックが呼ばれるため、後半手数(200手目以降など)では
        # これが連続で走り続けて UI が重くなる。
        # ・最終結果(is_during_search=False)は必ず走らせる(評価バッジ・グラフが確実に確定)
        # ・中間結果は前回実行から _ponder_heavy_throttle_ms 経過していれば走らせる
        # ・ただしシグネチャが前回と同じならスキップ(同じ評価でも再描画コストを払うのは無駄)
        # ・ユーザー操作起点の update_tree 呼び出しは別経路から行われており、
        #   このスロットリングの対象外(新ノード出現アニメや現在ノード移動アニメ
        #   は影響を受けない)。
        now_t = time.monotonic()
        time_ok = is_final or (
            (now_t - self._ponder_heavy_last_t) * 1000.0 >= self._ponder_heavy_throttle_ms
        )
        sig_changed = force_all or heavy_sig != self._last_ponder_sig_heavy
        if time_ok and sig_changed:
            self._ponder_heavy_last_t = now_t
            self._last_ponder_sig_heavy = heavy_sig
            # グラフをリアルタイム更新（解析済みノードの目差を反映）
            self._update_graph()
            # 分岐ツリーをリアルタイム更新（評価バッジを即時反映）
            self._branch_tree.update_tree(self._game_state, self._node_analyses)

    def _show_first_launch_rank_dialog(self: "MainWindowProto"):
        """初回起動時の棋力選択ダイアログを表示し、選択値を保存する。
        QSettings.contains("player_rank") が False の時のみ呼ばれる想定。
        """
        dlg = _FirstLaunchRankDialog(self.RANK_OPTIONS, parent=self)
        # 親 (= MainWindow) の中央に配置
        dlg.adjustSize()
        parent_geo = self.geometry()
        x = parent_geo.x() + (parent_geo.width() - dlg.width()) // 2
        y = parent_geo.y() + (parent_geo.height() - dlg.height()) // 2
        dlg.move(x, y)
        # モーダル表示 (キャンセル不可なので必ず accept で抜ける)
        dlg.exec()
        rank = dlg.selected_rank()
        if rank is not None:
            from PyQt6.QtCore import QSettings as _QS_save
            _QS_save("Kizuki", "Kizuki").setValue("player_rank", int(rank))
            set_player_rank(int(rank))
            # 棋力リストの選択中アイテムも更新(通常は _on_rank_action 経由だが
            # ここはダイアログから直接設定するので手動で同期する)
            lw = getattr(self, "_rank_list_widget", None)
            if lw is not None:
                for i in range(lw.count()):
                    it = lw.item(i)
                    if it is None:
                        continue
                    v = it.data(Qt.ItemDataRole.UserRole)
                    it.setData(Qt.ItemDataRole.UserRole + 1, (v == rank))
                lw.viewport().update()
            # 解析キャッシュをクリアして再描画
            self._node_analyses = {}
            if self._game_state:
                self._refresh_board()
                self._update_graph()
            if self._ai_enabled:
                self._start_pondering_current()

    def _on_rank_action(self: "MainWindowProto", rank_val: int):
        """棋力メニューのアクション選択時。
        棋力変更後は解析キャッシュをクリアし、再解析を促す。
        """
        set_player_rank(rank_val)
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("player_rank", rank_val)
        # 棋力リスト各行の選択中フラグを更新 (UserRole+1)。
        # 描画は _RankItemDelegate が UserRole+1 を見て行うので、
        # データ更新後に viewport().update() で再描画をトリガする。
        lw = getattr(self, "_rank_list_widget", None)
        if lw is not None:
            for i in range(lw.count()):
                it = lw.item(i)
                if it is None:
                    continue
                v = it.data(Qt.ItemDataRole.UserRole)
                is_checked = (v == rank_val)
                it.setData(Qt.ItemDataRole.UserRole + 1, is_checked)
            lw.viewport().update()
        # 棋力閾値が変わるので解析結果のキャッシュを破棄し、現在局面を
        # 再解析できるよう再描画 + 必要ならポンダリング再開
        self._node_analyses = {}
        if self._game_state:
            self._refresh_board()
            self._update_graph()
        if self._ai_enabled:
            self._start_pondering_current()
        # 棋力閾値が変わるので解析結果のキャッシュを破棄し、現在局面を
        # 再解析できるよう再描画 + 必要ならポンダリング再開
        self._node_analyses = {}
        if self._game_state:
            self._refresh_board()
            self._update_graph()
        if self._ai_enabled:
            self._start_pondering_current()

    def _on_rank_menu_about_to_show(self: "MainWindowProto"):
        """棋力メニュー表示直前のフック。
        埋め込んだ QListWidget の高さを MainWindow に合わせて制限し、
        画面外はみ出しを防ぐ。同時に選択中項目を可視位置までスクロールする。

        実装メモ:
          ・QMenu の組み込みスクロール矢印は WA_TranslucentBackground +
            FramelessWindowHint 環境で描画されない (Qt のスクロール矢印
            描画パスが半透明背景と相性が悪い)。そのため棋力メニューだけは
            QWidgetAction + QListWidget でスクロール可能なリストを埋め込む
            方式を採用している(コミメニューの「その他」インラインウィジェット
            と同じ哲学)。
          ・上下余白: MainWindow 上下に各 32px の余白を残し、リストが
            アプリ枠の中に視覚的に収まるようにする。
          ・最小高: 計算結果が極端に小さくなった場合の保険として最低 200px
            は確保する(項目 5〜6 個分)。
          ・ウィンドウリサイズ後の初回表示で古い高さが反映される問題への
            対策:
              1. processEvents() で保留中のリサイズイベントを反映させる。
              2. _on_rank_menu_about_to_hide でメニュー/リストの固定サイズ
                 を解除しておき、次回 aboutToShow で確実にゼロから再計算
                 させる(Qt の QMenu 内部 sizeHint キャッシュ回避)。
              3. setFixedHeight 後に親 QMenu.adjustSize() で実サイズ強制
                 再計算。
        """
        lw = getattr(self, "_rank_list_widget", None)
        if lw is None:
            return

        # 保留中のリサイズイベントを処理してから現在の高さを取得する。
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # MainWindow 高さに対する上下余白 (px) と最小高
        MARGIN = 32
        MIN_H = 200
        win_h = max(self.height(), MIN_H + MARGIN * 2)
        cap_h = max(MIN_H, win_h - MARGIN * 2)

        # 項目を全部表示した時の自然な高さ
        # = 行高 × 項目数 + 上下フレーム余白
        row_h = lw.sizeHintForRow(0) if lw.count() > 0 else 28
        natural_h = row_h * lw.count() + 2 * lw.frameWidth() + 4
        target_h = min(natural_h, cap_h)
        lw.setFixedHeight(target_h)
        lw.updateGeometry()

        # 親 QMenu (棋力サブメニュー) のジオメトリを強制再計算。
        rm = getattr(self, "_rank_menu", None)
        if rm is not None:
            # Qt が古い sizeHint を保持していることがあるため、
            # 一度メニュー本体の固定サイズも明示的に上書きする。
            # adjustSize() は子の sizeHint を聞いて自分のサイズを決め直す。
            rm.setFixedHeight(target_h + 8)  # +8 は QMenu の上下 padding 分
            rm.adjustSize()

        # 選択中項目を可視位置へ自動スクロール
        from PyQt6.QtWidgets import QAbstractItemView
        for i in range(lw.count()):
            item = lw.item(i)
            if item is not None and item.data(Qt.ItemDataRole.UserRole + 1):
                lw.setCurrentItem(item)
                lw.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
                break

    def _on_rank_menu_about_to_hide(self: "MainWindowProto"):
        """棋力メニューが閉じる直前に、固定サイズ制約を解除する。
        次回 aboutToShow で「現在の」MainWindow サイズに基づき、ゼロから
        確実に再計算できるようにするため。Qt の QMenu は内部に sizeHint
        キャッシュを持っており、setFixedHeight した値をそのまま再利用する
        ことがある。これがウィンドウリサイズ後の初回表示で古いサイズが
        残る原因と推定。
        """
        lw = getattr(self, "_rank_list_widget", None)
        if lw is not None:
            # setFixedHeight で固定された min/max を解除して、自由に
            # サイズを取れる状態に戻す。
            lw.setMinimumHeight(0)
            lw.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
        rm = getattr(self, "_rank_menu", None)
        if rm is not None:
            rm.setMinimumHeight(0)
            rm.setMaximumHeight(16777215)

    def _apply_rules_komi_to_engine(self: "MainWindowProto", *, restart_pondering: bool = True):
        """現在のルール・コミ・置き石を解析エンジンに反映する。

        SGF が読み込まれていれば、その AB/AW を置き石として渡す。
        restart_pondering=True なら現在表示中ノードのポンダリングを再起動する。
        """
        if not self._engine:
            return
        # 置き石を SGF から取得（読み込まれていなければ空リスト）
        initial_stones: list[tuple[str, str]] = []
        if self._game is not None:
            root = self._game.root
            bs = self._game.board_size
            cols = "ABCDEFGHJKLMNOPQRST"
            for color, key in [("B", "AB"), ("W", "AW")]:
                for coord in root.get_all(key):
                    pos = sgf_coord_to_pos(coord)
                    if pos is None:
                        continue
                    col, row = pos
                    gtp = f"{cols[col]}{bs - row}"
                    initial_stones.append((color, gtp))
        # ポンダリングを停止してから設定変更（次クエリから反映）
        if self._engine.is_running():
            try:
                self._engine.stop_pondering()
            except Exception:
                pass
            try:
                self._engine.set_game_info(
                    self._current_komi, self._current_rules, initial_stones)
            except Exception as e:
                logger.warning("Failed to apply game info to engine: %s", e)
        # 解析を再開
        if restart_pondering:
            try:
                self._start_pondering_current()
            except Exception:
                pass

    def _on_rules_changed(self: "MainWindowProto", rules_key: str):
        """ルールメニュー変更時。
        ルールを更新すると同時にコミもそのルールの標準値にリセットする。
        例: 中国ルール→7.5、日本ルール→6.5、NZ→7.0。
        """
        if rules_key == self._current_rules:
            return
        self._current_rules = rules_key
        # ルール標準コミを取得（不明なルールは現状の DEFAULT_KOMI）
        new_komi = self.RULE_DEFAULT_KOMI.get(rules_key, self.DEFAULT_KOMI)
        komi_changed = abs(new_komi - self._current_komi) > 1e-6
        if komi_changed:
            self._current_komi = new_komi
            # 解析キャッシュをクリア（コミが変わると形勢が変わるため）
            self._node_analyses = {}
            # ルール由来のコミ変更はプリセット標準値(6.5/7.5など)に切替なので
            # 「その他」経由フラグはクリアし、プリセット側のチェックに戻す。
            self._komi_via_other = False
            self._sync_komi_menu_check(new_komi)
        # QSettings 保存
        from PyQt6.QtCore import QSettings
        qs = QSettings("Kizuki", "Kizuki")
        qs.setValue("katago_rules", rules_key)
        if komi_changed:
            qs.setValue("katago_komi", new_komi)
        # エンジンに反映
        self._apply_rules_komi_to_engine()
        # InfoPanel 表示を更新
        if self._info is not None:
            try:
                self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            except Exception:
                pass

    def _sync_komi_menu_check(self: "MainWindowProto", komi: float):
        """コミメニューのチェック状態を現在値と「経路フラグ」に応じて同期する。

        ・_komi_via_other == False(プリセットボタン経由で確定した最新):
            → プリセット側のチェックを付与。「その他」側はチェックなし。
              「その他」ウィジェットの数値は変更しない(ユーザーが調整中の値を保持)。
        ・_komi_via_other == True(その他クリック経由で確定した最新):
            → プリセットチェック全解除、「その他」側にチェック付与。
              ウィジェットの数値も現在値に同期する。

        ルール変更による自動コミ調整(ルールメニューから来た時)は、プリセット
        値に切り替わるのが普通なので、プリセット経由扱いで _on_komi_changed を
        呼べばよい。
        """
        if self._action_komi is None:
            return  # メニュー構築前
        via_other = getattr(self, "_komi_via_other", False)
        if via_other:
            # その他経由: プリセットの全チェックを外す
            for act in self._action_komi.values():
                act.setChecked(False)
        else:
            # プリセット経由: 該当プリセットにチェック、該当無しなら全外し
            match = None
            for v, act in self._action_komi.items():
                if abs(v - komi) < 1e-6:
                    match = act
                    break
            if match is not None:
                match.setChecked(True)
            else:
                # プリセット経由なのに該当なし → 想定外ケースとして全外し
                for act in self._action_komi.values():
                    act.setChecked(False)
        # 「その他」側のチェック表示
        if self._komi_custom_widget is not None:
            self._komi_custom_widget.set_checked(via_other)
            # その他経由時はウィジェット数値も現在値に合わせる
            # (プリセット経由時はウィジェットの調整中値を保持するため変更しない)
            if via_other:
                self._komi_custom_widget.set_value(komi)

    def _on_komi_changed(self: "MainWindowProto", komi: float):
        """プリセットボタン経由でのコミ変更。エンジン設定更新 + QSettings 保存 +
        メニュー側のチェック状態同期。プリセット経由なので _komi_via_other は False。"""
        if abs(komi - self._current_komi) < 1e-6:
            # 現在値と同じ → 「その他」経由フラグだけ解除して終了
            # (例: その他で 6.5 を確定した状態から、プリセット 6.5 を再クリック)
            if getattr(self, "_komi_via_other", False):
                self._komi_via_other = False
                self._sync_komi_menu_check(komi)
            return
        self._current_komi = komi
        self._komi_via_other = False
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("katago_komi", komi)
        # 解析キャッシュをクリア（コミ変更で形勢が変わるため）
        self._node_analyses = {}
        self._apply_rules_komi_to_engine()
        # メニューのチェック状態を同期(その他ウィジェットの値は変えない)
        self._sync_komi_menu_check(komi)
        if self._info is not None:
            try:
                self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            except Exception:
                pass

    def _on_komi_custom_confirmed(self: "MainWindowProto", komi: float):
        """「その他」インラインウィジェットのクリックで現在値を確定する。
        値がプリセットと同値であっても、「その他」経由として扱い、チェック印は
        「その他」側に付与する。確定後はメニューを閉じる。"""
        # 値が変わらない場合でも「その他」経由フラグだけ立てる必要がある
        # (例: 現在 6.5 プリセット選択中、その他で 6.5 のままクリック →
        #  チェックを「その他」側に移したい)
        changed = abs(komi - self._current_komi) >= 1e-6
        self._current_komi = komi
        self._komi_via_other = True
        from PyQt6.QtCore import QSettings
        qs = QSettings("Kizuki", "Kizuki")
        qs.setValue("katago_komi", komi)
        # 「その他」値の永続化(次回起動時の初期表示用)
        qs.setValue("katago_komi_other_value", komi)
        if changed:
            # 解析キャッシュをクリア
            self._node_analyses = {}
            self._apply_rules_komi_to_engine()
            if self._info is not None:
                try:
                    self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
                except Exception:
                    pass
        # メニューのチェック状態を同期
        self._sync_komi_menu_check(komi)
        # メニューを閉じる
        if self._komi_menu is not None:
            self._komi_menu.close()

    def _on_komi_realtime_change(self: "MainWindowProto", komi: float):
        """「その他」選択中の ± ボタンによる数値変更を即時反映する。
        _on_komi_custom_confirmed と違い、メニューは閉じない(連続調整のため)。
        - エンジン設定更新、解析キャッシュクリア、QSettings 保存
        - チェック表示は既に「その他」側に付いているため、_sync_komi_menu_check
          は呼ばずに最小限の状態更新で済ませる(再描画コストを抑える)。
        """
        if abs(komi - self._current_komi) < 1e-6:
            return
        self._current_komi = komi
        # _komi_via_other は True のまま維持(その他選択中の状態を保つ)
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("katago_komi", komi)
        # 解析キャッシュをクリア
        self._node_analyses = {}
        self._apply_rules_komi_to_engine()
        if self._info is not None:
            try:
                self._info.update_game_info(self._game, rules=self._current_rules, komi=self._current_komi)
            except Exception:
                pass

    def _on_komi_other_value_adjusted(self: "MainWindowProto", value: float):
        """「その他」ウィジェットの ± ボタンで値が変わった時の永続化。
        プリセット選択中の ± 操作(コミ確定なし)でも、ユーザーの調整値を
        次回起動時に復元できるよう QSettings に保存する。
        コミ自体の確定処理は別経路(_on_komi_realtime_change /
        _on_komi_custom_confirmed)で行うので、ここでは保存だけ。"""
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("katago_komi_other_value", value)

    def _on_volume_changed(self: "MainWindowProto", value: int):
        """音量スライダー変更時。SoundPlayer に反映、ラベル更新、QSettings 保存。
        0% は実質ミュート（再生を抑制）として動作する。
        """
        vol = max(0.0, min(1.0, value / 100.0))
        self._sound.volume = vol
        if self._volume_label is not None:
            # ラベルは固定中央配置(縦スライダー)。テキストだけ更新する。
            self._volume_label.setText(f"{value}%")
        from PyQt6.QtCore import QSettings
        QSettings("Kizuki", "Kizuki").setValue("sound_volume", vol)
        # タイトルバー音量アイコンを 0% かどうかで切り替え
        if self._titlebar is not None and hasattr(self._titlebar, "_btn_volume"):
            self._titlebar.update_volume_icon(muted=(value <= 0))

    def _on_volume_icon_clicked(self: "MainWindowProto"):
        """タイトルバー右端の音量アイコンクリック時に、音量メニューをトグル popup する。
        - 閉じている時 → popup
        - 開いている時に再クリック → 閉じる
          (Qt は popup 外クリック扱いで先に menu を閉じてから clicked を発火させるため、
           受信時 isVisible() == False になっている。set_menu と同様、直近の close 時刻を
           記録して 200ms 以内のクリックは「閉じる動作の続き」として再表示を抑制する)
        - 外側クリック → Qt 標準で自動的に閉じる(popup の挙動)
        位置はアイコンボタンの直下(VS Code/Win11 風)。
        """
        if self._volume_menu is None or self._titlebar is None:
            return
        btn = getattr(self._titlebar, "_btn_volume", None)
        if btn is None:
            return
        m = self._volume_menu

        # 初回呼び出し時に aboutToHide で close 時刻を記録するハンドラを接続
        # (set_menu と同じ仕組み)
        if not getattr(m, "_kizuki_volume_toggle_installed", False):
            self._volume_close_state = {"last_close_ms": 0}
            def _on_about_to_hide():
                from PyQt6.QtCore import QDateTime
                now_ms = QDateTime.currentMSecsSinceEpoch()
                self._volume_close_state["last_close_ms"] = now_ms
                # 他メニューと共通の「直近メニュー終了時刻」も更新
                # (タイトルバーのドラッグ判定で使われる)
                if self._titlebar is not None:
                    self._titlebar._last_any_menu_close_ms = now_ms
            m.aboutToHide.connect(_on_about_to_hide)
            m._kizuki_volume_toggle_installed = True

        # トグル: 直近(200ms以内)に閉じたばかりなら再表示しない
        from PyQt6.QtCore import QDateTime
        st = getattr(self, "_volume_close_state", {"last_close_ms": 0})
        now = QDateTime.currentMSecsSinceEpoch()
        if now - st["last_close_ms"] < 200:
            return

        # ボタン下端の左端をグローバル座標に変換して popup
        from PyQt6.QtCore import QPoint
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        # メニュー幅がボタンより広い場合、画面右端をはみ出さないよう左にずらす
        # (アイコンが右端付近にあるため特に重要)
        screen_geom = self.screen().availableGeometry() if self.screen() else None
        menu_size_hint = m.sizeHint()
        if screen_geom is not None:
            x_max = screen_geom.right() - menu_size_hint.width() - 4
            if pos.x() > x_max:
                pos.setX(max(screen_geom.left() + 4, x_max))
        m.popup(pos)

    def _volume_slider_mouse_press(self: "MainWindowProto", ev):
        """音量スライダーのグルーブクリック時に、クリック位置へハンドルを直接移動させる。
        ハンドル上のクリックは通常のドラッグ動作に委譲する。水平・垂直両対応。
        """
        if ev.button() == Qt.MouseButton.LeftButton:
            slider = self._volume_slider
            handle_half = SLIDER_HANDLE // 2
            is_vertical = slider.orientation() == Qt.Orientation.Vertical
            if is_vertical:
                # 縦: クリックの y 座標 → ハンドル位置(上=最大、下=最小)
                available = slider.height() - handle_half * 2
                ratio = ((slider.value() - slider.minimum()) /
                         max(1, slider.maximum() - slider.minimum()))
                # 現在のハンドルの y(上=最大なので 1-ratio)
                handle_pos = handle_half + (1.0 - ratio) * available
                click_pos = ev.position().y()
            else:
                available = slider.width() - handle_half * 2
                ratio = ((slider.value() - slider.minimum()) /
                         max(1, slider.maximum() - slider.minimum()))
                handle_pos = handle_half + ratio * available
                click_pos = ev.position().x()
            # ハンドル上のクリックは通常のドラッグに委譲
            if abs(click_pos - handle_pos) <= handle_half + 2:
                QSlider.mousePressEvent(slider, ev)
                return
            # ハンドル以外のクリック: クリック位置に直接ジャンプ
            pos = click_pos - handle_half
            r = max(0.0, min(1.0, pos / available if available > 0 else 0))
            if is_vertical:
                # 上が最大なので反転
                r = 1.0 - r
            value = round(slider.minimum() + r * (slider.maximum() - slider.minimum()))
            slider.setValue(value)
            # ジャンプ後、ドラッグ追従できるよう通常の press イベントも転送
            QSlider.mousePressEvent(slider, ev)
            ev.accept()
        else:
            QSlider.mousePressEvent(self._volume_slider, ev)

    def _invalidate_graph_struct_cache(self: "MainWindowProto"):
        """_update_graph() の構造キャッシュを無効化する。

        ノード移動、ノード追加・削除など、棋譜のツリー構造または現在ノードに
        変化があった場合に呼ぶ。次回の _update_graph() でキャッシュを再構築する。
        """
        self._graph_struct_cache = None

    @_profile_method("_update_graph")
    def _update_graph(self: "MainWindowProto"):
        """解析済みノードの目差グラフを更新する。
        一度描画したデータは前の手に戻っても消えないよう、
        現在のパス上の全解析済みノードを常にプロットする。

        構造キャッシュ:
        path_to_root() / main_line() / on_main_line 判定 / graph_total 計算は
        ノード構造と現在ノードに依存するが、_node_analyses の値変化(ポンダリング
        中の score_lead/blunder 更新)では変わらない。よってこれらは構造キャッシュ
        として保持し、ポンダリング中の繰り返し呼び出しでは再計算しない。
        ノード移動・追加・削除時は _invalidate_graph_struct_cache() でキャッシュを
        破棄する。
        """
        if not self._game or not self._game_state: return

        cur_node = self._game_state.current_node

        # ── 構造キャッシュの再利用 or 再構築 ──
        cache_key = (id(cur_node), id(self._game.root))
        cache = self._graph_struct_cache
        if cache is not None and cache[0] == cache_key:
            # キャッシュヒット: 構造系の派生量を流用
            _, move_nodes_in_path, main_move_nodes, on_main_line, graph_total = cache
            total_main = len(main_move_nodes)
        else:
            # キャッシュミス: path/main_line を再構築
            path_nodes = self._game_state.path_to_root()  # ルート→現在ノードの順
            move_nodes_in_path = [n for n in path_nodes if n.move_color]

            # メインライン全体も取得（手戻り後も先の解析済みデータを保持するため）
            main_line = self._game.main_line()
            main_move_nodes = [n for n in main_line if n.move_color]
            total_main = len(main_move_nodes)

            # graph_total: メインライン上なら total_main、サブ分岐上なら
            # 「サブ分岐の真の末尾までの手数」(現在ノードから主分岐の子を末端まで
            # 辿った長さ)。スライダーの maximum と整合する。
            on_main_line = cur_node in main_line
            if on_main_line:
                graph_total = total_main
            else:
                tail = cur_node
                while tail.children:
                    tail = tail.children[0]
                t = tail
                graph_total = 0
                while t.parent is not None:
                    if t.move_color:
                        graph_total += 1
                    t = t.parent

            self._graph_struct_cache = (
                cache_key, move_nodes_in_path, main_move_nodes,
                on_main_line, graph_total,
            )

        # プロット対象: 現在パス上のノード（解析済みのみ）
        # ただし現在パスがメインラインの場合はメインライン全体の解析済みノードも含める
        # X軸はスライダー値と合わせて 1始まり（1手目=1）とする
        # _node_analyses はポンダリング中に値が変わるので、ここは毎回再構築する。
        xs, ys = [], []

        # まず現在パス上の解析済みノードを収集
        for i, n in enumerate(move_nodes_in_path):
            ma = self._node_analyses.get(id(n))
            if ma is not None:
                xs.append(i + 1)   # 1始まり: スライダー値と一致
                ys.append(ma.score_lead)

        # 現在パスがメインラインと一致する範囲より先のメインライン解析済みノードも追加
        # （手戻り後も前回解析したデータを消さない）
        cur_path_len = len(move_nodes_in_path)
        for i, n in enumerate(main_move_nodes):
            if i < cur_path_len:
                continue  # 現在パスと重複する範囲はスキップ
            ma = self._node_analyses.get(id(n))
            if ma is not None:
                xs.append(i + 1)   # 1始まり
                ys.append(ma.score_lead)

        # 現在位置のインデックス（1始まり）
        cur_plot_idx = len(move_nodes_in_path) if move_nodes_in_path else 0
        cur_ma = self._node_analyses.get(id(cur_node))

        graph = self._info.get_graph()
        if xs:
            # 解析データあり: 通常通り曲線を描画
            graph.set_data_sparse(xs, ys, graph_total)
        else:
            # 解析データなし(解析OFF や新規作成直後): 曲線データは空のままで、
            # X軸範囲(目盛りラベル)だけ手数に応じて更新する。これにより
            # グラフ下部の手数表示と縦線位置が正しく描画される。
            graph.set_total_moves(graph_total)

        cur_score = cur_ma.score_lead if cur_ma else dict(zip(xs, ys)).get(cur_plot_idx)
        graph.set_current(cur_plot_idx, score=cur_score, no_data=(cur_score is None and not self._ai_enabled))
