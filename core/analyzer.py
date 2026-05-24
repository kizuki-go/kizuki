"""
Move analysis data classes: per-move analysis with blunder classification.
License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .katago_engine import AnalysisResult, MoveInfo


# ---------------------------------------------------------------------------
# Blunder classification
# ---------------------------------------------------------------------------

@dataclass
class BlunderInfo:
    """Classification of a move's quality."""
    win_rate_before: float  # win-rate before the move (current player's POV)
    win_rate_after: float   # win-rate after the move (current player's POV)
    win_rate_loss: float    # positive = bad move (kept for reference)
    best_move: str          # best move according to KataGo
    best_move_wr: float
    score_lead_before: float
    score_lead_after: float
    score_lead_loss: float = 0.0  # 目差の損失（正 = 悪い手）

    @property
    def category(self) -> str:
        """目差ベースで判定: best, good, inaccuracy, mistake, blunder."""
        loss = self.score_lead_loss
        if loss < 0.5:
            return "best"
        if loss < 2.0:
            return "good"
        if loss < 5.0:
            return "inaccuracy"
        if loss < 10.0:
            return "mistake"
        return "blunder"

    @property
    def label_jp(self) -> str:
        mapping = {
            "best":       "最善",
            "good":       "良手",
            "inaccuracy": "緩手",
            "mistake":    "疑問手",
            "blunder":    "悪手",
        }
        return mapping.get(self.category, "")

    @property
    def color(self) -> str:
        """Qt color name for UI display."""
        mapping = {
            "best":       "#2ab566",  # 緑
            "good":       "#85c23a",  # 黄緑
            "inaccuracy": "#c9950c",  # 黄
            "mistake":    "#e07840",  # 橙
            "blunder":    "#e8453f",  # 赤
        }
        return mapping.get(self.category, "#888")


@dataclass
class MoveAnalysis:
    """Full analysis data for a single move."""
    move_number: int
    color: str          # "B" or "W"
    coord: str          # SGF coordinate
    human_coord: str    # human-readable like "Q16"
    win_rate: float     # win-rate for the player who just moved (before this move)
    score_lead: float
    blunder: Optional[BlunderInfo] = None
    best_moves: list[MoveInfo] = field(default_factory=list)
    analysis_result: Optional[AnalysisResult] = None
    sgf_comment: str = ""
