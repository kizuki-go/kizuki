"""
gui/theme.py — Theme シングルトンと UI 定数。

このモジュールは他の全 gui モジュールから参照される基盤レイヤ。
依存: PyQt6 のみ。他の gui モジュールを import してはならない。

提供:
- EVAL_COLORS / LIGHT_BLUNDER_COLORS: 評価カテゴリの色辞書(可変)
- Theme: ダーク/ライトの配色クラス
- _theme: モジュールレベルシングルトン
- T(): 上記シングルトンを返すヘルパ
- COLS: 列ラベル
- SP_* / R_* / PAD_* / SPACING_ROW: 間隔・角丸・パディング定数
"""
from __future__ import annotations
from PyQt6.QtGui import QColor


# ── 評価カテゴリの色定義 (全UIで共通) ──────────────────────────────────────
# アプリ全体で参照される「最善/良手/緩手/疑問/悪手/未解析」のカラーパレット。
# 各カテゴリは以下3つのキーを持つ:
#   main          : ピル背景・グラフマーカー・碁盤バッジ等の主用途。
#                   ダーク時の白文字との WCAG AA 4.5:1 を確保する暗めの彩度色。
#   text          : バッジ上のテキスト色 (通常は白)
#   text_dark_mode: ダークテーマでテキストとして使う色。PANEL=#252525 上で
#                   WCAG AA 4.5:1 を確保する明るめの同色相色。
#                   全カテゴリで「ピル背景(暗) + テキスト(明)」を同色相のペアにしてトーン統一。
# ライトテーマでのテキスト色は Theme.BLUNDER (テーマ依存) を使うため、
# このテーブルはダークモード前提の値のみ持つ。
EVAL_COLORS: dict = {
    "best":       {"main": "#0a7ac5", "text": "#ffffff", "text_dark_mode": "#0d99f7"},
    "good":       {"main": "#20874c", "text": "#ffffff", "text_dark_mode": "#28a85e"},
    "inaccuracy": {"main": "#c3840e", "text": "#ffffff", "text_dark_mode": "#d99f11"},
    "mistake":    {"main": "#d35d17", "text": "#ffffff", "text_dark_mode": "#e7712b"},
    "blunder":    {"main": "#d33939", "text": "#ffffff", "text_dark_mode": "#f35555"},
    None:         {"main": "#6a7888", "text": "#ffffff", "text_dark_mode": "#808d9c"},
}


# ── ライトテーマ用の悪手判定色 ───────────────────────────────────────
# EVAL_COLORS とは別管理(ダーク前提値とは異なるライト最適化値)。
# Theme.__init__ で参照される。ColorAdjustmentDialog で書き換え可能。
LIGHT_BLUNDER_COLORS: dict = {
    "best":       "#0a76b9",
    "good":       "#20874c",
    "inaccuracy": "#af760c",
    "mistake":    "#c55616",
    "blunder":    "#c13434",
    None:         "#6a7888",
}


# ── Theme system ───────────────────────────────────────────────────────────

class Theme:
    """ダーク/ライトモードのカラートークンを管理するクラス。
    T() ヘルパー経由でどこからでもアクセスできる。
    """

    DARK    = "dark"
    LIGHT   = "light"

    def __init__(self, mode: str = "dark"):
        self._mode = mode
        self._apply(mode)

    def _apply(self, mode: str):
        self._mode = mode
        if mode == self.DARK:
            # VS Code / Notion / Claude Desktop と同様の「やや明るい黒」系。
            # 純黒(#000)+純白(#fff)の組み合わせは目に厳しいため、
            # 背景は #1a1a1a〜#2d2d2d、テキストは #e6e6e6 で柔らかく。
            self.BG      = QColor("#1a1a1a")
            self.PANEL   = QColor("#252525")
            self.PANEL2  = QColor("#2d2d2d")
            self.TOOLBAR = QColor("#1f1f1f")
            self.BORDER  = QColor("#3d3d3d")
            self.BORDER2 = QColor("#333333")
            self.TEXT    = QColor("#ffffff")
            self.TEXT2   = QColor("#b0b0b0")
            # アイコン用の少し薄いテキスト色(▶/コメントアイコン/コメントバッジ等で使用)。
            # ダークでは TEXT(#ffffff)と同じで現状の見た目を維持。
            # ライトでは TEXT(#333333)より少し薄い #444444 を使う。
            self.ICON_DIM = QColor("#ffffff")
            self.ACCENT  = QColor("#909090")
            self.GREEN   = QColor("#28a85e")
            self.YELLOW  = QColor("#c49010")
            self.ORANGE  = QColor("#d45e18")
            self.RED     = QColor("#c22424")
            # 評価バッジ色 (グラフマーカー、碁盤碁石バッジ等で使用)。
            # ダークテーマでは EVAL_COLORS の main をそのまま参照。
            self.BLUNDER = {
                k: QColor(v["main"])
                for k, v in EVAL_COLORS.items()
                if k is not None
            }
            # グラフ描画色
            self.GRAPH_BLACK = QColor(12, 12, 12, 180)    # 黒番エリア塗り (#0c0c0c, α=180, STONE_BLACK と同 RGB)
            self.GRAPH_WHITE = QColor(255, 255, 255, 160) # 白番エリア塗り (#ffffff, α=160)
            self.GRAPH_LINE  = QColor("#909090")          # 折れ線
            self.GRAPH_BG    = "#1a1a1a"                  # pyqtgraph 背景(BGに揃える)
            self.GRAPH_AXIS  = "#555555"                  # 軸・グリッド色
            # 碁盤座標ラベル色
            self.COORD_LABEL = QColor("#888888")
            # 碁石・小バー・分岐ツリーノード等の「黒系UI要素」共通色 (不透明)。
            # 背景 #1a1a1a に対して L* 差 -8 程度のコントラスト(より黒寄り)。
            self.STONE_BLACK = QColor("#0c0c0c")
            # 白系UI要素 (ScoreBoard 白石塗り、分岐ツリー白ノード塗り、目差ラベル白側背景等)。
            # 両モードで共通の値を使用。
            self.STONE_WHITE = QColor("#e0ddd8")
            # 碁石ボーダー (黒石・白石それぞれ、ScoreBoard / _StoneIcon / BranchTree 共通)。
            self.STONE_BORDER_BLACK = QColor("#8a8a8a")
            self.STONE_BORDER_WHITE = QColor("#c0bbb5")
            # 未指定石塗り(_StoneIcon の "—" 等)。
            # BadgeWidget の「不明」バッジ (EVAL_COLORS[None]["main"]) と同じ
            # #6a7888 で統一し、手番不明状態の見た目を一貫させる。
            self.STONE_NEUTRAL = QColor("#6a7888")
            # 勝率バー白側塗り。STONE_WHITE よりも明るく、黒側との対比を強調する
            # (ScoreBoard の左右バー、目差ラベル等の「白系UI」と分けて管理)。
            self.WINRATE_WHITE = QColor("#ffffff")
            # 目差ラベル黒側のボーダー色。背景は GRAPH_BLACK と同期で動的なため、
            # ボーダーが背景に溶け込まない明るさが必要。
            # ダーク: 合成背景 ≒ #131313 に対し #555555 で十分なコントラスト。
            self.SCORE_LABEL_BORDER_BLACK = QColor("#555555")
            # 目差ラベル白側のボーダー色。背景は GRAPH_WHITE (#ffffff) と同期。
            # ダーク: 背景 #ffffff に対し #909090(明暗差 ≒ 112)で視認。
            # 黒側 SCORE_LABEL_BORDER_BLACK と対称な設計。
            self.SCORE_LABEL_BORDER_WHITE = QColor("#909090")
        else:  # LIGHT
            self.BG      = QColor("#fafafa")
            self.PANEL   = QColor("#fdfdfd")
            self.PANEL2  = QColor("#f0f0f0")
            self.TOOLBAR = QColor("#f0f0f0")
            self.BORDER  = QColor("#d0d0d0")
            self.BORDER2 = QColor("#e0e0e0")
            self.TEXT    = QColor("#333333")
            self.TEXT2   = QColor("#555555")
            # アイコン用の少し薄いテキスト色(▶/コメントアイコン/コメントバッジ等で使用)。
            # ダークでは TEXT(#ffffff)と同じ。
            # ライトでは TEXT(#333333)より少し薄い #444444 を使う。
            self.ICON_DIM = QColor("#444444")
            self.ACCENT  = QColor("#6a6a6a")
            self.GREEN   = QColor("#1a8a4a")
            self.YELLOW  = QColor("#a07800")
            self.ORANGE  = QColor("#b84d10")
            self.RED     = QColor("#aa1a1a")
            self.BLUNDER = {
                k: QColor(v) for k, v in LIGHT_BLUNDER_COLORS.items()
            }
            # グラフ描画色
            self.GRAPH_BLACK = QColor(42, 42, 42, 230)     # 黒番エリア塗り (#2a2a2a, α=230, 勝率バー黒と同色)
            self.GRAPH_WHITE = QColor(216, 216, 216, 220)  # 白番エリア塗り (#d8d8d8相当)
            self.GRAPH_LINE  = QColor("#6a6a6a")            # 折れ線
            self.GRAPH_BG    = "#fafafa"                   # pyqtgraph 背景
            self.GRAPH_AXIS  = "#888888"                   # 軸・グリッド色
            # 碁盤座標ラベル色
            self.COORD_LABEL = QColor("#666666")
            # 碁石・小バー・分岐ツリーノード等の「黒系UI要素」共通色 (不透明)。
            # GRAPH_BLACK (#444444 α=210) よりやや濃く、碁石らしさを強める。
            self.STONE_BLACK = QColor("#2a2a2a")
            # 白系UI要素 (ScoreBoard 白石塗り、分岐ツリー白ノード塗り、目差ラベル白側背景等)。
            # 両モードで共通の値を使用。
            self.STONE_WHITE = QColor("#e0ddd8")
            # 碁石ボーダー (黒石・白石それぞれ、ScoreBoard / _StoneIcon / BranchTree 共通)。
            self.STONE_BORDER_BLACK = QColor("#8a8a8a")
            self.STONE_BORDER_WHITE = QColor("#c0bbb5")
            # 未指定石塗り(_StoneIcon の "—" 等)。
            # BadgeWidget の「不明」バッジ (LIGHT_BLUNDER_COLORS[None]) と同じ
            # #6a7888 で統一し、手番不明状態の見た目を一貫させる。
            self.STONE_NEUTRAL = QColor("#6a7888")
            # 勝率バー白側塗り。STONE_WHITE よりも明るく、黒側との対比を強調する
            # (ScoreBoard の左右バー、目差ラベル等の「白系UI」と分けて管理)。
            self.WINRATE_WHITE = QColor("#d8d8d8")
            # 目差ラベル黒側のボーダー色。ダーク側コメント参照。
            # ライト: 合成背景 ≒ #3f3f3f に対し #555555 だとほぼ溶ける。
            # 1段明るい #8a8a8a (= STONE_BORDER_BLACK と同値) で視認性を確保。
            self.SCORE_LABEL_BORDER_BLACK = QColor("#8a8a8a")
            # 目差ラベル白側のボーダー色。ダーク側コメント参照。
            # ライト: 背景 GRAPH_WHITE (#d8d8d8) に対し #a0a0a0(明暗差 ≒ 56)で控えめに視認。
            self.SCORE_LABEL_BORDER_WHITE = QColor("#a0a0a0")

    def set_mode(self, mode: str):
        self._apply(mode)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_dark(self) -> bool:
        return self._mode == self.DARK

    def blunder_color(self, category: str) -> QColor:
        return self.BLUNDER.get(category, self.TEXT2)

    @property
    def board_base_color(self) -> QColor:
        """碁盤の地色。None の場合は木目テクスチャを使う。"""
        return None

    @property
    def board_line_color(self) -> QColor:
        """グリッド線色（木目に馴染むかなり濃い茶色）。"""
        return QColor(35, 25, 10)

    @property
    def board_edge_color(self) -> QColor:
        """碁盤外枠色（線より濃い茶色）。"""
        return QColor(80, 50, 10)

    @property
    def board_star_color(self) -> QColor:
        """星点色（線と同じかなり濃い茶色）。"""
        return QColor(35, 25, 10)

    @property
    def board_coord_color(self) -> QColor:
        """座標文字色（かなり濃いグレー）。"""
        return QColor("#222222")


# グローバルテーマインスタンス（起動時に QSettings から復元）
# 初期値はダーク。QSettings に保存値があれば __init__ で上書きされる。
_theme = Theme("dark")


def T() -> Theme:
    """どこからでもテーマトークンを参照するヘルパー。
    例: T().BG, T().PANEL.name(), T().BLUNDER["best"]
    """
    return _theme


COLS = "ABCDEFGHJKLMNOPQRST"


# ── Spacing tokens (4px grid) ─────────────────────────────────────────────
SP_XS = 4    # アイコン横、最小余白
SP_SM = 8    # 標準内側パディング
SP_MD = 12   # セクション間
SP_LG = 16   # 強い区切り
SP_XL = 24   # ペイン間

# 角丸トークン（4の倍数ベース・SP と同じグリッド）
R_XS   = 4    # バー内部・最小要素
R_SM   = 8    # ボタン・ポップオーバー・小コンポーネント
R_MD   = 12   # カード類（標準）
R_LG   = 16   # フローティングパネル
R_PILL = 99   # バッジ・ナビバー（完全ピル型）

# 標準パディング (left, top, right, bottom)
PAD_CARD  = (SP_MD, SP_MD, SP_MD, SP_MD)        # (12, 12, 12, 12)  カード/パネル標準
PAD_TIGHT = (SP_SM, SP_XS, SP_SM, SP_XS)        # (8, 4, 8, 4)  コンパクト
PAD_NAV   = (SP_SM, 0,     SP_SM, 0)            # (8, 0, 8, 0)  ナビ用横並び（現在未使用、将来用）
PAD_ICON  = (SP_XS, SP_XS, SP_XS, SP_XS)        # (4, 4, 4, 4)  ToggleBar内

# レイアウトのスペーシング
SPACING_ROW = SP_MD   # 横並び要素 (12)
