"""
gui/infra.py — プロファイラ、棋力閾値、Blunder評価、サウンド、半透明ウィジェット基盤。

このモジュールは基盤レイヤ(theme/fonts/icons と並ぶ下層)。
依存: PyQt6, core.analyzer, gui.theme のみ。
上層 (gui.widgets.*, gui.dialogs, gui.menus, gui._mixins.*) を import してはならない。

提供:
- _Profiler / _profile / _profile_method: 軽量パフォーマンスプロファイラ
- _get_thresholds / get_current_thresholds / set_player_rank: 棋力別の悪手閾値
- BlunderInfo: core.analyzer.BlunderInfo を拡張(現在の棋力閾値に基づく判定)
- eval_badge_tuple: 評価カテゴリ → (main, text) 色タプル
- SoundPlayer: 着手音/取り音 (QSoundEffect)
- TranslucentWidget: paintEvent で半透明背景を描画する QWidget
"""
from __future__ import annotations
import logging
import os as _os
import threading as _threading
import time
from contextlib import contextmanager as _contextmanager
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPainterPath
from PyQt6.QtWidgets import QWidget

try:
    from PyQt6.QtMultimedia import QSoundEffect
    from PyQt6.QtCore import QUrl
    _SOUND_AVAILABLE = True
except ImportError:
    _SOUND_AVAILABLE = False

from core.analyzer import BlunderInfo as _BlunderInfoBase
from gui.theme import T, R_MD, EVAL_COLORS

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# パフォーマンス計測機構
# ══════════════════════════════════════════════════════════════════════════════
# 環境変数 KIZUKI_PROFILE=1 で有効化。無効時は完全に no-op で本番性能に影響なし。
#
# 使い方:
#   with _profile("BranchTree.paint"):
#       ...時間を計測したい処理...
#
# 1秒ごとにバックグラウンドスレッドが集計をログファイル(gui/profile.log)に
# 追記する。無効時は _Profiler.enabled が False になるので、_profile() は
# 直ちに no-op コンテキストマネージャを返す(time.perf_counter も呼ばない)。


class _Profiler:
    """軽量プロファイラ。タグごとに呼出回数と累積時間を集計し、定期的にログ出力する。"""

    _ENV_VAR = "KIZUKI_PROFILE"
    _FLUSH_INTERVAL_SEC = 1.0

    def __init__(self):
        self.enabled: bool = bool(_os.environ.get(self._ENV_VAR, "").strip())
        # tag -> (count, total_ns)
        self._stats: dict[str, list] = {}
        self._lock = _threading.Lock()
        self._flusher_thread: Optional[_threading.Thread] = None
        self._stop_event = _threading.Event()
        self._log_path: Optional[Path] = None
        if self.enabled:
            self._init_logging()
            self._start_flusher()

    def _init_logging(self):
        """ログファイルパスを決定してヘッダ行を書き込む。"""
        try:
            log_path = Path(__file__).parent / "profile.log"
            self._log_path = log_path
            with open(log_path, "w", encoding="utf-8") as f:
                from datetime import datetime
                f.write(f"# Kizuki performance profile log\n")
                f.write(f"# Started: {datetime.now().isoformat()}\n")
                f.write(f"# Format: [elapsed_sec] tag: count=N total_ms=X.X avg_ms=Y.Y\n\n")
            logger.warning("Profiler enabled: log = %s", log_path)
        except Exception as e:
            logger.warning("Profiler log init failed: %s", e)
            self.enabled = False

    def _start_flusher(self):
        """1秒ごとに集計をログ出力するバックグラウンドスレッド。"""
        self._start_time = time.perf_counter()

        def _run():
            while not self._stop_event.is_set():
                self._stop_event.wait(self._FLUSH_INTERVAL_SEC)
                if self._stop_event.is_set():
                    break
                self._flush()

        t = _threading.Thread(target=_run, daemon=True, name="KizukiProfileFlusher")
        t.start()
        self._flusher_thread = t

    def _flush(self):
        """現在の集計をログファイルに追記し、カウンタをリセットする。"""
        if not self.enabled or self._log_path is None:
            return
        with self._lock:
            if not self._stats:
                return
            # スナップショット取得 + リセット
            snapshot = self._stats
            self._stats = {}
        elapsed = time.perf_counter() - self._start_time
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{elapsed:7.2f}s]\n")
                # count 降順で並べる(高頻度なものを上に)
                items = sorted(snapshot.items(), key=lambda kv: -kv[1][0])
                for tag, (count, total_ns) in items:
                    total_ms = total_ns / 1_000_000.0
                    avg_ms = total_ms / count if count else 0.0
                    f.write(f"  {tag:<32} count={count:5d}  total={total_ms:7.2f}ms  avg={avg_ms:6.3f}ms\n")
                f.write("\n")
        except Exception as e:
            logger.warning("Profiler flush failed: %s", e)

    def record(self, tag: str, ns_elapsed: int):
        """1回の計測結果を記録する(tag ごとに count と total_ns を加算)。"""
        with self._lock:
            entry = self._stats.get(tag)
            if entry is None:
                self._stats[tag] = [1, ns_elapsed]
            else:
                entry[0] += 1
                entry[1] += ns_elapsed

    def stop(self):
        """フラッシャースレッドを停止する(アプリ終了時)。最後の集計も出す。"""
        if not self.enabled:
            return
        self._stop_event.set()
        if self._flusher_thread is not None:
            self._flusher_thread.join(timeout=2.0)
        self._flush()


# モジュールグローバルなプロファイラインスタンス
_profiler = _Profiler()


@_contextmanager
def _profile(tag: str):
    """計測用コンテキストマネージャ。無効時は no-op、有効時のみ時間計測する。

    使用例:
        def paintEvent(self, ev):
            with _profile("BranchTree.paint"):
                ...描画処理...
    """
    if not _profiler.enabled:
        yield
        return
    t0 = time.perf_counter_ns()
    try:
        yield
    finally:
        _profiler.record(tag, time.perf_counter_ns() - t0)


def _profile_method(tag: str):
    """メソッド計測用デコレータ。プロファイラ無効時はラップせず元関数を返すので
    オーバーヘッドゼロ。

    使用例:
        @_profile_method("Board.paint")
        def paintEvent(self, ev):
            ...
    """
    def _decorator(fn):
        if not _profiler.enabled:
            # 無効時はラップせず元関数をそのまま返す → オーバーヘッドゼロ
            return fn

        def _wrapper(*args, **kwargs):
            t0 = time.perf_counter_ns()
            try:
                return fn(*args, **kwargs)
            finally:
                _profiler.record(tag, time.perf_counter_ns() - t0)
        # __name__ や __doc__ を保持(デバッグしやすさのため)
        _wrapper.__name__ = fn.__name__
        _wrapper.__doc__ = fn.__doc__
        return _wrapper
    return _decorator


# ══════════════════════════════════════════════════════════════════════════════


# ── 棋力別閾値テーブル ────────────────────────────────────────────────────────
# ランク値: 負数=級（-30〜-1）、正数=段（1〜9）
# 各エントリ: (good, inaccuracy, mistake, blunder) の目差損失閾値

_RANK_THRESHOLDS: list[tuple[int, int, tuple]] = [
    # (rank_min, rank_max, (good, inaccuracy, mistake, blunder))
    (-30, -20, (4.5, 12.0, 26.0, 42.0)),   # 30〜20級
    (-19, -15, (3.5,  9.5, 21.0, 35.0)),   # 19〜15級
    (-14, -10, (2.8,  7.5, 17.0, 29.0)),   # 14〜10級
    ( -9,  -6, (2.2,  6.0, 13.5, 24.0)),   #  9〜 6級
    ( -5,  -3, (1.7,  4.5, 10.5, 19.5)),   #  5〜 3級
    ( -2,  -1, (1.2,  3.5,  8.0, 15.5)),   #  2〜 1級
    (  1,   2, (0.8,  2.5,  6.0, 12.0)),   # 初段〜2段
    (  3,   4, (0.5,  1.8,  4.5,  9.0)),   #  3〜 4段
    (  5,   6, (0.4,  1.2,  3.0,  6.5)),   #  5〜 6段
    (  7,   9, (0.3,  0.8,  2.0,  4.5)),   #  7〜 9段
]

def _get_thresholds(rank: int) -> tuple:
    """ランク値から閾値タプルを返す。範囲外は最近傍グループを使用。"""
    for r_min, r_max, thresholds in _RANK_THRESHOLDS:
        if r_min <= rank <= r_max:
            return thresholds
    # 範囲外: 最弱 or 最強グループを返す
    if rank < _RANK_THRESHOLDS[0][0]:
        return _RANK_THRESHOLDS[0][2]
    return _RANK_THRESHOLDS[-1][2]

# グローバル棋力設定（MainWindow から更新される）
# 負数=級（-30〜-1）、正数=段（1〜9）
_current_player_rank: int = -5   # デフォルト: 5級


def get_current_thresholds() -> tuple:
    return _get_thresholds(_current_player_rank)


def set_player_rank(rank: int):
    global _current_player_rank
    _current_player_rank = rank


class BlunderInfo(_BlunderInfoBase):
    """
    BlunderInfo を拡張し、打った手（played_move）と KataGo の1番候補手が
    一致する場合を無条件に「最善」と判定するロジックに変更。
    カテゴリ判定閾値はグローバル棋力設定に基づく。
    """
    played_move: str = ""   # 実際に打った手（GTP座標）

    @property
    def category(self) -> str:
        # 打った手が1番候補手と一致 → 無条件に最善
        if self.played_move and self.best_move:
            if self.played_move.upper() == self.best_move.upper():
                return "best"
        # 棋力別閾値で判定
        good, inaccuracy, mistake, blunder = get_current_thresholds()
        loss = self.score_lead_loss
        if loss < good:
            return "good"
        if loss < inaccuracy:
            return "inaccuracy"
        if loss < mistake:
            return "mistake"
        return "blunder"


def eval_badge_tuple(category) -> tuple:
    """評価カテゴリから BADGE_COLORS 用のタプル (main, text) を返す。
    BadgeWidget や MoveInfoCard で従来 dict 値として持っていたものを EVAL_COLORS
    から導出する。"""
    c = EVAL_COLORS.get(category, EVAL_COLORS[None])
    return (c["main"], c["text"])


# ── Sound player ─────────────────────────────────────────────────────────────
class SoundPlayer:
    """碁石の着手音・取り音を再生する。WAVファイルが存在しない場合は無音。"""
    def __init__(self):
        self._place   = None
        self._capture = None
        self._muted   = False
        self._volume  = 0.6  # 0.0 〜 1.0
        if not _SOUND_AVAILABLE:
            return
        base = Path(__file__).parent.parent / "sounds"
        self._place   = self._load(base / "stone_place.wav")
        self._capture = self._load(base / "stone_capture.wav")

    def _load(self, path: Path):
        if not _SOUND_AVAILABLE:
            return None
        se = QSoundEffect()
        se.setSource(QUrl.fromLocalFile(str(path)))
        se.setVolume(self._volume)
        return se

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool):
        self._muted = value
        self._apply_volume()

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, value))
        self._apply_volume()

    def _apply_volume(self):
        v = 0.0 if self._muted else self._volume
        for se in (self._place, self._capture):
            if se:
                se.setVolume(v)

    def play_place(self):
        if self._place and not self._muted and self._volume > 0.0:
            self._place.play()

    def play_capture(self):
        if self._capture and not self._muted and self._volume > 0.0:
            self._capture.play()


# ── 半透明オーバーレイウィジェット ──────────────────────────────────────────
class TranslucentWidget(QWidget):
    """paintEvent で半透明背景を描画する子ウィジェット。
    setWindowOpacity は子ウィジェットには効かないため、
    QPainter で直接 rgba 背景を描画することで半透明を実現する。"""
    def __init__(self, parent=None, alpha=220):
        super().__init__(parent)
        self._alpha = alpha  # 0=透明 255=不透明
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def set_alpha(self, alpha: int):
        self._alpha = alpha
        self.update()

    def paintEvent(self, ev):
        from PyQt6.QtGui import QPainter, QColor, QPainterPath
        from PyQt6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = T().PANEL
        bg = QColor(c.red(), c.green(), c.blue(), self._alpha)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), R_MD, R_MD)
        p.fillPath(path, bg)
        # ボーダー
        border = QColor(T().BORDER)
        border.setAlpha(self._alpha)
        p.setPen(border)
        p.drawPath(path)
        p.end()
