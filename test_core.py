"""
SGFパーサーの回帰テスト。
core/sgf_parser.py の座標変換・パース・書き出しをカバーする。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.sgf_parser import (
    parse_sgf, dumps_sgf, sgf_coord_to_pos, sgf_coord_to_human,
    pos_to_sgf_coord, SGFGame, SGFNode,
)


SIMPLE_GAME = "(;FF[4]GM[1]SZ[19]KM[6.5]PB[Black]PW[White];B[pd];W[dp];B[pp];W[dd])"


# ---------------------------------------------------------------------------
# パース基本
# ---------------------------------------------------------------------------

def test_parse_simple():
    game = parse_sgf(SIMPLE_GAME)
    assert game.board_size == 19
    assert game.komi == 6.5
    assert game.player_black == "Black"
    assert game.player_white == "White"

def test_main_line_length():
    game = parse_sgf(SIMPLE_GAME)
    assert len(game.main_line()) == 5  # root + 4 moves

def test_moves():
    game = parse_sgf(SIMPLE_GAME)
    line = game.main_line()
    assert line[1].move_color == "B"
    assert line[1].move == sgf_coord_to_pos("pd")
    assert line[2].move_color == "W"

def test_pass_move():
    game = parse_sgf("(;FF[4];B[tt];W[])")
    line = game.main_line()
    assert line[1].move is None  # B[tt] はパス
    assert line[2].move is None  # W[]  はパス

def test_variations():
    sgf = "(;FF[4];B[pd](;W[dp];B[pp])(;W[qp];B[od]))"
    game = parse_sgf(sgf)
    b_node = game.root.children[0]  # root -> B[pd]
    assert len(b_node.children) == 2


# ---------------------------------------------------------------------------
# コメント・プロパティ
# ---------------------------------------------------------------------------

def test_comment():
    game = parse_sgf("(;FF[4];B[pd]C[Good move])")
    assert game.root.children[0].comment == "Good move"

def test_comment_setter():
    node = SGFNode()
    node.comment = "Hello"
    assert node.comment == "Hello"

def test_win_rate_property():
    node = SGFNode()
    node.win_rate = 0.7543
    assert abs(node.win_rate - 0.7543) < 1e-4


# ---------------------------------------------------------------------------
# 座標変換
# ---------------------------------------------------------------------------

def test_sgf_coord_roundtrip():
    for coord in ("aa", "pd", "dp", "pp", "dd"):
        pos = sgf_coord_to_pos(coord)
        assert pos is not None
        assert pos_to_sgf_coord(*pos) == coord

def test_sgf_coord_to_human():
    assert sgf_coord_to_human("pd", 19) == "Q16"
    assert sgf_coord_to_human("dp", 19) == "D4"

def test_gtp_coords():
    assert sgf_coord_to_pos("aa") == (0, 0)
    assert sgf_coord_to_human("aa", 19) == "A19"


# ---------------------------------------------------------------------------
# 書き出し → 再パースの往復
# ---------------------------------------------------------------------------

def test_dumps_parse_roundtrip():
    game = parse_sgf(SIMPLE_GAME)
    text = dumps_sgf(game)
    game2 = parse_sgf(text)
    assert game2.board_size == game.board_size
    assert len(game2.main_line()) == len(game.main_line())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
