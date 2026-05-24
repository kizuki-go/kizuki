"""
gui/icons.py — SVG アイコン生成と QSS 生成ヘルパ。

依存: gui.theme (T() 経由でテーマ色を参照), PyQt6
このモジュールから上層 (gui.infra, gui.widgets.*, gui.menus 等) を
import してはならない。

提供:
- SVG 文字列生成関数 (_rounded_check_svg / _rank_check_svg_bold / _chevron_down_svg)
- 対応する SVG ファイルパス生成関数 (_get_check_mark_path 等)
- QSS 生成関数 (menu_qss / rank_list_qss / statusbar_qss / icon_button_qss)
- ボタンへのホバーアイコン差し替え (install_icon_hover_color_swap)
- 汎用 SVG → QIcon 変換 (make_icon)
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import QByteArray

from gui.theme import T


# ── チェックマーク SVG ─────────────────────────────────────────────────────
def _rounded_check_svg(color: str) -> str:
    """丸みのあるチェックマーク(✓)を SVG 文字列で返す。
    stroke-linecap/linejoin を round にして線端と折れ角を丸める。
    16x16 ビューポート、現在のテーマ TEXT 色で描画。
    """
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="12" height="12">'
        f'  <path d="M 3 8.5 L 6.5 12 L 13 4.5" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


# キャッシュ: テーマ色ごとに SVG ファイルを書き出し、QSS から URL 参照する。
# QSS の image: 属性はファイルパスまたは Qt リソースを要求するため、
# メモリ上の pixmap を直接渡せない。色をキーにしてファイルを使い回す。
_check_svg_cache: dict[str, str] = {}

def _get_check_mark_path(color: str) -> str:
    """指定色の丸みチェックマーク SVG ファイルパスを返す。
    初回はテンポラリディレクトリに書き出し、2回目以降はキャッシュを返す。
    キャッシュキーは SVG 全文のハッシュ(色変更・SVGテンプレ変更で自動再生成)。
    """
    if color in _check_svg_cache:
        return _check_svg_cache[color]
    import tempfile, os, hashlib
    svg = _rounded_check_svg(color)
    # SVG 全文のハッシュをキーにすることで、テンプレ変更時にも新ファイルになる
    h = hashlib.md5(svg.encode()).hexdigest()[:10]
    tmp_dir = os.path.join(tempfile.gettempdir(), "kizuki_assets")
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, f"check_{h}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    # QSS の url() は forward slash の方が安全(Windows のバックスラッシュ問題回避)
    safe_path = path.replace("\\", "/")
    _check_svg_cache[color] = safe_path
    return safe_path


# ── 棋力サブメニュー専用の太線チェックマーク ─────────────────────────────
# 棋力サブメニューでは選択中項目が表示時にホバー状態(背景 PANEL2 #2d2d2d)
# になりやすく、白いストロークのコントラストが下がって視覚的に薄く見える。
# そのため棋力リスト用は stroke-width を 2 → 2.4 に増やし、純白中央部の
# 比率を上げてアンチエイリアスのフェード端の影響を抑える。
# ルール/コミ等の他サブメニューは _rounded_check_svg(stroke 2) を維持する。
def _rank_check_svg_bold(color: str) -> str:
    """棋力サブメニュー専用のチェックマーク SVG (stroke-width 2.4)。
    形状・サイズ(viewBox/width/height)・線端は _rounded_check_svg と同一。"""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="12" height="12">'
        f'  <path d="M 3 8.5 L 6.5 12 L 13 4.5" '
        f'fill="none" stroke="{color}" stroke-width="2.4" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


# 棋力専用 SVG のファイルキャッシュ (色 → パス)。_check_svg_cache とは
# 別管理 (テンプレが違うので衝突回避)。
_rank_check_svg_cache: dict[str, str] = {}

def _get_rank_check_mark_path(color: str) -> str:
    """棋力サブメニュー用の太線チェックマーク SVG ファイルパスを返す。
    QPixmap(path) で読み込み、QSS image:url() と同じ QImageReader 経由の
    シャープなラスタライズ結果を得る。
    """
    if color in _rank_check_svg_cache:
        return _rank_check_svg_cache[color]
    import tempfile, os, hashlib
    svg = _rank_check_svg_bold(color)
    h = hashlib.md5(svg.encode()).hexdigest()[:10]
    tmp_dir = os.path.join(tempfile.gettempdir(), "kizuki_assets")
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, f"rank_check_{h}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    safe_path = path.replace("\\", "/")
    _rank_check_svg_cache[color] = safe_path
    return safe_path


# ── ▼ 矢印 (filled triangle) SVG ─────────────────────────────────────
# QComboBox の drop-down 矢印を自前で用意するために使う。
# QSS で QComboBox::down-arrow に image:url() で参照する想定。
# 形状は Qt の QMenu::right-arrow と同じ「塗りつぶし三角」を 90° 回転した
# 下向きバージョン。シェブロン(線)ではなく塗りつぶしポリゴンにすることで、
# アプリ内の他のメニュー矢印と視覚的に統一される。
def _chevron_down_svg(color: str) -> str:
    """下向きの塗りつぶし三角 (▼) を SVG 文字列で返す。
    16x16 ビューポート内をほぼ満たす大きさの三角(QSS で表示サイズを
    指定)。塗りつぶしポリゴンにすることで、アプリ内の他のメニュー矢印
    (filled ▶) と視覚的に統一される。"""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="12" height="12">'
        f'  <polygon points="2,4 14,4 8,13" fill="{color}" />'
        f'</svg>'
    )


# chevron-down SVG のファイルキャッシュ (色 → パス)
_chevron_down_svg_cache: dict[str, str] = {}

def _get_chevron_down_path(color: str) -> str:
    """指定色の chevron-down SVG ファイルパスを返す。
    QSS の image:url() で参照する用途。色ごとにキャッシュ。
    """
    if color in _chevron_down_svg_cache:
        return _chevron_down_svg_cache[color]
    import tempfile, os, hashlib
    svg = _chevron_down_svg(color)
    h = hashlib.md5(svg.encode()).hexdigest()[:10]
    tmp_dir = os.path.join(tempfile.gettempdir(), "kizuki_assets")
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, f"chevron_down_{h}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
    safe_path = path.replace("\\", "/")
    _chevron_down_svg_cache[color] = safe_path
    return safe_path


# ── Menu / QListWidget / StatusBar QSS ─────────────────────────────────────
# メニュー QSS（PyQt6 の QMenuBar 内部マージンを含む共通スタイル）。
# フォントは Font_MD と同じ 12px、padding は 4pxグリッド準拠。
# 全メニュー（メインメニューバー・右クリックメニュー）で共通使用。
def menu_qss() -> str:
    t = T()
    # 丸みチェックマーク画像のパス(現在テーマの TEXT 色で生成・キャッシュ)
    check_url = _get_check_mark_path(t.TEXT.name())
    # メニュー項目のレイアウト:
    #   通常メニュー (サブメニューあり): padding-right=24px で▶アイコン領域を確保
    #   リーフメニュー (objectName="leaf_menu"): padding-right=8px に縮める
    #   テキスト開始位置 = padding-left = 12px
    return (
        f"QMenuBar{{background:{t.TOOLBAR.name()};color:{t.TEXT2.name()};"
        f"font-size:14px;border-bottom:1px solid {t.BORDER.name()};}}"
        f"QMenuBar::item{{padding:4px 8px;}}"
        f"QMenuBar::item:selected{{background:{t.PANEL2.name()};color:{t.TEXT.name()};}}"
        f"QMenu{{background:{t.PANEL.name()};color:{t.TEXT.name()};"
        f"font-size:14px;border:1px solid {t.BORDER.name()};border-radius:12px;padding:4px;}}"
        # 通常メニュー: サブメニュー▶のスペースを padding-right で確保
        f"QMenu::item{{padding:8px 24px 8px 12px;border-radius:4px;}}"
        # リーフメニュー (objectName=leaf_menu): ▶がないので右余白を 8px に縮める
        f"QMenu#leaf_menu::item{{padding:8px 8px 8px 12px;border-radius:4px;}}"
        f"QMenu::item:selected{{background:{t.PANEL2.name()};}}"
        f"QMenu::item:disabled{{color:{t.TEXT2.name()};}}"
        f"QMenu::indicator{{width:12px;height:12px;margin-left:8px;}}"
        f"QMenu::indicator:checked{{image:url('{check_url}');}}"
        f"QMenu::right-arrow{{margin-left:4px;margin-right:4px;}}"
        f"QMenu::separator{{height:1px;background:{t.BORDER2.name()};margin:4px 8px;}}"
    )


def rank_list_qss() -> str:
    """棋力メニュー内に埋め込む QListWidget 用 QSS。

    描画(各行のチェックマーク・テキスト・ホバー背景)は
    _RankItemDelegate が完全に独自実装するため、ここでは:
      ・リスト全体の背景色・フォント
      ・スクロールバー
    のみ定義する。 QListWidget::item の padding/background も delegate が
    担当するため QSS では指定しない (指定すると delegate.option.rect の
    計算とずれるため)。

    QMenu のスクロール矢印 (QMenu::scroller) は WA_TranslucentBackground +
    FramelessWindowHint 環境で描画されないため、棋力メニューだけは
    QListWidget でリスト表示してスクロールバー経由でスクロールさせる。

    スクロールバーの見た目は分岐ツリー (_tree_scroll) など他の
    QScrollArea と統一: width:6px / margin:0 / handle 色 T().BORDER /
    border-radius:3px。
    """
    t = T()
    return (
        # リスト本体: メニュー本体と同じ背景・テキスト色、外枠なし。
        # padding は 0 (上下左右ともゼロ) にしてスクロールバーを右端ピッタリ
        # まで寄せる。上下の余白は親 QMenu の padding:4 が外側に確保される
        # ため問題なし。左端の余白も詰まるが、delegate の CHECK_LEFT で
        # ✓ 位置を制御しているのでバランスは取れている。
        f"QListWidget{{background:{t.PANEL.name()};color:{t.TEXT.name()};"
        f"font-size:14px;border:none;outline:none;padding:0;}}"
        # スクロールバー: 分岐ツリー用と同じ細身デザインに統一。
        f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
        f"QScrollBar::handle:vertical{{background:{t.BORDER.name()};"
        f"border-radius:3px;min-height:20px;}}"
        f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0px;}}"
    )


# StatusBar QSS(補助情報なので Font_XS 相当の 12px)
def statusbar_qss() -> str:
    t = T()
    return (
        f"QStatusBar{{background:{t.PANEL.name()};color:{t.TEXT2.name()};"
        f"font-size:12px;border-top:1px solid {t.BORDER.name()};}}"
    )


# ── アイコンボタン用 共通 QSS ヘルパー ─────────────────────────────────────
# アプリ全体のアイコンボタン(ナビバーのコメント/戻る/進む、ダイアログの ×、
# コメント欄の × など)で使う統一スタイル。設計ルール:
#
#   ルール A: ホバー時の背景色は「親背景に対して 1 段明るい色」
#       - メイン領域 (背景 = PANEL or BG) → ホバー = PANEL2
#       - タイトルバー領域 (背景 = PANEL2) → ホバー = BORDER2
#         (in_titlebar=True で切り替え)
#   ルール B: ホバーでアイコンの色は変えない (背景のみ変化)
#   ルール C: アイコンの通常色は文脈で決める
#       - 主要操作 (タイトルバー等): TEXT
#       - 補助/閉じる (ナビ、× など): TEXT2 (デフォルト)
#
# 個別の細かい違い (角丸サイズ、フォントサイズ、padding) は引数で吸収する。
def icon_button_qss(
    *,
    in_titlebar: bool = False,
    icon_color: str = "TEXT2",
    border_radius: int = 4,
    font_size: Optional[int] = None,
    padding: Optional[str] = None,
    hover_color_swap: bool = True,
) -> str:
    """アイコンボタン用の統一 QSS を生成する。
    透明背景 + ホバーで PANEL2 (タイトルバー内なら BORDER2) を敷く。

    hover_color_swap=True (デフォルト) なら、ホバー時に文字色を TEXT2 → TEXT
    に切り替える(テキスト × などのテキストアイコンに有効)。QIcon を使う
    アイコンボタンの場合、文字色は無関係なので install_icon_hover_color_swap
    で別途アイコン差し替えを行う。
    """
    t = T()
    hover_bg = t.BORDER2.name() if in_titlebar else t.PANEL2.name()
    color = t.TEXT.name() if icon_color == "TEXT" else t.TEXT2.name()
    hover_color = t.TEXT.name() if hover_color_swap else color
    extra = ""
    if font_size is not None:
        extra += f"font-size:{font_size}px;"
    if padding is not None:
        extra += f"padding:{padding};"
    return (
        f"QPushButton{{background:transparent;border:none;"
        f"border-radius:{border_radius}px;color:{color};{extra}}}"
        f"QPushButton:hover{{background:{hover_bg};color:{hover_color};}}"
    )


def install_icon_hover_color_swap(btn, normal_icon, hover_icon):
    """ボタンに enter/leave フックを設定し、ホバー時にアイコンを差し替える。
    QIcon を使うアイコンボタンで、ホバー時に色変化させたい時に使う。
    既存のフックがあれば上書きされる。
    """
    btn._normal_icon = normal_icon
    btn._hover_icon = hover_icon
    btn.setIcon(normal_icon)

    def _on_enter(ev, b=btn):
        b.setIcon(b._hover_icon)
        # 既定 enterEvent も呼ぶ(他の処理が壊れないよう)
        type(b).enterEvent(b, ev) if hasattr(type(b), "enterEvent") else None

    def _on_leave(ev, b=btn):
        b.setIcon(b._normal_icon)
        type(b).leaveEvent(b, ev) if hasattr(type(b), "leaveEvent") else None

    btn.enterEvent = _on_enter
    btn.leaveEvent = _on_leave


# ── SVG Pictogram utilities ────────────────────────────────────────────────
def make_icon(svg: str, size: int = 16, color: str = "#ffffff", opacity: float = 1.0) -> QIcon:
    """SVG文字列からQIconを生成。{{color}}プレースホルダーを差し込む。
    opacity: 0.0〜1.0 のアルファ値(QPainter.setOpacity 経由でラスタライズ時に適用)。
    """
    data = svg.replace("{{color}}", color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(data))
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    if opacity < 1.0:
        p.setOpacity(opacity)
    renderer.render(p)
    p.end()
    return QIcon(pix)
