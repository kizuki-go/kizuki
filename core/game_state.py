"""
GameState: 盤面状態管理・着手・分岐管理モジュール
License: MIT
"""
from __future__ import annotations
from typing import Optional
from core.sgf_parser import SGFNode, SGFGame, pos_to_sgf_coord, sgf_coord_to_pos

COLS = "ABCDEFGHJKLMNOPQRST"

class GameState:
    """
    現在の盤面状態と手順カーソルを管理するクラス。
    SGFのノードツリーを直接操作して着手・分岐を行う。
    """

    def __init__(self, game: SGFGame):
        self.game = game
        self.board_size = game.board_size
        # 現在のノード（カーソル位置）
        self._current_node: SGFNode = game.root
        # 盤面の石の状態
        self._stones: dict[tuple[int,int], str] = {}
        # 手番
        self._turn: str = "B"
        # アゲハマ
        self._black_captures: int = 0
        self._white_captures: int = 0
        # 現在ノードまでのパスを再構築
        self._rebuild_from_root()

    # ── カーソル移動 ────────────────────────────────────────────
    def go_to_node(self, node: SGFNode):
        """指定ノードまで盤面を再構築。"""
        self._current_node = node
        self._rebuild_from_root()

    def forward(self, variation_idx: int = 0) -> bool:
        """次の手へ（variation_idx: 分岐インデックス）。"""
        node = self._current_node
        if not node.children:
            return False
        variation_idx = min(variation_idx, len(node.children) - 1)
        next_node = node.children[variation_idx]
        self._apply_node(next_node)
        self._current_node = next_node
        return True

    def backward(self) -> bool:
        """1手戻る。"""
        if self._current_node.parent is None:
            return False
        self._current_node = self._current_node.parent
        self._rebuild_from_root()
        return True

    # ── 着手 ────────────────────────────────────────────────────
    def play(self, col: int, row: int) -> Optional[SGFNode]:
        """
        指定位置に現在の手番の石を打つ。
        - 既存の子ノードと一致する場合 → その分岐へ移動
        - 新しい手の場合 → 新分岐を作成
        - 着手不可の場合 → None を返す
        """
        if not self._is_valid_move(col, row):
            return None

        sgf_coord = pos_to_sgf_coord(col, row)
        color = self._turn

        # 既存の子ノードに同じ手があるか確認
        for child in self._current_node.children:
            if child.move_color == color and child.get(color) == sgf_coord:
                self._apply_node(child)
                self._current_node = child
                return child

        # 新しいノードを作成
        new_node = SGFNode(parent=self._current_node)
        new_node.properties[color] = [sgf_coord]
        self._current_node.children.append(new_node)
        self._apply_node(new_node)
        self._current_node = new_node
        return new_node

    def pass_move(self) -> SGFNode:
        """パス。"""
        color = self._turn
        new_node = SGFNode(parent=self._current_node)
        new_node.properties[color] = ["tt"]  # SGFのパス表記
        self._current_node.children.append(new_node)
        self._current_node = new_node
        self._turn = "W" if color == "B" else "B"
        return new_node

    # ── 分岐情報 ────────────────────────────────────────────────
    @property
    def current_node(self) -> SGFNode:
        return self._current_node

    @property
    def stones(self) -> dict[tuple[int,int], str]:
        return dict(self._stones)

    @property
    def turn(self) -> str:
        return self._turn

    @property
    def has_variations(self) -> bool:
        return len(self._current_node.children) > 1

    @property
    def variations(self) -> list[SGFNode]:
        return self._current_node.children

    def move_number(self) -> int:
        """現在の手数（ルートからのノード深さ）。"""
        n = 0; node = self._current_node
        while node.parent:
            n += 1; node = node.parent
        return n

    def path_to_root(self) -> list[SGFNode]:
        """ルートから現在ノードまでのパス。"""
        path = []
        node = self._current_node
        while node:
            path.append(node)
            node = node.parent
        return list(reversed(path))

    # ── 内部処理 ────────────────────────────────────────────────
    def _rebuild_from_root(self):
        """ルートから現在ノードまで盤面を再構築。"""
        self._stones = {}
        self._turn = "B"
        self._black_captures = 0
        self._white_captures = 0
        path = self.path_to_root()
        for node in path:
            self._apply_node(node)

    def _apply_node(self, node: SGFNode):
        """ノードの内容を盤面に適用。"""
        # 初期配置
        for color, key in [("B","AB"),("W","AW")]:
            for coord in node.get_all(key):
                pos = sgf_coord_to_pos(coord)
                if pos:
                    self._stones[pos] = color

        # 着手
        for color in ("B","W"):
            coord = node.get(color)
            if coord is not None and coord != "tt":
                pos = sgf_coord_to_pos(coord)
                if pos:
                    captured = self._calc_captures(pos, color)
                    for cp in captured:
                        del self._stones[cp]
                    if color == "B":
                        self._white_captures += len(captured)
                    else:
                        self._black_captures += len(captured)
                    self._stones[pos] = color
                self._turn = "W" if color == "B" else "B"
                return
        # 手番反転なし（ルートなど）

    def _is_valid_move(self, col: int, row: int) -> bool:
        pos = (col, row)
        if pos in self._stones:
            return False
        if not (0 <= col < self.board_size and 0 <= row < self.board_size):
            return False
        # 自殺手チェック（簡易）
        temp = dict(self._stones)
        temp[pos] = self._turn
        opp = "W" if self._turn == "B" else "B"
        # 相手の石を取れるか
        for cap in self._get_neighbors(col, row):
            nc, nr = cap
            if temp.get((nc,nr)) == opp:
                if not self._has_liberty(nc, nr, temp):
                    return True  # 相手を取れる
        # 自分に呼吸点があるか
        if self._has_liberty(col, row, temp):
            return True
        return False

    def _has_liberty(self, col, row, stones) -> bool:
        color = stones.get((col,row))
        if not color:
            return True
        visited = set()
        stack = [(col,row)]
        while stack:
            c,r = stack.pop()
            if (c,r) in visited: continue
            visited.add((c,r))
            for nc,nr in self._get_neighbors(c,r):
                if (nc,nr) not in stones:
                    return True
                if stones[(nc,nr)] == color and (nc,nr) not in visited:
                    stack.append((nc,nr))
        return False

    def _calc_captures(self, pos, color) -> list[tuple[int,int]]:
        opp = "W" if color == "B" else "B"
        temp = dict(self._stones)
        temp[pos] = color
        captured = []
        for nc,nr in self._get_neighbors(*pos):
            if temp.get((nc,nr)) == opp:
                if not self._has_liberty(nc, nr, temp):
                    group = self._get_group(nc, nr, temp)
                    captured.extend(group)
                    for gp in group:
                        del temp[gp]
        return captured

    def _get_group(self, col, row, stones) -> list[tuple[int,int]]:
        color = stones.get((col,row))
        visited = set(); stack = [(col,row)]
        while stack:
            c,r = stack.pop()
            if (c,r) in visited: continue
            visited.add((c,r))
            for nc,nr in self._get_neighbors(c,r):
                if stones.get((nc,nr)) == color and (nc,nr) not in visited:
                    stack.append((nc,nr))
        return list(visited)

    def _get_neighbors(self, col, row) -> list[tuple[int,int]]:
        result = []
        for dc,dr in [(-1,0),(1,0),(0,-1),(0,1)]:
            nc,nr = col+dc, row+dr
            if 0 <= nc < self.board_size and 0 <= nr < self.board_size:
                result.append((nc,nr))
        return result
