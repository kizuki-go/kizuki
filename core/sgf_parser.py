"""
SGF (Smart Game Format) parser and writer.
Implements the SGF FF[4] specification from scratch.
Reference: https://www.red-bean.com/sgf/sgf4.html
License: MIT
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SGFNode:
    """A single node in the SGF game tree."""
    properties: dict[str, list[str]] = field(default_factory=dict)
    children: list["SGFNode"] = field(default_factory=list)
    parent: Optional["SGFNode"] = field(default=None, repr=False)

    def get(self, key: str, default=None):
        vals = self.properties.get(key)
        return vals[0] if vals else default

    def get_all(self, key: str) -> list[str]:
        return self.properties.get(key, [])

    def set(self, key: str, values: list[str]):
        self.properties[key] = values

    @property
    def move(self) -> Optional[tuple[int, int]]:
        """Return (col, row) 0-based if this node contains a move, else None."""
        for key in ("B", "W"):
            val = self.get(key)
            if val is not None:
                return sgf_coord_to_pos(val)
        return None

    @property
    def move_color(self) -> Optional[str]:
        if "B" in self.properties:
            return "B"
        if "W" in self.properties:
            return "W"
        return None

    @property
    def comment(self) -> str:
        return self.get("C", "")

    @comment.setter
    def comment(self, text: str):
        self.properties["C"] = [text]

    @property
    def win_rate(self) -> Optional[float]:
        """Custom property WR stored as a float string by the analyzer."""
        val = self.get("WR")
        try:
            return float(val) if val is not None else None
        except ValueError:
            return None

    @win_rate.setter
    def win_rate(self, value: float):
        self.properties["WR"] = [f"{value:.4f}"]

    @property
    def score_lead(self) -> Optional[float]:
        """Custom property SL: estimated score lead (KataGo)."""
        val = self.get("SL")
        try:
            return float(val) if val is not None else None
        except ValueError:
            return None

    @score_lead.setter
    def score_lead(self, value: float):
        self.properties["SL"] = [f"{value:.2f}"]


@dataclass
class SGFGame:
    """Represents a complete SGF game tree."""
    root: SGFNode = field(default_factory=SGFNode)

    @property
    def board_size(self) -> int:
        try:
            return int(self.root.get("SZ", "19"))
        except ValueError:
            return 19

    @property
    def komi(self) -> float:
        try:
            return float(self.root.get("KM", "6.5"))
        except ValueError:
            return 6.5

    @property
    def player_black(self) -> str:
        return self.root.get("PB", "Black")

    @property
    def player_white(self) -> str:
        return self.root.get("PW", "White")

    @property
    def rules(self) -> str:
        """Return rule set string from RU property, e.g. 'Japanese', 'Chinese'."""
        return self.root.get("RU", "")

    def main_line(self) -> list[SGFNode]:
        """Return nodes along the main variation (always first child)."""
        nodes = []
        node = self.root
        while node:
            nodes.append(node)
            node = node.children[0] if node.children else None
        return nodes


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

LETTERS = "abcdefghijklmnopqrstuvwxyz"


def sgf_coord_to_pos(coord: str) -> Optional[tuple[int, int]]:
    """Convert SGF coordinate like 'pd' to (col, row) 0-based."""
    if not coord or coord == "tt" or coord == "":
        return None  # pass
    if len(coord) < 2:
        return None
    col = LETTERS.find(coord[0].lower())
    row = LETTERS.find(coord[1].lower())
    if col == -1 or row == -1:
        return None
    return col, row


def pos_to_sgf_coord(col: int, row: int) -> str:
    return LETTERS[col] + LETTERS[row]


def sgf_coord_to_human(coord: str, board_size: int = 19) -> str:
    """Convert SGF coordinate to human-readable like 'Q16'."""
    pos = sgf_coord_to_pos(coord)
    if pos is None:
        return "pass"
    col, row = pos
    col_letter = "ABCDEFGHJKLMNOPQRST"[col]  # skip I
    row_number = board_size - row
    return f"{col_letter}{row_number}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_PROP_RE = re.compile(r'([A-Z]+)\s*((?:\[(?:[^\]\\]|\\.)*\]\s*)+)')
_VAL_RE  = re.compile(r'\[([^\]\\]|\\.)*\]')


class SGFParser:
    """Recursive-descent SGF parser."""

    def __init__(self, text: str):
        self._text = text
        self._pos = 0

    def _skip_ws(self):
        while self._pos < len(self._text) and self._text[self._pos] in " \t\r\n":
            self._pos += 1

    def _expect(self, ch: str):
        self._skip_ws()
        if self._pos >= len(self._text) or self._text[self._pos] != ch:
            raise ValueError(
                f"Expected '{ch}' at position {self._pos}, "
                f"got '{self._text[self._pos:self._pos+5]}'"
            )
        self._pos += 1

    def _parse_value(self) -> str:
        self._expect("[")
        start = self._pos
        while self._pos < len(self._text):
            ch = self._text[self._pos]
            if ch == "\\":
                self._pos += 2
                continue
            if ch == "]":
                val = self._text[start:self._pos]
                self._pos += 1
                return val
            self._pos += 1
        raise ValueError("Unterminated SGF property value")

    def _parse_node(self) -> SGFNode:
        self._expect(";")
        node = SGFNode()
        self._skip_ws()
        while self._pos < len(self._text) and self._text[self._pos].isupper():
            # property identifier
            id_start = self._pos
            while self._pos < len(self._text) and self._text[self._pos].isupper():
                self._pos += 1
            prop_id = self._text[id_start:self._pos]
            values = []
            self._skip_ws()
            while self._pos < len(self._text) and self._text[self._pos] == "[":
                values.append(self._parse_value())
                self._skip_ws()
            node.properties[prop_id] = values
            self._skip_ws()
        return node

    def _parse_tree(self, parent: Optional[SGFNode] = None) -> SGFNode:
        self._expect("(")
        self._skip_ws()
        root_of_tree = None
        current = parent
        while self._pos < len(self._text):
            self._skip_ws()
            ch = self._text[self._pos] if self._pos < len(self._text) else ""
            if ch == ";":
                node = self._parse_node()
                node.parent = current
                if current is None:
                    root_of_tree = node
                else:
                    current.children.append(node)
                current = node
            elif ch == "(":
                # variation branch
                self._parse_tree(current)
            elif ch == ")":
                self._pos += 1
                break
            else:
                self._pos += 1  # skip unexpected chars
        return root_of_tree

    def parse(self) -> SGFGame:
        self._skip_ws()
        root = self._parse_tree()
        if root is None:
            raise ValueError("Empty or invalid SGF")
        return SGFGame(root=root)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _escape_sgf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("]", "\\]")


class SGFWriter:
    def write(self, game: SGFGame) -> str:
        return "(" + self._write_node(game.root) + ")"

    def _write_node(self, node: SGFNode) -> str:
        parts = [";"]
        for key, values in node.properties.items():
            parts.append(key)
            for v in values:
                parts.append(f"[{_escape_sgf(v)}]")
        if len(node.children) == 1:
            parts.append(self._write_node(node.children[0]))
        elif len(node.children) > 1:
            for child in node.children:
                parts.append("(" + self._write_node(child) + ")")
        return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_sgf(text: str) -> SGFGame:
    return SGFParser(text).parse()


def load_sgf(path: str) -> SGFGame:
    """SGFファイルを読み込む。
    エンコーディングは以下の順で決定する:
    1. バイト列から CA[] プロパティを正規表現で抽出
    2. CA が見つからない場合は chardet / utf-8 / cp932 / euc-jp の順でフォールバック
    """
    import re as _re

    with open(path, "rb") as f:
        raw = f.read()

    # CA プロパティをバイト列から直接検索（まだデコード前）
    ca_match = _re.search(rb'CA\[([^\]]+)\]', raw)
    if ca_match:
        ca_value = ca_match.group(1).decode("ascii", errors="ignore").strip()
        # SGF の CA 値を Python の codec 名にマッピング
        _CA_MAP = {
            "utf-8": "utf-8", "utf8": "utf-8",
            "shift_jis": "cp932", "shift-jis": "cp932",
            "sjis": "cp932", "s_jis": "cp932", "cp932": "cp932",
            "euc-jp": "euc-jp", "euc_jp": "euc-jp", "eucjp": "euc-jp",
            "iso-8859-1": "latin-1", "latin-1": "latin-1",
        }
        encoding = _CA_MAP.get(ca_value.lower(), ca_value)
        try:
            return parse_sgf(raw.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            pass  # CA値が不正な場合はフォールバックへ

    # CA なし: chardet → utf-8 → cp932 → euc-jp の順で試みる
    try:
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding") or "utf-8"
        return parse_sgf(raw.decode(enc))
    except ImportError:
        pass
    except UnicodeDecodeError:
        pass

    for enc in ("utf-8", "cp932", "euc-jp", "latin-1"):
        try:
            return parse_sgf(raw.decode(enc))
        except UnicodeDecodeError:
            continue

    # 最終手段: エラー文字を置換して強制デコード
    return parse_sgf(raw.decode("utf-8", errors="replace"))


def save_sgf(game: SGFGame, path: str):
    text = SGFWriter().write(game)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def dumps_sgf(game: SGFGame) -> str:
    return SGFWriter().write(game)
