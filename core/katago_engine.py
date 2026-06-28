"""
KataGo Analysis Engine communication layer.
GTPモードではなくanalysisモードを使用（高速）。
KataGo is licensed under Apache 2.0.
License: MIT

v3変更点:
- 受信専用スレッド（_reader_thread）を常時起動
- start_pondering / stop_pondering によるリアルタイム解析
"""

from __future__ import annotations

import subprocess
import threading
import time
import json
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class MoveInfo:
    move: str
    visits: int = 0
    win_rate: float = 0.0
    score_lead: float = 0.0
    prior: float = 0.0
    order: int = 0

    @property
    def loss(self) -> float:
        return getattr(self, "_loss", 0.0)

    @loss.setter
    def loss(self, v: float):
        self._loss = v


@dataclass
class AnalysisResult:
    move_number: int = 0
    turn_player: str = "B"
    root_win_rate: float = 0.5
    root_score_lead: float = 0.0
    root_visits: int = 0  # ルート全体の探索回数（maxVisits の対象）
    best_moves: list = field(default_factory=list)
    raw: str = ""
    is_during_search: bool = False
    # ownership[i] = +1.0(黒) 〜 -1.0(白)、盤面左上から行優先(row-major)
    # len == board_size * board_size、未取得時は空リスト
    ownership: list[float] = field(default_factory=list)


class KataGoEngine:
    """
    KataGo Analysis Engine を使った高速解析クラス。
    analysis モードはGTPモードより大幅に高速。

    ポンダリング（リアルタイム解析）:
        start_pondering(moves, on_result, max_visits) を呼ぶと、
        KataGoが解析を開始し、reportDuringSearchEvery 秒ごとに
        on_result(AnalysisResult) コールバックが呼ばれる。
        局面が変わったら再度 start_pondering を呼ぶだけでよい
        （前の解析は自動的に新しいクエリに切り替わる）。
        stop_pondering() で解析を停止する。
    """

    def __init__(
        self,
        executable: str,
        model: str,
        config: str = "",
        human_model: str = "",
        board_size: int = 19,
        komi: float = 6.5,
        rules: str = "japanese",
    ):
        self.executable = executable
        self.model = model
        self.config = config
        self.human_model = human_model
        self.board_size = board_size
        self.komi = komi
        self.rules = rules
        # 置き石（AB/AW）。KataGo Analysis API が期待する [["B","D4"], ...] の形式で保持。
        # set_game_info() で更新される。空ならクエリに含めない。
        self._initial_stones: list[list[str]] = []
        self._proc = None
        self._running = False
        self._lock = threading.Lock()        # 状態変数保護用
        self._stdin_lock = threading.Lock()  # stdin 書き込み専用
        self._query_id = 0

        # ── ポンダリング用 ──────────────────────────────────────
        # 現在ポンダリング中のクエリID（受信スレッドが照合に使う）
        self._ponder_qid: Optional[str] = None
        # 即時表示用プリフェッチID
        self._ponder_prefetch_qid: Optional[str] = None
        # ポンダリング結果のコールバック on_result(AnalysisResult)
        self._ponder_callback: Optional[Callable[[AnalysisResult], None]] = None

        # 後方互換（既存コードが参照している場合のため）
        self.on_analysis = None
        self.on_error = None

        # OpenCL チューニング進捗コールバック(起動時のみ使用想定)。
        # 呼び出し元(起動シーケンス)が
        # on_tuning_progress(phase_count, current, phase_total) 形式の
        # callable をセットすると、_drain_stderr がチューニングログを検出する
        # たびに呼び出す。
        #   phase_count: 何個目のフェーズか(1始まり、累積カウント)
        #   current    : そのフェーズ内での現在の進捗(0-indexed)
        #   phase_total: そのフェーズで testing される設定の総数
        # KataGo はチューニングを複数フェーズ(xGemm/winograd等)に分けて
        #順に行うため、phase_count は「全体で何フェーズあるか」ではなく
        # 「これまでに何個目のフェーズに入ったか」を示す(全体数は事前に
        # わからないため)。
        # 通常の解析時(起動完了後)はチューニングログが出ないため未使用のまま。
        self.on_tuning_progress: Optional[Callable[[int, int, int], None]] = None

        # 起動完了通知用イベント（_drain_stderr が "Started, ready to begin handling requests"
        # を検出したら set される）
        self._ready_event = threading.Event()

    # ── 起動・停止 ─────────────────────────────────────────────

    def start(self):
        args = [self.executable, "analysis", "-model", self.model]
        if self.config:
            args += ["-config", self.config]
        if self.human_model:
            args += ["-human-model", self.human_model]

        logger.info("Starting KataGo analysis: %s", " ".join(args))
        import sys as _sys
        _flags = subprocess.CREATE_NO_WINDOW if _sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_flags,
        )
        self._running = True

        # 受信専用スレッド（常時起動）
        threading.Thread(target=self._reader_thread, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

        # エンジン起動完了を待機
        # KataGo Analysis Engine は準備完了時に
        # "Started, ready to begin handling requests" を stderr に出力する。
        # 初回起動時は OpenCL 自動チューニング（モデルサイズごと）に
        # 数分〜10分かかるため、長めのタイムアウトを設定する。
        if not self._ready_event.wait(timeout=600.0):
            # タイムアウト: エンジンを停止し例外を投げる
            try:
                self._proc.kill()
            except Exception:
                pass
            self._running = False
            raise RuntimeError(
                "KataGo failed to become ready within 10 minutes. "
                "Check stderr logs for details."
            )
        logger.info("KataGo ready")

    def stop(self):
        self._running = False
        self._ponder_qid = None
        self._ponder_callback = None
        if self._proc:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            self._proc = None

    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    def set_game_info(
        self,
        komi: float,
        rules: str,
        initial_stones: list[tuple[str, str]],
    ):
        """対局情報（コミ・ルール・置き石）を更新する。

        次回以降のポンダリング/解析クエリから新しい値が使われる。
        現在進行中のクエリには影響しない（呼び出し側で必要なら stop_pondering
        してから呼ぶ）。

        Args:
            komi: コミ（例 6.5、置き碁なら 0.5）
            rules: KataGo の rules 文字列（"japanese"/"chinese"/"korean"/"aga"/
                   "tromp-taylor"/"new-zealand" など）
            initial_stones: [("B","D4"), ("W","Q16"), ...] 形式の置き石リスト
                            （SGF の AB/AW を GTP 座標化したもの）
        """
        self.komi = komi
        self.rules = rules
        # KataGo は内部で list[list[str]] を期待するので変換しておく
        self._initial_stones = [[c, m] for c, m in initial_stones]

    def play(self, color, coord):
        pass  # analysis モードでは不要

    def undo(self):
        pass  # analysis モードでは不要

    # ── ポンダリング API ────────────────────────────────────────

    def start_pondering(
        self,
        moves: list[tuple[str, str]],
        on_result: Callable[[AnalysisResult], None],
        max_visits: int = 5000,
        num_results: int = 5,
        include_ownership: bool = False,
    ):
        """
        指定局面のポンダリング（連続解析）を開始する。

        - 既存のポンダリングは自動的に新クエリに切り替わる。
        - on_result は reportDuringSearchEvery 秒ごとに呼ばれる。
        - 局面移動時は再度このメソッドを呼ぶだけでよい。

        max_visits: 解析の上限。デフォルト 5000。
                    KataGo 公式 gtp_example.cfg の maxVisitsPondering 推奨値で、
                    Lizzie 等の他 GUI でも一般的に 1000〜5000 の範囲が使われる。
                    上限到達後は探索が停止し CPU/GPU が解放される。
                    5000 visits でもアマ高段〜プロレベルの精度を保つ。
        include_ownership: 形勢判断(ownership)データを KataGo に要求するか。
                    デフォルト False。KataGo 公式ドキュメントによれば、True にすると
                    "メモリ使用量が2倍になり、パフォーマンスがやや低下する"。
                    形勢判断オーバーレイ表示中(_ownership_enabled=True)のときだけ
                    True を渡すこと。
        """
        if not self.is_running():
            return

        with self._lock:
            self._query_id += 1
            qid = "p%d" % self._query_id
            self._ponder_qid = qid
            self._ponder_callback = on_result

        moves_list = [[c, m] for c, m in moves]

        # 実行中の全クエリをキャンセルしてスロットを解放する
        with self._lock:
            self._query_id += 1
            term_id = "t%d" % self._query_id
        terminate_query = {"id": term_id, "action": "terminate_all"}
        self._send(terminate_query)

        # ── プリフェッチ: maxVisits=1 で即座に初回候補手を得る ──
        # 局面移動直後に数ms以内で候補手を表示するための先行クエリ
        with self._lock:
            self._query_id += 1
            pre_qid = "p%d" % self._query_id
            self._ponder_prefetch_qid = pre_qid  # プリフェッチID

        pre_query = {
            "id": pre_qid,
            "moves": moves_list,
            "rules": self.rules,
            "komi": self.komi,
            "boardXSize": self.board_size,
            "boardYSize": self.board_size,
            "analyzeTurns": [len(moves)],
            "maxVisits": 1,
            "includeOwnership": include_ownership,
        }
        if self._initial_stones:
            pre_query["initialStones"] = self._initial_stones
        self._send(pre_query)

        # ── 本クエリ: maxVisits 上限まで継続解析 ──
        with self._lock:
            self._query_id += 1
            qid = "p%d" % self._query_id
            self._ponder_qid = qid  # 本クエリIDに切り替え

        query = {
            "id": qid,
            "moves": moves_list,
            "rules": self.rules,
            "komi": self.komi,
            "boardXSize": self.board_size,
            "boardYSize": self.board_size,
            "analyzeTurns": [len(moves)],
            "maxVisits": max_visits,
            "reportDuringSearchEvery": 0.1,
            "includeOwnership": include_ownership,
        }
        if self._initial_stones:
            query["initialStones"] = self._initial_stones

        self._send(query)
        logger.info("Pondering started: qid=%s moves=%d ownership=%s",
                    qid, len(moves), include_ownership)

    def stop_pondering(self):
        """ポンダリングを停止する（KataGoのクエリもキャンセル）。"""
        with self._lock:
            self._ponder_qid = None
            self._ponder_prefetch_qid = None
            self._ponder_callback = None
        # KataGoに残っているクエリを全てキャンセル
        if self.is_running():
            with self._lock:
                self._query_id += 1
                term_id = "t%d" % self._query_id
            self._send({"id": term_id, "action": "terminate_all"})
        logger.info("Pondering stopped")

    def _send(self, query: dict):
        """クエリを KataGo の stdin に送る（スレッドセーフ）。"""
        line = json.dumps(query) + "\n"
        logger.warning("Send: %s", line[:200])
        with self._stdin_lock:
            self._proc.stdin.write(line.encode("utf-8"))
            self._proc.stdin.flush()

    def _reader_thread(self):
        """
        stdout を常時監視する専用スレッド。
        - ポンダリングIDと一致 → コールバックを呼ぶ
        """
        while self._running and self._proc:
            try:
                raw = self._proc.stdout.readline()
            except Exception:
                break
            if not raw:
                time.sleep(0.01)
                continue

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("JSON parse error: %s", line[:100])
                continue

            qid = data.get("id", "")
            # terminate アクションのレスポンスは無視する
            if data.get("action") == "terminate_all" or qid.startswith("t"):
                continue
            move_number = len(data.get("moves", []))  # 大抵含まれない
            result = self._parse_json(data, move_number)

            logger.debug("Recv qid=%s WR=%.1f%%", qid, result.root_win_rate * 100)

            # ポンダリング結果（本クエリ or プリフェッチ）
            with self._lock:
                ponder_qid = self._ponder_qid
                prefetch_qid = self._ponder_prefetch_qid
                callback = self._ponder_callback

            is_ponder = (qid == ponder_qid)
            is_prefetch = (qid == prefetch_qid)

            if (is_ponder or is_prefetch) and callback is not None:
                try:
                    callback(result)
                except Exception as e:
                    logger.warning("Ponder callback error: %s", e)
                continue

    def _parse_json(self, data: dict, move_number: int) -> AnalysisResult:
        result = AnalysisResult(move_number=move_number)
        result.is_during_search = data.get("isDuringSearch", False)
        moves_info = []

        turn_results = data.get("turnResults", [])
        if not turn_results:
            turn_results = [data]

        for turn in turn_results:
            root_info = turn.get("rootInfo", {})
            # Human SL Modelの場合は humanWinrate を優先使用
            result.root_win_rate = root_info.get("humanWinrate",
                                   root_info.get("winrate", 0.5))
            result.root_score_lead = root_info.get("humanScoreMean",
                                     root_info.get("scoreLead", 0.0))
            # ルート全体の visits（maxVisits の上限対象、UI表示用）
            result.root_visits = int(root_info.get("visits", 0))

            # ownership: KataGo は turn レベルに "ownership" キーで返す
            # 値は盤面左上から行優先の float リスト、+1=黒 / -1=白
            ownership_raw = turn.get("ownership", [])
            if ownership_raw:
                result.ownership = [float(v) for v in ownership_raw]

            for i, mv in enumerate(turn.get("moveInfos", [])):
                # Human SL Model の場合は humanPrior を prior として使用
                prior = mv.get("humanPrior", mv.get("prior", 0.0))
                mi = MoveInfo(
                    move=mv.get("move", "pass"),
                    visits=mv.get("visits", 0),
                    win_rate=mv.get("winrate", 0.0),
                    score_lead=mv.get("scoreLead", 0.0),
                    prior=prior,
                    order=mv.get("order", i),
                )
                moves_info.append(mi)

        moves_info.sort(key=lambda x: x.order)
        if moves_info:
            top_wr = moves_info[0].win_rate
            for mi in moves_info:
                mi._loss = max(0.0, top_wr - mi.win_rate)
            if result.root_win_rate == 0.5:
                result.root_win_rate = moves_info[0].win_rate

        result.best_moves = moves_info
        result.raw = json.dumps(data)
        return result

    # OpenCL チューニングログのパース用パターン(_drain_stderr で使用)。
    # 例:
    #   "Testing 69 different configs"      → そのフェーズの設定総数
    #   "Tuning 18/69 Calls/sec 7187.86 ..." → 現在の進捗(0-indexed)
    # KataGo はチューニングを複数フェーズ(xGemm/winograd等)に分けて行うため、
    # フェーズが変わるたびに新しい "Testing N different configs" が出力され、
    # 進捗はフェーズごとにリセットされる。フェーズ数自体は環境依存で事前に
    # わからないため、「現在のフェーズ内での何個目か」に加えて「これまでに
    # 何個目のフェーズに入ったか」を累積カウントして、ユーザーに「どこまで
    # 進んでいるか」の手がかりを示す(全体の合計フェーズ数は事前にわからない
    # ため、終了予測はできない)。
    _TUNING_TESTING_RE = re.compile(r"^Testing (\d+) different configs")
    _TUNING_PROGRESS_RE = re.compile(r"^Tuning (\d+)/(\d+)")

    def _drain_stderr(self):
        tuning_phase_total = 0  # 現在のチューニングフェーズの設定総数(未検出時は0)
        tuning_phase_count = 0  # これまでに開始したフェーズの累積数(1始まりで報告)
        for line in self._proc.stderr:
            line = line.decode("utf-8", errors="replace").rstrip()
            if line:
                logger.warning("KataGo stderr: %s", line)
                # 起動完了マーカーを検出（cpp/command/analysis.cpp で出力される文字列）
                if not self._ready_event.is_set() and \
                        "Started, ready to begin handling requests" in line:
                    self._ready_event.set()

                # ── OpenCL チューニング進捗の検出 ──
                # 起動完了前(初回起動・新環境)のみ意味を持つ。起動完了後の
                # 通常解析ではこのパターンに一致する行は出現しない想定。
                if self.on_tuning_progress is not None:
                    m_testing = self._TUNING_TESTING_RE.match(line)
                    if m_testing:
                        tuning_phase_total = int(m_testing.group(1))
                        tuning_phase_count += 1
                        continue
                    m_progress = self._TUNING_PROGRESS_RE.match(line)
                    if m_progress and tuning_phase_total > 0:
                        current = int(m_progress.group(1))
                        try:
                            self.on_tuning_progress(
                                tuning_phase_count, current, tuning_phase_total)
                        except Exception:
                            # コールバック側のエラーで起動シーケンス自体を
                            # 壊さないよう、ここで握りつぶす。
                            pass


# ── Mock（テスト用） ────────────────────────────────────────────────────────

class MockKataGoEngine(KataGoEngine):
    def __init__(self, board_size=19, komi=6.5):
        self.board_size = board_size
        self.komi = komi
        self.rules = "japanese"
        self._initial_stones: list[list[str]] = []
        self._running = True
        self.on_analysis = None
        self.on_error = None
        self._lock = threading.Lock()
        self._query_id = 0
        self._ponder_qid = None
        self._ponder_callback = None
        self._ponder_timer: Optional[threading.Timer] = None

    def start(self):
        logger.info("MockKataGoEngine started")

    def stop(self):
        self._running = False
        self.stop_pondering()

    def is_running(self):
        return self._running

    def play(self, color, coord): pass
    def undo(self): pass

    def start_pondering(self, moves, on_result, max_visits=5000, num_results=5,
                        include_ownership=False):
        self.stop_pondering()
        self._ponder_callback = on_result

        def _tick():
            if not self._ponder_callback:
                return
            result = self._mock_result(len(moves))
            try:
                self._ponder_callback(result)
            except Exception:
                pass
            # 1秒後に再度呼ぶ
            self._ponder_timer = threading.Timer(1.0, _tick)
            self._ponder_timer.daemon = True
            self._ponder_timer.start()

        self._ponder_timer = threading.Timer(0.5, _tick)
        self._ponder_timer.daemon = True
        self._ponder_timer.start()

    def stop_pondering(self):
        self._ponder_callback = None
        if self._ponder_timer:
            self._ponder_timer.cancel()
            self._ponder_timer = None

    def _mock_result(self, move_number):
        import random
        wr = max(0.05, min(0.95, 0.5 + random.gauss(0, 0.08)))
        cols = "ABCDEFGHJKLMNOPQRST"
        candidates = []
        for i in range(5):
            col = random.choice(cols)
            row = random.randint(1, self.board_size)
            candidates.append(MoveInfo(
                move="%s%d" % (col, row),
                visits=200 - i * 30,
                win_rate=wr - i * 0.03,
                score_lead=random.gauss(0, 3),
                prior=0.15 - i * 0.02,
                order=i,
            ))
        # モック ownership: 盤面左上から行優先、ランダムなグラデーション
        n = self.board_size
        ownership = []
        for r in range(n):
            for c in range(n):
                # 左上=黒寄り、右下=白寄りのダミーグラデーション
                v = ((n - 1 - r) + (n - 1 - c)) / (2 * (n - 1)) * 2 - 1
                v = max(-1.0, min(1.0, v + random.gauss(0, 0.15)))
                ownership.append(v)
        return AnalysisResult(
            move_number=move_number,
            turn_player="B" if move_number % 2 == 0 else "W",
            root_win_rate=wr,
            root_score_lead=random.gauss(0, 3),
            best_moves=candidates,
            ownership=ownership,
        )
