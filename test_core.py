"""
Tests for go_analyzer core modules.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.sgf_parser import (
    parse_sgf, dumps_sgf, sgf_coord_to_pos, sgf_coord_to_human,
    pos_to_sgf_coord, SGFGame, SGFNode,
)
from core.katago_engine import MockKataGoEngine
from core.analyzer import GameAnalyzer, BlunderInfo


# ---------------------------------------------------------------------------
# SGF parser tests
# ---------------------------------------------------------------------------

SIMPLE_GAME = "(;FF[4]GM[1]SZ[19]KM[6.5]PB[Black]PW[White];B[pd];W[dp];B[pp];W[dd])"

def test_parse_simple():
    game = parse_sgf(SIMPLE_GAME)
    assert game.board_size == 19
    assert game.komi == 6.5
    assert game.player_black == "Black"
    assert game.player_white == "White"

def test_main_line_length():
    game = parse_sgf(SIMPLE_GAME)
    line = game.main_line()
    assert len(line) == 5  # root + 4 moves

def test_moves():
    game = parse_sgf(SIMPLE_GAME)
    line = game.main_line()
    assert line[1].move_color == "B"
    assert line[1].move == sgf_coord_to_pos("pd")
    assert line[2].move_color == "W"

def test_sgf_coord_roundtrip():
    for coord in ("aa", "pd", "dp", "pp", "dd"):
        pos = sgf_coord_to_pos(coord)
        assert pos is not None
        back = pos_to_sgf_coord(*pos)
        assert back == coord

def test_sgf_coord_to_human():
    assert sgf_coord_to_human("pd", 19) == "Q16"
    assert sgf_coord_to_human("dp", 19) == "D4"

def test_pass_move():
    game = parse_sgf("(;FF[4];B[tt];W[])")
    line = game.main_line()
    # both are passes
    assert line[1].move is None
    assert line[2].move is None

def test_comment():
    game = parse_sgf("(;B[pd]C[Good move])")
    assert game.root.children[0].comment == "Good move"

def test_comment_setter():
    node = SGFNode()
    node.comment = "Hello"
    assert node.comment == "Hello"

def test_win_rate_property():
    node = SGFNode()
    node.win_rate = 0.7543
    assert abs(node.win_rate - 0.7543) < 1e-4

def test_dumps_parse_roundtrip():
    game = parse_sgf(SIMPLE_GAME)
    text = dumps_sgf(game)
    game2 = parse_sgf(text)
    assert game2.board_size == game.board_size
    assert len(game2.main_line()) == len(game.main_line())

def test_variations():
    sgf = "(;FF[4];B[pd](;W[dp];B[pp])(;W[qp];B[od]))"
    game = parse_sgf(sgf)
    root = game.root
    # root -> B[pd] node
    b_node = root.children[0]
    assert len(b_node.children) == 2


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def test_gtp_coords():
    # Column A=0, row 19 -> "A19" in GTP
    pos = sgf_coord_to_pos("aa")
    assert pos == (0, 0)
    human = sgf_coord_to_human("aa", 19)
    assert human == "A19"


# ---------------------------------------------------------------------------
# Mock engine tests
# ---------------------------------------------------------------------------

def test_mock_engine_starts():
    eng = MockKataGoEngine()
    eng.start()
    assert eng.is_running()
    eng.stop()
    assert not eng.is_running()

def test_mock_engine_analysis():
    eng = MockKataGoEngine()
    eng.start()
    result = eng.request_analysis([], max_visits=50, num_results=3)
    assert result is not None
    assert 0.0 <= result.root_win_rate <= 1.0
    assert len(result.best_moves) == 3
    eng.stop()

def test_mock_engine_moves():
    eng = MockKataGoEngine()
    eng.start()
    eng.play("B", "Q16")
    eng.play("W", "D4")
    result = eng.request_analysis([("B","Q16"),("W","D4")], max_visits=10)
    assert result is not None
    assert result.move_number == 2
    eng.stop()


# ---------------------------------------------------------------------------
# Analyzer tests
# ---------------------------------------------------------------------------

def test_blunder_category():
    for loss, expected in [
        (0.01, "best"),
        (0.03, "good"),
        (0.07, "inaccuracy"),
        (0.15, "mistake"),
        (0.25, "blunder"),
    ]:
        b = BlunderInfo(
            win_rate_before=0.6,
            win_rate_after=0.6 - loss,
            win_rate_loss=loss,
            best_move="Q16",
            best_move_wr=0.6,
            score_lead_before=0,
            score_lead_after=0,
        )
        assert b.category == expected, f"loss={loss} expected {expected} got {b.category}"

def test_blunder_labels():
    b = BlunderInfo(0.6, 0.35, 0.25, "Q16", 0.6, 0, 0)
    assert b.label_jp == "悪手"
    assert b.color.startswith("#")

def test_full_game_analysis():
    eng = MockKataGoEngine()
    eng.start()
    game = parse_sgf(SIMPLE_GAME)
    analyzer = GameAnalyzer(eng, game)
    results = analyzer.analyze(max_visits=20, num_candidates=3)
    assert len(results) == 4  # 4 moves in SIMPLE_GAME
    for ma in results:
        assert ma.blunder is not None
        assert ma.blunder.category in ("best","good","inaccuracy","mistake","blunder")
    eng.stop()

def test_analyzer_summary():
    eng = MockKataGoEngine()
    eng.start()
    game = parse_sgf(SIMPLE_GAME)
    analyzer = GameAnalyzer(eng, game)
    analyzer.analyze(max_visits=20)
    summary = analyzer.summary()
    assert "B" in summary and "W" in summary
    total_b = sum(summary["B"].values())
    total_w = sum(summary["W"].values())
    assert total_b + total_w == 4
    eng.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
