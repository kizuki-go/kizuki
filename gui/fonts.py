"""
gui/fonts.py — フォント生成ヘルパ。

このモジュールは theme.py と並ぶ基盤レイヤ。
依存: PyQt6 のみ。他の gui モジュールを import してはならない。

提供:
- F(size, bold): UIフォント
- Fmono(size, bold): 等幅フォント
- Font_XS..XXL / FontMono_XS..XXL: T-shirt サイズ別ヘルパ
"""
from __future__ import annotations
from PyQt6.QtGui import QFont, QFontDatabase


# フォント名定数（登録失敗時のフォールバック込み）
_UI_FONT       = "Yu Gothic UI"   # 游ゴシック（Windows標準）
_MONO_FONT     = "Yu Gothic UI"
_UI_FALLBACK   = "BIZ UDGothic"
_MONO_FALLBACK = "BIZ UDGothic"


def _font_name(preferred: str, fallback: str) -> str:
    """登録済みフォントファミリーに preferred があればそれを返す。"""
    families = QFontDatabase.families()
    return preferred if preferred in families else fallback


def F(size, bold=False):
    """UIフォント。size はピクセル単位（px）。"""
    f = QFont(_font_name(_UI_FONT, _UI_FALLBACK))
    f.setPixelSize(size)
    f.setBold(bold)
    return f


def Fmono(size, bold=False):
    """等幅フォント。size はピクセル単位（px）。"""
    f = QFont(_font_name(_MONO_FONT, _MONO_FALLBACK))
    f.setPixelSize(size)
    f.setBold(bold)
    return f


# ── Typography tokens (px) ────────────────────────────────────────────────
# T-shirt sizing で命名統一（Spacing トークンと同じ命名軸）。
# サイズ変更は1箇所で済む。役割の目安はコメント参照。
# スケール: XS / SM / MD / LG / XL / XXL = 12 / 14 / 16 / 18 / 24 / 28

def Font_XS(bold=False):
    """補助テキスト・凡例・座標・ヘルプバッジ (12px)"""
    return F(12, bold)

def Font_SM(bold=False):
    """メニュー・ステータスバー・補助UI (14px)"""
    return F(14, bold)

def Font_MD(bold=False):
    """通常本文・ラベル・カードタイトル(bold) (16px) ★標準"""
    return F(16, bold)

def Font_LG(bold=False):
    """中型見出し・準ディスプレイ (18px)"""
    return F(18, bold)

def Font_XL(bold=True):
    """大型ディスプレイ (24px) ※現在未使用、将来用"""
    return F(24, bold)

def Font_XXL(bold=True):
    """メトリック数値・特大ディスプレイ (28px)"""
    return F(28, bold)

def FontMono_XS(bold=False):
    """ツリーノード・小さい目盛 (12px)"""
    return Fmono(12, bold)

def FontMono_SM(bold=False):
    """グラフ目盛 (14px)"""
    return Fmono(14, bold)

def FontMono_MD(bold=False):
    """通常テキスト等幅 (16px) ※現在未使用"""
    return Fmono(16, bold)

def FontMono_LG(bold=True):
    """ScoreBoard 勝率数値 (18px)"""
    return Fmono(18, bold)

def FontMono_XL(bold=True):
    """大型数値ディスプレイ (24px) ※未使用、将来用"""
    return Fmono(24, bold)

def FontMono_XXL(bold=True):
    """特大数値ディスプレイ (28px) ※未使用、将来用"""
    return Fmono(28, bold)
