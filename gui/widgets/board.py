"""
gui/widgets/board.py — 碁盤ウィジェットと中央配置コンテナ。

依存: gui.theme, gui.fonts, gui.infra, PyQt6, pathlib.
このモジュールは他の widget から参照されることがあるが、自身は上層
(gui.dialogs, gui.menus, gui._mixins.*, gui.main_window) を import しない。

提供:
- BoardWidget: 碁盤本体(描画、クリック、アニメ、ownership 等)
- BoardContainer: 常に正方形・中央配置するコンテナ
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPointF, QRectF, QVariantAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QPainterPathStroker,
    QPen, QBrush, QColor, QImage, QPixmap,
    QRadialGradient, QTransform,
)

from gui.theme import T, R_MD, COLS
from gui.fonts import F, Fmono
from gui.infra import _profile, _profile_method, BlunderInfo


# ── Board widget ────────────────────────────────────────────────────────────
class BoardWidget(QWidget):
    stone_clicked = pyqtSignal(int, int)

    # オーバーレイのフェードアニメ設定
    _OVERLAY_FADE_DURATION_MS = 250
    # 盤面反転(時計回り180度回転 + 一時縮小)の所要時間
    _FLIP_ANIM_DURATION_MS = 1500
    # 反転アニメ中の座標ラベル制御:
    # - フェードアウト: 回転開始と並行(回転全体の _FLIP_COORD_FADEOUT_RATIO)
    #   0.12 = 開始 12%(=180ms@1500ms)で alpha を 1→0
    # - 完全非表示: フェードアウト完了から回転完了まで(視覚ノイズ除去)
    # - フェードイン: 回転完了後に独立アニメで実施(_FLIP_COORD_FADEIN_DURATION_MS)
    #   回転中は完全非表示、回転が止まってから座標が戻る挙動。
    _FLIP_COORD_FADEOUT_RATIO = 0.12
    _FLIP_COORD_FADEIN_DURATION_MS = 180
    # 棋譜内容差し替え時(新規作成・SGFオープン・貼り付け)のクロスフェード時間。
    # 旧盤面 pixmap が α=1→0、新通常描画が α=0→1 で重ね合わされる。
    _CONTENT_CHANGE_FADE_MS = 600

    def __init__(self):
        super().__init__()
        self.board_size = 19
        self.stones: dict = {}
        self.candidates: list = []
        self.last_move: Optional[tuple] = None
        self.blunder: Optional[BlunderInfo] = None
        self.show_hints = True
        self.show_coords = False
        self.show_ownership = False        # 形勢判断オーバーレイ ON/OFF
        # 手順番号オーバーレイ:
        #   move_numbers が空 → 描画なし
        #   非空              → その辞書通りに描画する（起点や1始まりかは呼び出し側で計算）
        self.show_badges = True            # 評価バッジ ON/OFF
        # 最後の手のマーキング ON/OFF。最後に打たれた石の中心に枠線のみの
        # 円(リング)を描画する。 黒石上は白線、白石上は黒線で描画して
        # 視認性を確保する。デフォルト OFF。
        self.show_last_move_mark = False
        # AI解析トグルの状態(MainWindow と同期)。hints のフェード判定で使用:
        # 解析 ON 中に着手で candidates が一瞬空になる経路では α を 1.0 のまま
        # 保持し、データ到着時に瞬時表示する(着手のたびにフェードしないため)。
        self.ai_enabled = True
        self.ownership: list[float] = []   # KataGo ownership データ
        self.move_numbers: dict = {}       # {(col,row): 表示する番号}
        self.next_moves = []
        self.turn = "B"                    # 現在の手番（"B" or "W"）
        # 盤面反転（180度回転）の有無。flipped プロパティ経由で操作される。
        # setter が時計回り180度回転 + 一時縮小のアニメを起動する。
        self._flipped = False
        # 反転アニメの状態管理:
        #   _flip_anim_progress: 0.0 → 1.0 (現在の進捗)。1.0 = 静止状態。
        #   _flip_anim_from / _flip_anim_to: アニメ開始/終了時の flipped 値
        #   アニメ中は _flip_anim_from の状態で _xy() を計算し、回転 transform で
        #   描画。t=1.0 到達時に _flipped = _flip_anim_to に切り替わる。
        self._flip_anim_progress: float = 1.0
        self._flip_anim_from: bool = False
        self._flip_anim_to: bool = False
        self._flip_anim = None  # QVariantAnimation の参照保持
        # アニメ中に毎フレーム同じ盤本体を描画するのは重い(_stones の RadialGradient
        # 等で 60fps 維持が難しくカクつく)ため、開始時にキャプチャした QPixmap を
        # 回転 transform で描画する方式にする。
        # 物理的に正しい光の挙動を再現するため、構成を 2層に分ける:
        # - 石なし盤面 pixmap : 木目・格子・星・候補手・形勢・手数等。回転対象。
        # - 黒石/白石 pixmap  : 立体感(影・グラデ・ハイライト)込みの石1個分。
        #                       回転しない(光源は世界座標で固定なので、石の絵は
        #                       盤の回転と独立して位置だけ移動する)。
        # 各フレームでは「盤面 pixmap を回転描画 → 各石位置(回転後の世界座標)に
        # 石 pixmap を drawPixmap」する。これで光源固定の物理的に正しい見た目が
        # 軽量に実現できる(grdient 計算は事前1回のみ、フレームあたり drawPixmap
        # は ~200回程度で GPU 加速の対象)。
        self._flip_anim_pixmap_board = None    # 石なし盤面(回転対象)
        self._flip_anim_pixmap_stone_b = None  # 黒石1個分(回転しない)
        self._flip_anim_pixmap_stone_w = None  # 白石1個分(回転しない)
        # 石 pixmap のメタ情報(各石を描画する際の位置調整用)
        self._flip_anim_stone_rad = 0.0        # 石半径(画面ピクセル)
        self._flip_anim_stone_pad = 0          # pixmap 内マージン(影が枠外に出ないよう確保)
        # 反転アニメ完了後の座標フェードイン用:
        # 回転完了 (_flip_anim_progress = 1.0) になった時点で起動され、
        # _flip_coord_fadein_progress を 0.0 → 1.0 へ補間する。回転中は
        # 強制的に 0.0 に保持し、フェードイン完了後は 1.0(通常の座標表示)。
        self._flip_coord_fadein_progress: float = 1.0
        self._flip_coord_fadein_anim = None
        # 棋譜内容差し替え時(新規作成・SGFオープン・貼り付け)のクロスフェード:
        # - _content_change_pixmap_old: 切り替え直前の見た目をキャプチャした pixmap
        # - _content_change_progress: 0.0(切替直後) → 1.0(完了)
        #   paintEvent で 旧 pixmap (α=1-t) を下地に描画してから、新しい通常描画を
        #   α=t で上に重ねる。完了時に pixmap を破棄。
        self._content_change_pixmap_old = None
        self._content_change_progress: float = 1.0
        self._content_change_anim = None
        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # ── オーバーレイのフェード状態 ──
        # 現在値 _overlay_alpha[key] を paintEvent で setOpacity に乗せる。
        # paintEvent 冒頭で _sync_overlay_alpha_targets() を呼び、各 key の
        # 現在の有効状態(show_ownership 等の bool)に基づき目標値を計算、
        # 必要に応じてアニメを起動する(呼び出し側コードは無改変で済む)。
        # キー:
        #   "ownership" — show_ownership に追従
        #   "hints"     — show_hints     に追従
        #   "move_num"  — bool(move_numbers) に追従
        self._overlay_alpha = {
            "ownership": 1.0 if self.show_ownership else 0.0,
            "hints":     1.0 if self.show_hints     else 0.0,
            "move_num":  1.0 if self.move_numbers   else 0.0,
            "badges":    1.0 if self.show_badges    else 0.0,
            # "coords" は他キーと違い「進捗値」として扱う:
            # - 文字描画の透明度 alpha
            # - 加えて _cell() / _margin() のマージン補間にも使用
            #   → 0.0(OFF時のレイアウト) ↔ 1.0(ON時のレイアウト) を線形補間
            # 盤面サイズと座標文字が同期して滑らかに変化する。
            "coords":    1.0 if self.show_coords    else 0.0,
        }
        # アニメ管理: key → (anim, start_alpha, end_alpha)
        self._overlay_anims: dict = {}
        # フェードアウト中の描画継続用スナップショット
        # 呼び出し側で self.ownership = [] や self.move_numbers = {} と
        # クリアされても、フェードアウト完了までこちらを使って描画する。
        self._overlay_snap_ownership: Optional[list] = None
        self._overlay_snap_move_numbers: Optional[dict] = None
        self._overlay_snap_candidates: Optional[list] = None

    def set_position(self, stones, candidates, last_move=None, blunder=None, next_moves=None, ownership=None, turn="B"):
        self.stones = stones
        self.candidates = candidates
        self.last_move = last_move
        self.blunder = blunder
        self.next_moves = next_moves or []  # [(col, row, color), ...]
        if ownership is not None:
            self.ownership = ownership
        self.turn = turn                    # 現在の手番を更新
        self.update()

    # geometry
    def _coord_progress(self) -> float:
        """座標表示の進捗 [0.0, 1.0]。OFF=0.0, ON=1.0、アニメ中はその間。
        _cell/_margin/_bg のマージン計算と _coords の文字 alpha に使う。"""
        return float(self._overlay_alpha.get("coords", 1.0 if self.show_coords else 0.0))

    def _cell(self):
        s = min(self.width(), self.height())
        # 座標OFF: 全マージン c*1.1（改善前と同一）→ divisor = board_size + 1.2
        # 座標ON : 全マージン c*1.75（板内c*1.2 + 外c*0.55）→ divisor = board_size + 2.5
        # 進捗 t∈[0,1] で線形補間して滑らかに切替える。
        t = self._coord_progress()
        divisor = self.board_size + 1.2 + 1.3 * t   # 1.2(OFF) → 2.5(ON)
        return s / divisor

    def _margin(self):
        # 全マージン（キャンバス端〜第1グリッド線）
        # OFF: c*1.1, ON: c*1.75 を進捗 t で補間
        c = self._cell()
        t = self._coord_progress()
        return c * (1.1 + 0.65 * t)
    def _orig(self):
        m = self._margin()
        c = self._cell()
        total = m + (self.board_size - 1) * c + m  # 碁盤全体の描画幅
        ox = (self.width()  - total) / 2 + m
        oy = (self.height() - total) / 2 + m
        return ox, oy
    def _xy_grid(self, col, row):
        """グリッド線・座標ラベル・星のための座標変換（反転無視）。
        盤面反転中でも座標ラベルや星は固定位置に出すため、こちらを使う。"""
        ox, oy = self._orig()
        c = self._cell()
        return ox + col * c, oy + row * c

    @property
    def flipped(self) -> bool:
        """盤面反転状態(公的なフラグ)。論理的な最終状態を返す。
        マウス入力(_flip_pos)・SGF 出力等の論理判定はこの値を使う。"""
        return self._flipped

    @flipped.setter
    def flipped(self, v: bool):
        """flipped 切替時に、180度回転 + 一時縮小のアニメを起動する。
        回転方向は反転 ON への切替なら時計回り、OFF への切替なら反時計回り
        (詳細は _start_flip_anim の docstring 参照)。
        既存呼び出し側コード(self._board.flipped = v)を変更せずに済むよう、
        property setter としてフックする。"""
        v = bool(v)
        if v == self._flipped and self._flip_anim_progress >= 1.0:
            return  # 同値かつ静止中: 何もしない
        # アニメ開始
        # 開始時点の「描画用 flipped」を from として固定
        self._flip_anim_from = self._render_flipped()
        self._flipped = v
        self._flip_anim_to = v
        self._start_flip_anim()

    def _render_flipped(self) -> bool:
        """描画(_xy)が参照する flipped 値。
        アニメ中は _flip_anim_from(開始時点の状態)、静止時は _flipped。
        アニメ中は描画データを from 状態で固定し、それを transform で
        回転するため、見た目連続性が保たれる(t=1.0 到達時に _flipped へ
        切替わるが、180度回転 = 反転と同じなので位置が一致して連続)。"""
        if self._flip_anim_progress < 1.0:
            return self._flip_anim_from
        return self._flipped

    def _start_flip_anim(self):
        """盤面180度回転のアニメを起動。
        _flip_anim_progress を 0.0 → 1.0 へ補間する。
        回転方向は _flip_anim_to によって決まる:
          - _flip_anim_to=True (反転ON へ向かう): 時計回り
          - _flip_anim_to=False (反転OFF へ戻る): 反時計回り
        180度回転なので始点と終点の位置は方向によらず一致するが、
        アニメ中の途中位置が鏡像になり、ON/OFF で逆方向に回る視覚効果が得られる。
        パフォーマンス対策として、開始時に「石なし盤面」「黒石1個」「白石1個」
        の 3つの pixmap を生成する。アニメ中の各フレームは:
          1. 石なし盤面 pixmap を回転 transform で描画(下地)
          2. 各石の論理位置を _xy で取得 → 回転 transform を適用 → 画面座標
          3. その画面座標に石 pixmap を drawPixmap(回転なし)
        という流れで、光源を世界座標で固定したまま盤だけが回転する物理的に
        正しい挙動を、軽量に実現する(grdient 計算は事前1回のみ、フレーム
        あたり drawPixmap は ~200回程度で GPU 加速の対象)。"""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        # 既存アニメは停止(連打時に滑らかに新規起動)
        if self._flip_anim is not None:
            try:
                self._flip_anim.stop()
            except RuntimeError:
                pass
            self._flip_anim = None
        # 進行中の座標フェードインアニメも停止(連打時に新しい回転が始まるので
        # 進行中のフェードインを破棄)。回転中は座標を非表示にしたいので、
        # _flip_coord_fadein_progress を 0.0 にリセット。
        if self._flip_coord_fadein_anim is not None:
            try:
                self._flip_coord_fadein_anim.stop()
            except RuntimeError:
                pass
            self._flip_coord_fadein_anim = None
        self._flip_coord_fadein_progress = 0.0
        # 古い pixmap があれば破棄
        self._flip_anim_pixmap_board = None
        self._flip_anim_pixmap_stone_b = None
        self._flip_anim_pixmap_stone_w = None
        # progress を 0 にしてから sync して pixmap キャプチャ。
        # ここで _render_flipped() は _flip_anim_from を返す状態になっており、
        # _xy() が「アニメ開始時の論理状態」で位置を計算する。
        self._flip_anim_progress = 0.0
        # キャプチャ前に _overlay_alpha 等の状態を最新化(paintEvent 冒頭で
        # 呼ばれる sync を、ここでも一度走らせて pixmap が現フレームの
        # オーバーレイ alpha を反映するようにする)。
        self._sync_overlay_alpha_targets()
        # ウィジェットサイズが未確定(初期化直後など)ならキャプチャしない
        if self.width() > 0 and self.height() > 0:
            try:
                self._capture_flip_pixmap()
            except Exception:
                self._flip_anim_pixmap_board = None
                self._flip_anim_pixmap_stone_b = None
                self._flip_anim_pixmap_stone_w = None
        anim = QVariantAnimation(self)
        anim.setDuration(self._FLIP_ANIM_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_changed(t):
            try:
                self._flip_anim_progress = float(t)
                self.update()
            except (RuntimeError, TypeError, ValueError):
                pass

        def _on_finished():
            self._flip_anim_progress = 1.0
            self._flip_anim = None
            # キャッシュ pixmap を破棄してメモリ解放
            self._flip_anim_pixmap_board = None
            self._flip_anim_pixmap_stone_b = None
            self._flip_anim_pixmap_stone_w = None
            # 回転完了 → 座標フェードインアニメを起動
            self._start_flip_coord_fadein_anim()
            self.update()

        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._flip_anim = anim
        anim.start()

    def _start_flip_coord_fadein_anim(self):
        """反転アニメ完了直後に呼ぶ: 座標ラベルを 0→1 へフェードインする。
        回転中は _flip_coord_fadein_progress=0.0 で完全非表示、回転完了で
        ここから _FLIP_COORD_FADEIN_DURATION_MS かけて 1.0 まで補間する。"""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        # 既存があれば停止
        if self._flip_coord_fadein_anim is not None:
            try:
                self._flip_coord_fadein_anim.stop()
            except RuntimeError:
                pass
            self._flip_coord_fadein_anim = None
        self._flip_coord_fadein_progress = 0.0
        anim = QVariantAnimation(self)
        anim.setDuration(self._FLIP_COORD_FADEIN_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_changed(t):
            try:
                self._flip_coord_fadein_progress = float(t)
                self.update()
            except (RuntimeError, TypeError, ValueError):
                pass

        def _on_finished():
            self._flip_coord_fadein_progress = 1.0
            self._flip_coord_fadein_anim = None
            self.update()

        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._flip_coord_fadein_anim = anim
        anim.start()

    def _xy(self, col, row):
        """石とそれに紐づく要素（候補手・最後の手・手順番号・形勢判断
        マーカー・評価バッジ・次の手マーカー）の座標変換。
        _render_flipped() が True なら 180度回転した位置を返す。
        アニメ中は _flip_anim_from(開始時点)で固定され、回転 transform
        側で見かけの位置を補間する仕組み。"""
        ox, oy = self._orig()
        c = self._cell()
        if self._render_flipped():
            col = self.board_size - 1 - col
            row = self.board_size - 1 - row
        return ox + col * c, oy + row * c

    def _flip_pos(self, col: int, row: int) -> tuple[int, int]:
        """マウス入力等で「画面上のマス位置」と「論理座標」を相互変換する。
        180度回転は自分自身が逆操作（対合）なので、入力・出力の両方向に
        同じ関数を使える。flipped でないときは恒等変換。
        論理的な最終状態を見るので self.flipped(= self._flipped)を使う。"""
        if not self.flipped:
            return col, row
        return self.board_size - 1 - col, self.board_size - 1 - row

    def _sync_overlay_alpha_targets(self):
        """show_ownership / show_hints / bool(move_numbers) の現在状態を見て、
        各オーバーレイの α 目標値を再計算し、目標値が変化していれば
        QVariantAnimation でフェードを起動する。

        呼び出し側コード(self._board.show_ownership = ... など)を
        変更せずアニメ機構を組み込むため、paintEvent 冒頭でこのメソッドを
        呼ぶ運用にしている。

        フェードアウト中にデータの実体がクリアされる場合に備えて、
        paintEvent 末尾で「描画後の非空データ」を _overlay_snap_* に
        スナップショット保存しておき、フェードアウト中はそれを使って
        描画を続ける(_clear_overlay_snapshot で完了時に破棄)。"""
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        targets = {
            "ownership": 1.0 if self.show_ownership else 0.0,
            # hints は以下の条件で表示:
            #   - メニュートグル(show_hints) ON
            #   - かつ「候補手データあり」or「AI解析ON」
            # AI解析ON中は着手で candidates が一瞬空になる経路があっても α を
            # 維持してフェードチラつきを防ぐ(データ到着時に瞬時表示)。
            # 解析OFF時に candidates=[] にされた時はこの条件式が False になり
            # フェードアウトする。
            "hints":     1.0 if (self.show_hints and (bool(self.candidates) or self.ai_enabled)) else 0.0,
            "move_num":  1.0 if self.move_numbers   else 0.0,
            "badges":    1.0 if self.show_badges    else 0.0,
            # 座標 ON/OFF: 文字 alpha とレイアウト補間の両方に使う進捗値
            "coords":    1.0 if self.show_coords    else 0.0,
        }
        for key, target in targets.items():
            cur = self._overlay_alpha.get(key, 0.0)
            running = self._overlay_anims.get(key)
            running_target = running[2] if running else cur
            if abs(running_target - target) < 1e-6:
                continue  # 既に同じ目標へアニメ中 or 同値
            # 既存アニメを停止
            if running is not None:
                try:
                    running[0].stop()
                except RuntimeError:
                    pass
            # 新規アニメを起動
            anim = QVariantAnimation(self)
            anim.setDuration(self._OVERLAY_FADE_DURATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            start = float(cur)
            end   = float(target)
            anim_key = key

            def _on_changed(t, _key=anim_key, _s=start, _e=end):
                try:
                    t = float(t)
                except (TypeError, ValueError):
                    return
                self._overlay_alpha[_key] = _s + (_e - _s) * t
                self.update()

            def _on_finished(_key=anim_key, _e=end):
                self._overlay_alpha[_key] = _e
                self._overlay_anims.pop(_key, None)
                # フェードアウト完了: スナップショット破棄(常時負荷を回避)
                if _e <= 0.0:
                    self._clear_overlay_snapshot(_key)
                self.update()

            anim.valueChanged.connect(_on_changed)
            anim.finished.connect(_on_finished)
            self._overlay_anims[key] = (anim, start, end)
            anim.start()

    def _save_overlay_snapshots_from_current(self):
        """paintEvent 末尾で呼ぶ: 現フレームの非空データを覚えておき、
        次フレーム以降にデータ実体がクリアされた状態でフェードアウトしても、
        覚えておいた値を使って描画継続できるようにする。"""
        if self.ownership:
            self._overlay_snap_ownership = list(self.ownership)
        if self.move_numbers:
            self._overlay_snap_move_numbers = dict(self.move_numbers)
        if self.candidates:
            # 解析トグル OFF 等で candidates がクリアされても、フェードアウト中は
            # スナップショットを使って描画継続する
            self._overlay_snap_candidates = list(self.candidates)

    def _clear_overlay_snapshot(self, key):
        """フェードアウト完了時にスナップショットを破棄。"""
        if key == "ownership":
            self._overlay_snap_ownership = None
        elif key == "move_num":
            self._overlay_snap_move_numbers = None
        elif key == "hints":
            self._overlay_snap_candidates = None

    def _paint_board_inner(self, p, skip_stones: bool = False):
        """盤本体(座標を除く全要素)を p に描画する。
        paintEvent と _capture_flip_pixmap の両方から呼ばれる。
        座標ラベル(_coords)はここに含まない(回転外で固定描画されるため)。

        skip_stones=True の場合、石(_stones)の描画をスキップする。
        反転アニメ用に「石なし盤面」をキャプチャするときに使う(石は別途
        事前生成された pixmap として、回転後の位置に貼り付けられる)。

        呼び出し前提:
        - p は QPainter で Antialiasing がセット済み
        - _overlay_alpha は最新値に同期済み
        """
        a_ownership = self._overlay_alpha.get("ownership", 0.0)
        a_hints     = self._overlay_alpha.get("hints",     0.0)
        a_move_num  = self._overlay_alpha.get("move_num",  0.0)
        a_badges    = self._overlay_alpha.get("badges",    0.0)

        with _profile("Board.paint.bg"):
            self._bg(p)
        with _profile("Board.paint.grid"):
            self._grid(p)
        with _profile("Board.paint.stars"):
            self._stars(p)
        with _profile("Board.paint.next_lower"):
            self._next_moves_overlay(p)
        if not skip_stones:
            with _profile("Board.paint.stones"):
                self._stones(p)
        # ── 形勢(ownership) ──
        # フェードアウト中もスナップショットを使って描画継続
        if a_ownership > 0.0:
            ownership_data = (self.ownership if self.ownership
                              else (self._overlay_snap_ownership or []))
            if ownership_data:
                with _profile("Board.paint.ownership"):
                    # _ownership_markers が self.ownership を参照するため一時的に差替
                    _saved = self.ownership
                    self.ownership = ownership_data
                    prev_op = p.opacity()
                    p.setOpacity(prev_op * a_ownership)
                    self._ownership_markers(p)
                    p.setOpacity(prev_op)
                    self.ownership = _saved
        # ── 解析(hints + 次手リング上層) ──
        if a_hints > 0.0:
            # ai_enabled=True の場合はスナップショットを使わない。
            # 着手で candidates が一瞬空になる経路では「古い候補手をスナップ
            # ショットで描画継続」すると、ポンダリング結果到着までの間
            # 古い候補手が見え続けてしまうため。α は 1.0 のままなので、
            # データ到着時に瞬時表示される(切替の遅れがない)。
            # ai_enabled=False (解析OFF でフェードアウト中) の時だけ、
            # スナップショットを使って滑らかに消す。
            if self.ai_enabled:
                cand_data = self.candidates if self.candidates else []
            else:
                cand_data = (self.candidates if self.candidates
                             else (self._overlay_snap_candidates or []))
            if cand_data:
                with _profile("Board.paint.hints"):
                    _saved_cand = self.candidates
                    self.candidates = cand_data
                    prev_op = p.opacity()
                    p.setOpacity(prev_op * a_hints)
                    self._hints(p)
                    self._next_moves_overlay_top(p)
                    p.setOpacity(prev_op)
                    self.candidates = _saved_cand
        # ── 手順番号 ──
        if a_move_num > 0.0:
            mn_data = (self.move_numbers if self.move_numbers
                       else (self._overlay_snap_move_numbers or {}))
            if mn_data:
                with _profile("Board.paint.move_num"):
                    _saved = self.move_numbers
                    self.move_numbers = mn_data
                    prev_op = p.opacity()
                    p.setOpacity(prev_op * a_move_num)
                    self._move_number_overlay(p)
                    p.setOpacity(prev_op)
                    self.move_numbers = _saved
        # 最終手の悪手バッジは候補手・次手リング含む全オーバーレイの上に重ねる。
        # メニューOFF へのフェードアウト中は show_badges=False になっているが
        # α > 0 の間は show_badges を一時 True に差し替えて描画継続する。
        if a_badges > 0.0:
            with _profile("Board.paint.last_badge"):
                _saved_sb = self.show_badges
                self.show_badges = True
                prev_op = p.opacity()
                p.setOpacity(prev_op * a_badges)
                self._last_move_badge(p)
                p.setOpacity(prev_op)
                self.show_badges = _saved_sb

        # 最後の手のマーキング (中央リング)。show_last_move_mark フラグで
        # 制御。バッジ (右上の○△✕) と独立で、両方有効でも併存する。
        # フェード処理は不要: ON/OFF 切替時に瞬時に表示/非表示。
        with _profile("Board.paint.last_mark"):
            self._last_move_mark(p)

    def _capture_flip_pixmap(self):
        """反転アニメ開始時に呼ぶ: 描画コンポーネントを 3つの pixmap に分割
        キャプチャする。
          - self._flip_anim_pixmap_board   : 石なし盤面(木目・格子・星・候補手・
            形勢・手数・最後の手バッジ等)。回転対象。
          - self._flip_anim_pixmap_stone_b : 黒石1個分(影・グラデ・ハイライト
            込み)。回転しない(光源は世界座標で固定)。
          - self._flip_anim_pixmap_stone_w : 白石1個分。同上。
        以降のアニメ中フレームは:
          1. 盤面 pixmap を回転 transform で描画
          2. 各石の論理位置を _xy で取得 → 回転 transform を適用 → 画面座標
          3. その画面座標に石 pixmap を drawPixmap(回転なし)
        という流れで「光源固定・盤だけ回転」の物理的に正しい挙動を実現する。

        高 DPI 対応: devicePixelRatio() に従い物理ピクセル解像度で生成する
        ことで、回転中もアンチエイリアスの品質を保つ。"""
        from PyQt6.QtGui import QPixmap
        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height()

        # ── 1. 石なし盤面のキャプチャ ──
        board = QPixmap(int(w * dpr), int(h * dpr))
        board.setDevicePixelRatio(dpr)
        board.fill(Qt.GlobalColor.transparent)
        pp = QPainter(board)
        pp.setRenderHint(QPainter.RenderHint.Antialiasing)
        pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # _xy は _render_flipped() = _flip_anim_from を返す状態で位置計算
        self._paint_board_inner(pp, skip_stones=True)
        pp.end()
        self._flip_anim_pixmap_board = board

        # ── 2. 黒石・白石 pixmap の生成 ──
        # 通常時の石描画(_stones)と完全に同じ pixmap を共通メソッドで生成する。
        # _build_stone_pixmap は (pm, pad) のタプルを返す。
        cell = self._cell()
        rad = cell * 0.48
        pm_b, pad = self._build_stone_pixmap("B", rad, dpr)
        pm_w, _   = self._build_stone_pixmap("W", rad, dpr)
        self._flip_anim_pixmap_stone_b = pm_b
        self._flip_anim_pixmap_stone_w = pm_w
        self._flip_anim_stone_rad = rad
        self._flip_anim_stone_pad = pad

    def start_content_change_anim(self):
        """棋譜内容を差し替える直前に呼ぶ:
        現在の見た目(全要素 + 座標含む)を pixmap キャプチャしてから、
        クロスフェードアニメ(_content_change_progress: 0 → 1)を起動する。
        呼び出し側はその後、self.stones / candidates / board_size / move_numbers
        等を新しい状態に更新する。次の paintEvent で旧 pixmap が α=1 → 0、
        新しい通常描画が α=0 → 1 で重なって表示され、見た目が滑らかに切替わる。

        異なる盤面サイズ(9路 ↔ 13路 ↔ 19路)間での切替時、ウィジェット領域は
        同じだが盤面の物理サイズ(マージン)は変わるため、フェード合成で
        違和感なく繋ぐ。
        """
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        from PyQt6.QtGui import QPixmap

        # ウィジェットサイズ未確定なら何もしない(初期化直後など)
        if self.width() <= 0 or self.height() <= 0:
            return

        # 既存アニメは停止(連打耐性)
        if self._content_change_anim is not None:
            try:
                self._content_change_anim.stop()
            except RuntimeError:
                pass
            self._content_change_anim = None
        # 古い pixmap があれば破棄
        self._content_change_pixmap_old = None
        self._content_change_progress = 0.0

        # 現在の見た目を pixmap にキャプチャ。
        # _sync_overlay_alpha_targets を一度走らせて状態を最新化してから _paint_main。
        self._sync_overlay_alpha_targets()
        try:
            dpr = self.devicePixelRatioF()
            w, h = self.width(), self.height()
            pix = QPixmap(int(w * dpr), int(h * dpr))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pix)
            pp.setRenderHint(QPainter.RenderHint.Antialiasing)
            pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._paint_main(pp)
            pp.end()
            self._content_change_pixmap_old = pix
        except Exception:
            self._content_change_pixmap_old = None

        # pixmap が取れなかった場合はアニメ起動しない(瞬時切替で済ませる)
        if self._content_change_pixmap_old is None:
            self._content_change_progress = 1.0
            return

        anim = QVariantAnimation(self)
        anim.setDuration(self._CONTENT_CHANGE_FADE_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_changed(t):
            try:
                self._content_change_progress = float(t)
                self.update()
            except (RuntimeError, TypeError, ValueError):
                pass

        def _on_finished():
            self._content_change_progress = 1.0
            self._content_change_anim = None
            # キャッシュ pixmap を破棄してメモリ解放
            self._content_change_pixmap_old = None
            self.update()

        anim.valueChanged.connect(_on_changed)
        anim.finished.connect(_on_finished)
        self._content_change_anim = anim
        anim.start()

    def _paint_main(self, p):
        """盤本体 + 座標 までの完全な描画(反転アニメ・座標 ON/OFF アニメ含む)。
        paintEvent と _capture_content_pixmap の両方から呼ばれる。
        呼び出し側で:
        - QPainter の Antialiasing は設定済み
        - _sync_overlay_alpha_targets() は呼び出し済み(状態が最新)
        を前提とする。本メソッド自身は状態を変更しない(snapshot 保存等は呼び出し側)。"""
        a_coords = self._overlay_alpha.get("coords", 0.0)

        # ── 反転アニメ用 transform ──
        # _flip_anim_progress < 1.0 のとき、盤(木目・格子・星・候補手・形勢
        # マーカー・最後の手バッジ等)を時計回りに 180*t 度回転する。
        # 座標ラベル(_coords)は変換外で固定描画する。
        # 物理的な光源固定の挙動を再現するため、石は別レイヤーで扱う:
        # 盤を回した後、回転 transform を解除してから、各石の論理位置を
        # 回転後の画面座標に変換し、事前生成した石 pixmap(立体感込み)を
        # その位置に貼り付ける。これにより光源は世界座標で固定されたまま、
        # 石は盤と一緒に位置だけ動く(物理的に正しい挙動)。
        # パフォーマンスも軽い: 各フレームの処理は「盤面 pixmap 1枚回転描画
        # + 石 pixmap を ~200個 drawPixmap」のみで、grdient 計算は事前1回。
        flip_t = self._flip_anim_progress
        flipping = (flip_t < 1.0
                    and self._flip_anim_pixmap_board is not None
                    and self._flip_anim_pixmap_stone_b is not None
                    and self._flip_anim_pixmap_stone_w is not None)
        if flipping:
            with _profile("Board.paint.flip_anim"):
                from PyQt6.QtGui import QTransform
                cx = self.width() / 2.0
                cy = self.height() / 2.0
                # 回転方向: 反転ON(_flip_anim_to=True) は時計回り、
                # 反転OFF(_flip_anim_to=False) は反時計回り。
                # 180度回転なので最終位置は両方向で同じ(始点と終点は一致)、
                # アニメ中の途中位置だけが鏡像になる(視覚的に「戻る」印象)。
                # Qt 座標系では rotate(+) が時計回り、rotate(-) が反時計回り。
                direction = 1.0 if self._flip_anim_to else -1.0
                angle_deg = 180.0 * flip_t * direction
                # 1. 石なし盤面を回転描画
                p.save()
                p.translate(cx, cy)
                p.rotate(angle_deg)
                p.translate(-cx, -cy)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawPixmap(0, 0, self._flip_anim_pixmap_board)
                p.restore()
                # 2. 各石の画面座標を計算して石 pixmap を貼り付け(世界座標で固定)
                #    回転 transform を QTransform で再現し、各石の論理位置を変換する。
                xform = QTransform()
                xform.translate(cx, cy)
                xform.rotate(angle_deg)
                xform.translate(-cx, -cy)
                rad = self._flip_anim_stone_rad
                pad = self._flip_anim_stone_pad
                stone_b = self._flip_anim_pixmap_stone_b
                stone_w = self._flip_anim_pixmap_stone_w
                # 石 pixmap は中心が pixmap 内 (rad+pad, rad+pad)、サイズ (rad*2+pad*2)
                offset = rad + pad
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                for (col, row), color in self.stones.items():
                    # _xy() は _render_flipped() = _flip_anim_from を返す状態で
                    # アニメ開始時の論理位置を返す(キャプチャ時と同じ前提)。
                    bx, by = self._xy(col, row)
                    # 回転 transform を適用して画面座標へ
                    sx, sy = xform.map(bx, by)
                    pm = stone_b if color == "B" else stone_w
                    # 石中心が (sx, sy) になるよう pixmap 左上を offset 分シフト
                    p.drawPixmap(QPointF(sx - offset, sy - offset), pm)
        else:
            # 静止時(または pixmap 未準備の保険): 通常の盤本体描画
            self._paint_board_inner(p)
        # ── 座標ラベル ──
        # 座標表示の可視性 = a_coords(座標 ON/OFF アニメ) × flip_coord_vis
        # flip_coord_vis の構成:
        # - 回転中: 開始 _FLIP_COORD_FADEOUT_RATIO の期間で 1→0(回転と並行に消失)、
        #   その後は回転完了まで 0(完全非表示)
        # - 回転完了後: _flip_coord_fadein_progress(別アニメ)が 0→1 に動く
        # - 静止時(回転していないとき): 1.0(通常表示)
        flip_coord_vis = 1.0
        if flip_t < 1.0:
            # 回転中: 序盤フェードアウト、それ以降は 0
            r = self._FLIP_COORD_FADEOUT_RATIO
            if flip_t <= r:
                flip_coord_vis = 1.0 - flip_t / r              # 1 → 0
            else:
                flip_coord_vis = 0.0
        elif self._flip_coord_fadein_progress < 1.0:
            # 回転完了後のフェードイン中
            flip_coord_vis = self._flip_coord_fadein_progress
        final_coord_alpha = a_coords * flip_coord_vis
        if final_coord_alpha > 0.0:
            with _profile("Board.paint.coords"):
                self._coords(p, alpha=final_coord_alpha)

    @_profile_method("Board.paint")
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # オーバーレイのフェード状態を同期(必要ならアニメ起動)
        self._sync_overlay_alpha_targets()

        # ── 棋譜内容差し替え時のクロスフェード ──
        # _content_change_progress < 1.0 のとき、旧 pixmap(切替直前の見た目を
        # キャプチャ済)を α=1-t で下地に描画してから、新しい通常描画を α=t で
        # 上に重ねる。完了で旧 pixmap は破棄される。
        # 反転アニメ等もこの中で重なって動く可能性があるが、内容差し替え時は
        # 直前にデータが切り替わっているので、新通常描画は新棋譜の状態で動く。
        cc_t = self._content_change_progress
        cc_active = (cc_t < 1.0 and self._content_change_pixmap_old is not None)
        if cc_active:
            with _profile("Board.paint.content_change"):
                prev_op = p.opacity()
                # 旧 pixmap (α = 1 - t) を下地に
                p.setOpacity(prev_op * (1.0 - cc_t))
                p.drawPixmap(0, 0, self._content_change_pixmap_old)
                # 新通常描画を α = t で重ねる
                p.setOpacity(prev_op * cc_t)
                self._paint_main(p)
                p.setOpacity(prev_op)
        else:
            self._paint_main(p)
        p.end()
        # 描画後の非空データを覚えておき、次フレームでデータがクリア
        # された状態でフェードアウトしてもスナップショットで描画継続できるようにする
        self._save_overlay_snapshots_from_current()

    # 死に石判定の ownership 閾値
    DEAD_STONE_THRESHOLD = 0.5

    def _ownership_markers(self, p):
        """
        ownership を四角形マーカーで表現（マーカー表示モード）。
        - 各交点に ownership 値に応じたサイズの正方形を描画
        - ownership > 0 → 黒い四角、< 0 → 白い四角
        - サイズ: |v|^0.7 × 0.5 × cell
        - 石のある交点:
          - 死に石（石色と ownership 符号が逆転、|v| >= DEAD_STONE_THRESHOLD）→ 逆色マーカー描画
          - それ以外（生きている石）→ マーカー描画しない
        - 石のない交点: ownership に応じたマーカー描画
        """
        n = self.board_size
        if len(self.ownership) != n * n:
            return

        cell = self._cell()
        p.setPen(Qt.PenStyle.NoPen)

        for row in range(n):
            for col in range(n):
                v = self.ownership[row * n + col]
                abs_v = abs(v)

                # サイズの計算: |v|^0.7 × 0.5 × cell
                size = (abs_v ** 0.7) * 0.5 * cell
                if size < 1.0:
                    continue  # 小さすぎる場合は描画しない

                stone_color = self.stones.get((col, row))
                if stone_color is not None:
                    # 石のある交点: 死に石判定
                    is_dead = (stone_color == "B" and v <= -self.DEAD_STONE_THRESHOLD) or \
                              (stone_color == "W" and v >= self.DEAD_STONE_THRESHOLD)
                    if not is_dead:
                        continue
                    # 死に石: 逆色マーカーを目立たせる
                    if v > 0:
                        p.setBrush(QBrush(QColor(0, 0, 0, 230)))      # 黒マーカー（白石上）
                    else:
                        p.setBrush(QBrush(QColor(255, 255, 255, 230)))  # 白マーカー（黒石上）
                else:
                    # 石のない交点: 通常のマーカー
                    alpha = 230
                    if v > 0:
                        p.setBrush(QBrush(QColor(0, 0, 0, alpha)))      # 黒
                    else:
                        p.setBrush(QBrush(QColor(255, 255, 255, alpha)))  # 白

                x, y = self._xy(col, row)
                half = size / 2
                p.drawRect(QRectF(x - half, y - half, size, size))

    # ── 木目テクスチャ（固定512×512、ファイルキャッシュ）──────
    _wood_pixmap: "Optional[QPixmap]" = None  # シングルトン

    @staticmethod
    def _wood_cache_path() -> Path:
        """テクスチャPNGの保存先パスを返す。
        main_window.py と同じ階層の gui/ フォルダ内に保存する。
        """
        cache_dir = Path(__file__).parent / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "wood_texture_512_d8b058.png"

    @staticmethod
    def _make_wood_pixmap_data(size: int = 512) -> "QPixmap":
        """Perlinノイズ木目テクスチャを numpy で生成（固定サイズ）。"""
        import numpy as np
        w = h = size
        GRID = 8
        BR, BG, BB = 0xd8, 0xb0, 0x58  # #d8b058 薄赤みがかり
        CONTRAST = 0.07

        def smoothstep(t):
            return t * t * (3.0 - 2.0 * t)

        def noise2d_g(fx, fy, g_arr):
            gh, gw = g_arr.shape
            ix = fx.astype(np.int32); iy = fy.astype(np.int32)
            tx = fx - ix;            ty = fy - iy
            sx = smoothstep(tx);     sy = smoothstep(ty)
            ix0 = ix % gw; ix1 = (ix + 1) % gw
            iy0 = iy % gh; iy1 = (iy + 1) % gh
            v00 = g_arr[iy0, ix0]; v10 = g_arr[iy0, ix1]
            v01 = g_arr[iy1, ix0]; v11 = g_arr[iy1, ix1]
            return (v00*(1-sx)+v10*sx)*(1-sy)+(v01*(1-sx)+v11*sx)*sy

        gw_n = w // GRID + 3
        gh_n = h // GRID + 3
        grad1 = np.random.default_rng(42 ).random((gh_n, gw_n), dtype=np.float32)
        grad2 = np.random.default_rng(137).random((gh_n, gw_n), dtype=np.float32)
        grad3 = np.random.default_rng(251).random((gh_n, gw_n), dtype=np.float32)

        px = np.arange(w, dtype=np.float32)
        py = np.arange(h, dtype=np.float32)
        PX, PY = np.meshgrid(px, py)
        fx0 = PX / GRID * 0.6
        fy0 = PY / GRID * 3.5

        n1 = noise2d_g(fx0,           fy0,              grad1)
        n2 = noise2d_g(fx0 * 1.8,     fy0 * 0.4 + 5.3,  grad2)
        n3 = noise2d_g(PX/GRID*0.5,   PY/GRID*7.0+12.1, grad3)

        ring  = np.sin((fy0 * 0.9 + n1 * 2.5) * np.pi) * 0.5 + 0.5
        fiber = n3 * 0.30
        v = np.clip(ring * 0.65 + n2 * 0.2 + fiber + 0.05, 0.0, 1.0)
        factor = (1.0 - CONTRAST / 2.0) + v * CONTRAST

        r = np.clip(BR * factor, 0, 255).astype(np.uint8)
        g = np.clip(BG * factor, 0, 255).astype(np.uint8)
        b = np.clip(BB * factor, 0, 255).astype(np.uint8)
        a = np.full((h, w), 255, dtype=np.uint8)
        argb = np.stack([b, g, r, a], axis=2).copy()
        img = QImage(argb.data, w, h, w * 4, QImage.Format.Format_ARGB32)
        return QPixmap.fromImage(img.copy())

    @classmethod
    def get_wood_pixmap(cls) -> "QPixmap":
        """
        木目テクスチャを返す。
        1. メモリキャッシュ → 即時
        2. PNGファイル      → 高速読み込み
        3. 初回生成         → numpy で生成してファイルに保存
        """
        if cls._wood_pixmap is not None:
            return cls._wood_pixmap

        cache_path = cls._wood_cache_path()
        if cache_path.exists():
            pix = QPixmap(str(cache_path))
            if not pix.isNull():
                cls._wood_pixmap = pix
                logger.info("木目テクスチャをキャッシュから読み込み: %s", cache_path)
                return cls._wood_pixmap

        logger.info("木目テクスチャを生成中...")
        pix = cls._make_wood_pixmap_data(512)
        pix.save(str(cache_path), "PNG")
        logger.info("木目テクスチャを保存: %s", cache_path)
        cls._wood_pixmap = pix
        return cls._wood_pixmap

    def _bg(self, p):
        ox, oy = self._orig()
        c = self._cell()
        bw = c * (self.board_size - 1)
        # 板内マージン（板の端〜第1グリッド線）:
        #   座標OFF: c*0.6（木枠相当・少し広め）
        #   座標ON : c*1.2（ラベル領域として拡張、石との余裕確保）
        # 外マージン（キャンバス端〜板の端）は OFF時 c*0.5、ON時 c*0.55
        # 進捗 t∈[0,1] で線形補間して滑らかに切替える。
        t = self._coord_progress()
        m = c * (0.6 + 0.6 * t)

        # 碁盤縁の座標は「両端独立に round」する。
        # 旧実装は board_x = int(ox - m), board_w = int(bw + 2*m) と 2 段階切り捨て
        # しており、board_x + board_w(右端)が数学的右端より最大 2px 内側に
        # 寄るため、罫線(round 使用)との余白が左右(上下)で非対称になっていた。
        # 両端を独立に round することで、誤差を ±0.5px に抑え、左右・上下の余白
        # を対称化する(罫線との整合性も保たれる)。
        board_x = round(ox - m)
        board_y = round(oy - m)
        board_x_right = round(ox + (self.board_size - 1) * c + m)
        board_y_bottom = round(oy + (self.board_size - 1) * c + m)
        board_w = board_x_right - board_x
        board_h = board_y_bottom - board_y

        # 盤端の角丸: アプリ全体の R_MD に揃えて統一感を出す
        # （0px だと周囲のパネル群と並んだとき機械的に浮いて見える）
        board_radius = R_MD

        base = T().board_base_color
        if base is None:
            # デフォルト: 木目テクスチャ
            # 毎フレーム drawPixmap でスケール拡大すると重いため、
            # (board_w, board_h) サイズのスケール済みpixmapをインスタンス側に
            # キャッシュ。サイズが変わった時だけ再スケールする。
            cache_key = (board_w, board_h)
            if (getattr(self, "_wood_scaled_key", None) != cache_key
                    or getattr(self, "_wood_scaled", None) is None):
                src = BoardWidget.get_wood_pixmap()
                # FastTransformation: 木目はランダム要素が支配的なので
                # 補間品質より速度を優先しても見た目の差はほぼ出ない
                self._wood_scaled = src.scaled(
                    board_w, board_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                self._wood_scaled_key = cache_key
            # 角丸クリップで木目pixmapを描画
            p.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(
                QRectF(board_x, board_y, board_w, board_h),
                board_radius, board_radius,
            )
            p.setClipPath(clip_path)
            p.drawPixmap(board_x, board_y, self._wood_scaled)
            p.restore()
        else:
            # ブラック / ホワイト: 単色塗り
            p.setBrush(QBrush(base))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(
                QRectF(board_x, board_y, board_w, board_h),
                board_radius, board_radius,
            )


    # ── grid pixmap キャッシュ(_grid の高速化用) ──────────────────
    # 19路盤なら 19+19 = 38本の格子線を毎フレーム drawLine するコストが
    # 平均 2ms/呼出かかる(計測実測値)。盤面サイズ・セルサイズ・線色・DPI が
    # 変わらない限り描画結果は同一なので、pixmap にキャッシュして drawPixmap
    # 1回で済ませる。リサイズ・テーマ切替・棋譜サイズ変更時はキャッシュキーが
    # 変わって自動再生成される。
    def _ensure_grid_pixmap(self):
        """grid 描画結果を pixmap キャッシュ化する(必要に応じて再生成)。"""
        from PyQt6.QtGui import QPixmap
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return False
        dpr = self.devicePixelRatioF()
        cell = self._cell()
        coord_t = self._coord_progress()
        line_color = T().board_line_color
        # キャッシュキー: 描画結果が変わり得るすべての要因を網羅
        # (width, height, board_size, cell, coord_progress, line_color_rgba, dpr)
        cache_key = (
            w, h,
            self.board_size,
            round(cell, 3),
            round(coord_t, 3),
            line_color.rgba(),
            round(dpr, 3),
        )
        if getattr(self, "_grid_cache_key", None) == cache_key \
                and getattr(self, "_grid_cache_pixmap", None) is not None:
            return True  # キャッシュヒット
        # 再生成: ウィジェット全面サイズの透明 pixmap を作って絶対座標系で grid を描画する
        # (pixmap を drawPixmap(0,0) で貼るので座標変換不要)
        pix_w = int(w * dpr)
        pix_h = int(h * dpr)
        pix = QPixmap(pix_w, pix_h)
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        pp = QPainter(pix)
        # アンチエイリアスは OFF。
        # 格子線は水平・垂直のみで斜め線が無いため AA OFF で見た目劣化なし。
        # AA ON だと整数座標 1px 線が AA で 2 ピクセルに薄く分散し、
        # 縦線と横線の AA 縁が交点で二重合成されて「交点だけ濃く見える」
        # 現象が出る(線色そのものに α は無いが、AA で生まれる縁の半透明
        # ピクセルが重なるため)。AA OFF ならピクセル境界に 1px くっきり
        # 描画され、交点と単独線の色が完全に同じになる。
        # DPR には依存しないため HiDPI でも堅牢。
        pp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pp.setPen(QPen(line_color, 1.0))
        for i in range(self.board_size):
            x1, y1 = self._xy_grid(0, i)
            x2, _  = self._xy_grid(self.board_size - 1, i)
            pp.drawLine(round(x1), round(y1), round(x2), round(y1))
            x1, y1 = self._xy_grid(i, 0)
            _,  y2 = self._xy_grid(i, self.board_size - 1)
            pp.drawLine(round(x1), round(y1), round(x1), round(y2))
        pp.end()
        self._grid_cache_pixmap = pix
        self._grid_cache_key = cache_key
        return True

    def _grid(self, p):
        """格子線を描画する。pixmap キャッシュを使い、drawPixmap 1回で完了。
        (盤面サイズ・セルサイズ・線色・DPI が変わったときのみ再生成される。)"""
        if not self._ensure_grid_pixmap():
            return
        # キャッシュ pixmap はウィジェット全面を覆う透明 pixmap で、grid 線が
        # 絶対座標系(=ウィジェット座標系)で描かれているので、そのまま (0,0) に貼る。
        p.drawPixmap(0, 0, self._grid_cache_pixmap)

    def _coords(self, p, alpha: float = 1.0):
        c = self._cell(); ox, oy = self._orig()
        # 座標ラベルは板内マージン領域に描画（板の上）
        # 板内マージンは _bg() と同じ計算: 座標ON時 c*1.2
        slot = c * 1.2
        # フォントは小さめ（石との視覚的な分離を優先）
        font_size = max(6, int(slot * 0.33))
        f = Fmono(font_size)
        # アニメ中は alpha < 1.0 で半透明描画。setOpacity を一時的に乗算合成して使用。
        prev_op = p.opacity()
        if alpha < 1.0:
            p.setOpacity(prev_op * alpha)
        p.setFont(f); p.setPen(QPen(T().board_coord_color))
        # ラベル位置: スロット高さの30%位置（外側寄り）
        # スロット範囲は [oy - slot, oy]、中央揃えだと中心=50%位置
        # 30%位置にするため、矩形を外側方向に slot*0.20 シフト
        offset = slot * 0.20
        # 最終グリッド線の位置
        last_x, _ = self._xy_grid(self.board_size - 1, 0)
        _, last_y = self._xy_grid(0, self.board_size - 1)
        for i in range(self.board_size):
            # ── 列ラベル（上） ──
            x, _ = self._xy_grid(i, 0)
            lw = int(c * 0.9); lh = int(slot)
            p.drawText(int(x - lw / 2), int(oy - slot - offset),
                       lw, lh,
                       Qt.AlignmentFlag.AlignCenter, COLS[i])
            # ── 列ラベル（下） ──
            p.drawText(int(x - lw / 2), int(last_y + offset),
                       lw, lh,
                       Qt.AlignmentFlag.AlignCenter, COLS[i])
            # ── 行ラベル（左） ──
            _, y = self._xy_grid(0, i)
            lbl = str(self.board_size - i)
            lw2 = int(slot); lh2 = int(c * 0.9)
            p.drawText(int(ox - slot - offset), int(y - lh2 / 2),
                       lw2, lh2,
                       Qt.AlignmentFlag.AlignCenter, lbl)
            # ── 行ラベル（右） ──
            p.drawText(int(last_x + offset), int(y - lh2 / 2),
                       lw2, lh2,
                       Qt.AlignmentFlag.AlignCenter, lbl)
        # 透明度を元に戻す
        if alpha < 1.0:
            p.setOpacity(prev_op)

    def _stars(self, p):
        # 星点(hoshi)の位置。座標は 0-indexed。
        # - 19 路: 9 個(3-3, 4-4, 5-5 各線の交点、業界標準)
        # - 13 路: 5 個(隅の 4-4 と中央の 7-7、KGS / OGS / Lizzie / KaTrain 等
        #              主要囲碁ソフトと同じ実装)
        # - 9 路: 5 個(隅の 3-3 と中央の 5-5、同上)
        # 19 路以外は「9 個全部並べる」流派もあるが、現代の主要囲碁ソフトでは
        # 5 個が主流。標準に合わせることで他ソフトから移行するユーザーも違和感がない。
        positions = {
            19: [(3, 3), (3, 9), (3, 15),
                 (9, 3), (9, 9), (9, 15),
                 (15, 3), (15, 9), (15, 15)],
            13: [(3, 3), (3, 9), (9, 3), (9, 9), (6, 6)],
            9:  [(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)],
        }.get(self.board_size, [])
        # 星点サイズは「19路盤相当の cell_size」を基準に算出する。
        # _cell() は (board_size + α) で割るため盤面サイズが小さいほど大きくなり、
        # そのまま使うと 9/13 路で星点が 19 路より大きく見えてしまう。
        # 19 路換算で計算することで、どのサイズでも見た目が同じになる。
        # 座標 ON/OFF 進捗で補間(他要素と同期して滑らかに変化)。
        s = min(self.width(), self.height())
        t = self._coord_progress()
        cell_19 = s / (19 + 1.2 + 1.3 * t)
        r = max(2.5, cell_19 * 0.12)
        p.setBrush(QBrush(T().board_star_color)); p.setPen(Qt.PenStyle.NoPen)
        for c, r_ in positions:
            x, y = self._xy_grid(c, r_)
            p.drawEllipse(QPointF(round(x), round(y)), r, r)

    def _ensure_hints_pixmap(self):
        """hints 描画結果を pixmap キャッシュ化する(必要に応じて再生成)。
        candidates の値変化が無ければキャッシュを使い回し、 drawPixmap 1回で済ませる。
        戻り値: (pixmap_or_None, has_visible)
          pixmap_or_None: 描画すべきものがあれば QPixmap、無ければ None
          has_visible: True なら caller は drawPixmap、False ならスキップ
        """
        if not self.candidates:
            self._hints_pm = None
            self._hints_pm_key = None
            return False
        from PyQt6.QtGui import QPixmap
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return False
        dpr = self.devicePixelRatioF()
        cell = self._cell()

        with _profile("Board.paint.hints.prep"):
            total_visits    = sum(mi.visits for mi in self.candidates)
            visit_threshold = max(1, total_visits * 0.01)

            visible_pairs = []  # [(mi, pos), ...]
            for mi in self.candidates:
                if mi.visits < visit_threshold:
                    continue
                pos = sgf_coord_to_pos(self._gtp2sgf(mi.move))
                if pos is None:
                    continue
                visible_pairs.append((mi, pos))
            visible_pairs.sort(key=lambda mp: mp[0].order)
            visible_pairs = visible_pairs[:12]

            if not visible_pairs:
                self._hints_pm = None
                self._hints_pm_key = None
                return False

            visible = [mi for mi, _ in visible_pairs]
            max_visits = max((mi.visits for mi in visible), default=1) or 1

        # キャッシュキー: 描画結果が変わり得る要因をすべて
        # candidates の値(move, visits, score_lead)、turn、cell、ウィジェットサイズ、dpr
        # visits は粒度を 500 単位に粗くしてキャッシュヒット率を上げる:
        # ・visits は描画には ratio (= visits/max_visits) として alpha 値の計算に
        #   使われるが、500 visits 違いの alpha 差は人間の目では判別不可。
        # ・visits は表示テキストには出ない(★ または Δ score_lead のみ表示)。
        # score_lead は表示精度 0.1 単位なので、 round(*, 2) のまま維持
        # (粒度を粗くすると表示が古い値で固定される時間が生じる)。
        cand_sig = tuple(
            (mi.move, mi.visits // 500, round(mi.score_lead, 2))
            for mi in visible
        )
        cache_key = (
            w, h,
            round(cell, 3),
            self.turn,
            round(dpr, 3),
            cand_sig,
        )
        if getattr(self, "_hints_pm_key", None) == cache_key \
                and getattr(self, "_hints_pm", None) is not None:
            return True  # キャッシュヒット

        # 再生成: ウィジェット全面サイズの透明 pixmap に絶対座標系で描画
        with _profile("Board.paint.hints.draw"):
            pix_w = int(w * dpr)
            pix_h = int(h * dpr)
            pix = QPixmap(pix_w, pix_h)
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pix)
            pp.setRenderHint(QPainter.RenderHint.Antialiasing)
            rad = cell * 0.48
            is_black_turn = (self.turn == "B")
            best_sl_raw = visible[0].score_lead
            sl_best = best_sl_raw if is_black_turn else -best_sl_raw
            text_pen = QPen(QColor(0x22, 0x22, 0x22, 255))
            for rank, mp in enumerate(visible_pairs):
                mi, pos = mp
                col, row = pos
                x, y = self._xy(col, row)

                ratio = mi.visits / max_visits if max_visits > 0 else 0.0
                t = max(0.0, min(1.0, 1.0 - ratio))

                if rank == 0:
                    r_ch, g_ch, b_ch = 0x3d, 0x8e, 0xf0
                    alpha = 245
                else:
                    r_ch, g_ch, b_ch = 0x28, 0xa8, 0x5e
                    alpha = int(245 - t * 65)
                fc = QColor(r_ch, g_ch, b_ch)
                fc.setAlpha(alpha)
                outline_alpha = int(120 - t * 40)
                pp.setPen(QPen(QColor(0, 0, 0, outline_alpha), 1))
                pp.setBrush(QBrush(fc))
                pp.drawEllipse(QPointF(x, y), rad, rad)

                if rank == 0:
                    num_str = "\u2605"
                    font_size = max(8, int(cell * 0.50))
                else:
                    sl_this = mi.score_lead if is_black_turn else -mi.score_lead
                    delta = sl_this - sl_best
                    rounded = round(delta, 1)
                    if abs(rounded) < 1e-9:
                        num_str = "\u00b10.0"
                    else:
                        sign = "+" if rounded > 0 else ""
                        num_str = f"{sign}{rounded:.1f}"
                    font_size = max(8, int(cell * 0.38))
                pp.setPen(text_pen)
                pp.setFont(F(font_size, True))
                fm = pp.fontMetrics()
                num_w = fm.horizontalAdvance(num_str)
                num_h = fm.ascent()
                nx = x - num_w / 2
                base_y = y + num_h * 0.35
                pp.drawText(QPointF(nx, base_y), num_str)
            pp.end()
            self._hints_pm = pix
            self._hints_pm_key = cache_key
        return True

    def _hints(self, p):
        """候補手描画。pixmap キャッシュを使い、candidates 値が同じ間は drawPixmap のみ。"""
        if not self._ensure_hints_pixmap():
            return
        p.drawPixmap(0, 0, self._hints_pm)

    # ── 石 pixmap キャッシュ(_stones の高速化用) ────────────────
    # 1石ずつグラデーション計算 + drawEllipse を3回 + setBrush/setPen を行うと、
    # 200手以上の局面で 22ms/フレームかかってしまう(計測実測値)。
    # 黒/白の石 pixmap を事前生成しておき、_stones は drawPixmap のみのループに
    # することで描画コストを 1/10 以下に削減する。
    # キャッシュキーは (rad, dpr) のタプル。セルサイズ変更や DPI 変更で再生成。
    @staticmethod
    def _build_stone_pixmap(color: str, rad: float, dpr: float):
        """指定色・半径の石 pixmap を生成する(影 + グラデーション本体 +
        ハイライトを含む立体描画)。flip アニメ用と通常描画用の両方から呼ばれる。

        引数:
            color: 'B' または 'W'
            rad:   石の半径(論理ピクセル)
            dpr:   devicePixelRatio
        戻り値:
            (pixmap, pad)
              pad = 影とハイライトの余白(中心から pixmap 端までの余裕)。
              pixmap の論理サイズは (rad*2 + pad*2)。
              pixmap 中心が (rad+pad, rad+pad) の位置にある。
        """
        from PyQt6.QtGui import QPixmap
        # 影が pixmap 端からはみ出さないよう余白を確保。
        # 影は (rad*0.12, rad*0.15) 方向に rad*0.95 の半径で描画されるので、
        # 片側 pad = rad * 0.20 程度が必要。
        pad = rad * 0.20
        sz_logical = rad * 2.0 + pad * 2.0
        sz_phys = int(sz_logical * dpr) + 1  # +1 で端のクリップを防ぐ
        pm = QPixmap(sz_phys, sz_phys)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        sp = QPainter(pm)
        sp.setRenderHint(QPainter.RenderHint.Antialiasing)
        # pixmap 内のローカル中心(石本体の中心位置)
        cx_local = sz_logical / 2.0
        cy_local = sz_logical / 2.0
        # 影
        sp.setBrush(QBrush(QColor(0, 0, 0, 50)))
        sp.setPen(Qt.PenStyle.NoPen)
        sp.drawEllipse(QPointF(cx_local + rad*0.12, cy_local + rad*0.15),
                       rad*0.95, rad*0.95)
        # 本体(色によって異なるグラデーション)
        if color == "B":
            gr = QRadialGradient(cx_local - rad*0.35, cy_local - rad*0.35, rad * 1.2)
            gr.setColorAt(0.0, QColor("#787878"))
            gr.setColorAt(0.25, QColor("#1a1a1a"))
            gr.setColorAt(1.0, QColor("#000000"))
            sp.setBrush(QBrush(gr))
            sp.setPen(QPen(QColor("#000000"), 0.5))
        else:
            gr = QRadialGradient(cx_local - rad*0.35, cy_local - rad*0.35, rad * 1.2)
            gr.setColorAt(0.0, QColor("#ffffff"))
            gr.setColorAt(0.4, QColor("#f0f0f0"))
            gr.setColorAt(1.0, QColor("#c8c8c8"))
            sp.setBrush(QBrush(gr))
            sp.setPen(QPen(QColor("#c0bbb5"), 0.8))
        sp.drawEllipse(QPointF(cx_local, cy_local), rad, rad)
        # 小さなハイライト(光の反射)
        if color == "B":
            hl = QRadialGradient(cx_local - rad*0.3, cy_local - rad*0.35, rad*0.45)
            hl.setColorAt(0.0, QColor(255, 255, 255, 100))
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            sp.setBrush(QBrush(hl)); sp.setPen(Qt.PenStyle.NoPen)
            sp.drawEllipse(QPointF(cx_local - rad*0.15, cy_local - rad*0.2),
                           rad*0.4, rad*0.35)
        else:
            hl = QRadialGradient(cx_local - rad*0.3, cy_local - rad*0.35, rad*0.4)
            hl.setColorAt(0.0, QColor(255, 255, 255, 180))
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            sp.setBrush(QBrush(hl)); sp.setPen(Qt.PenStyle.NoPen)
            sp.drawEllipse(QPointF(cx_local - rad*0.15, cy_local - rad*0.2),
                           rad*0.35, rad*0.3)
        sp.end()
        return pm, pad

    def _ensure_stone_pixmaps(self, rad: float, dpr: float):
        """_stones 用の石 pixmap キャッシュを必要に応じて再生成する。
        cell サイズや DPI が変わった時のみ再生成し、それ以外は流用する。"""
        cache_key = (round(rad, 2), round(dpr, 3))
        if getattr(self, "_stone_pm_key", None) == cache_key \
                and getattr(self, "_stone_pm_b", None) is not None \
                and getattr(self, "_stone_pm_w", None) is not None:
            return  # キャッシュヒット: 何もしない
        # 再生成
        pm_b, pad = self._build_stone_pixmap("B", rad, dpr)
        pm_w, _   = self._build_stone_pixmap("W", rad, dpr)
        self._stone_pm_b = pm_b
        self._stone_pm_w = pm_w
        self._stone_pm_pad = pad
        self._stone_pm_rad = rad
        self._stone_pm_key = cache_key

    def _stones(self, p):
        """碁石を描画する(立体的、画面左上に光源を仮定)。
        反転アニメ中は呼ばれない(代わりに事前生成した石 pixmap を回転後の
        位置に貼り付ける方式で描画され、光源は世界座標で固定になる)。

        高速化: 黒石・白石それぞれ pixmap を1回だけ生成してキャッシュし、
        本メソッドでは drawPixmap でループ貼り付けするだけ。
        cell サイズ変更時のみ自動的に再生成される。
        """
        if not self.stones:
            return
        cell = self._cell()
        rad = cell * 0.48
        dpr = self.devicePixelRatioF()
        self._ensure_stone_pixmaps(rad, dpr)
        pm_b = self._stone_pm_b
        pm_w = self._stone_pm_w
        offset = rad + self._stone_pm_pad
        # SmoothPixmapTransform はサブピクセル位置でも滑らかに描画される
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for (col, row), color in self.stones.items():
            x, y = self._xy(col, row)
            pm = pm_b if color == "B" else pm_w
            # 石中心が (x, y) になるよう pixmap 左上を offset 分シフト
            p.drawPixmap(QPointF(x - offset, y - offset), pm)

        # 最終手の悪手バッジ描画は paintEvent 最後の _last_move_badge() で
        # 独立して行う。次手リング(_next_moves_overlay_top) や候補手などすべての
        # オーバーレイより前面に出すため、ここでは描画しない。

    def _build_mark_pixmap(self, rad: float, mark_r: float, line_w: float, line_color: QColor, dpr: float):
        """最終手リング用の pixmap を1枚生成する。

        重要: pixmap の論理サイズと pad は石本体 (_build_stone_pixmap) と
        完全に同じ値を使う。これにより Qt の drawPixmap で配置される矩形
        サイズが石本体と一致し、サブピクセル整列ルールも一致するため、
        どの座標でも石中心とリング中心が必ず重なる。
          石: sz_logical = rad*2 + (rad*0.20)*2,  pad = rad*0.20
          → リングも同じ値を使い、pixmap 内の中心 (cx_local, cy_local) に
            mark_r のリングを描画する。
        """
        from PyQt6.QtGui import QPixmap
        # 石本体と完全に同じパディングと論理サイズ
        pad = rad * 0.20
        sz_logical = rad * 2.0 + pad * 2.0
        sz_phys = int(sz_logical * dpr) + 1
        pm = QPixmap(sz_phys, sz_phys)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        sp = QPainter(pm)
        sp.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx_local = sz_logical / 2.0
        cy_local = sz_logical / 2.0
        pen = QPen(line_color, line_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        sp.setPen(pen)
        sp.setBrush(Qt.BrushStyle.NoBrush)
        sp.drawEllipse(QPointF(cx_local, cy_local), mark_r, mark_r)
        sp.end()
        return pm, pad

    def _ensure_mark_pixmaps(self, rad: float, dpr: float):
        """最終手リング用の pixmap キャッシュを必要に応じて再生成。
        cell サイズや DPI が変わった時のみ再生成し、それ以外は流用する。
        黒線(白石上)用と白線(黒石上)用を別々にキャッシュ。
        """
        cache_key = (round(rad, 2), round(dpr, 3))
        if getattr(self, "_mark_pm_key", None) == cache_key \
                and getattr(self, "_mark_pm_white", None) is not None \
                and getattr(self, "_mark_pm_black", None) is not None:
            return
        mark_r = rad * 0.40
        line_w = max(1.0, rad * 0.08)
        # 黒石上に描く白いリング
        pm_white, pad = self._build_mark_pixmap(rad, mark_r, line_w, QColor("#ffffff"), dpr)
        # 白石上に描く黒いリング
        pm_black, _   = self._build_mark_pixmap(rad, mark_r, line_w, T().STONE_BLACK, dpr)
        self._mark_pm_white = pm_white
        self._mark_pm_black = pm_black
        self._mark_pm_pad = pad
        self._mark_pm_key = cache_key

    def _last_move_mark(self, p):
        """直前の手の石の中心に枠線のみの円を描画する。
        伝統的な棋譜の「最終手マーカー」として使われる表現。
        黒石上は白線、白石上は黒線で描画して視認性を確保する。

        メニュー「表示 > 最後の手をマーク」で ON/OFF (デフォルト OFF)。
        評価バッジ (_last_move_badge: 右上の○△✕) とは独立で、両方
        同時に有効でも併存可能。

        実装メモ: pixmap の論理サイズ・pad を石本体と完全に揃え、
        drawPixmap で同じ矩形位置に貼り付ける。これにより Qt の内部
        rounding ルールが石とリングで完全一致し、どの座標でも中心
        ずれが発生しない。
        """
        if not (self.show_last_move_mark and self.last_move):
            return
        col, row = self.last_move
        if not (0 <= col < self.board_size and 0 <= row < self.board_size):
            return
        stone_color = self.stones.get((col, row))
        if stone_color is None:
            return  # 石が無い座標(取られた等): 何も描画しない

        x, y = self._xy(col, row)
        cell = self._cell()
        rad = cell * 0.48  # _stones() と同じ石半径
        dpr = self.devicePixelRatioF()
        self._ensure_mark_pixmaps(rad, dpr)
        pm = self._mark_pm_white if stone_color == "B" else self._mark_pm_black
        # 石本体と完全に同じ offset 計算 (rad + pad = rad + rad*0.20 = rad*1.20)
        offset = rad + self._mark_pm_pad
        p.drawPixmap(QPointF(x - offset, y - offset), pm)

    def _last_move_badge(self, p):
        """直前の手の右上に評価バッジ(○ △ ✕)を描画する。
        他のオーバーレイ(候補手・次手リング等)よりも上に重なるよう、
        paintEvent の最後で呼び出される。"""
        if not (self.show_badges and self.last_move):
            return
        cat = self.blunder.category if self.blunder else None
        if cat is None:
            return  # 未解析: 何も表示しない

        col, row = self.last_move
        x, y = self._xy(col, row)
        cell = self._cell()
        rad = cell * 0.48  # _stones() と同じ石半径

        # バッジ中心: 石の右上
        badge_r = rad * 0.46
        bx = x + rad * 0.72
        by = y - rad * 0.72

        # ── 解析済み: 右上に評価色バッジ ──────────────────────
        badge_color = self._get_last_move_color()

        # 白い縁取りリング（盤面サイズに比例した太さ）
        ring_w = max(1.0, badge_r * 0.2)  # badge_r の 20%（最低1px）
        p.setBrush(QBrush(QColor("#ffffff")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(bx, by), badge_r + ring_w, badge_r + ring_w)

        # バッジ本体
        p.setBrush(QBrush(badge_color))
        p.drawEllipse(QPointF(bx, by), badge_r, badge_r)

        # アイコン（常に白で描画 — バッジ色は十分暗い or 白が映える色）
        icon_pen_color = QColor("#ffffff")
        _stroker = QPainterPathStroker()
        _stroker.setWidth(badge_r * 0.34)
        _stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        _stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(icon_pen_color))

        if cat in ("best", "good"):
            # 〇 円アウトライン
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(icon_pen_color, badge_r * 0.22,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawEllipse(QPointF(bx, by), badge_r * 0.52, badge_r * 0.52)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(icon_pen_color))
        elif cat in ("mistake", "inaccuracy"):
            # △ アウトラインのみ（〇・×とテイストを統一）
            import math
            tri_h = badge_r * 0.94
            tri_w = tri_h / math.sqrt(3)
            offset = badge_r * 0.06  # 視覚的重心補正：少し下にずらす
            tri_top  = by - tri_h * 2 / 3 + offset
            tri_base = by + tri_h * 1 / 3 + offset
            triangle = QPainterPath()
            triangle.moveTo(bx,         tri_top)
            triangle.lineTo(bx + tri_w, tri_base)
            triangle.lineTo(bx - tri_w, tri_base)
            triangle.closeSubpath()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(icon_pen_color, badge_r * 0.20,
                          Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(triangle)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(icon_pen_color))
        else:
            # ✗ バツ (blunder のみ)
            _stroker.setWidth(badge_r * 0.22)
            d = badge_r * 0.44
            cross = QPainterPath()
            cross.moveTo(bx - d, by - d)
            cross.lineTo(bx + d, by + d)
            cross.moveTo(bx + d, by - d)
            cross.lineTo(bx - d, by + d)
            p.drawPath(_stroker.createStroke(cross))

    def _draw_next_move_circle(self, p, x, y, rad, color, is_main, show_center=True):
        """次の手の円を描画する共通ヘルパー。
        - メイン: 外側輪郭円 ＋ 中央に石色の塗りつぶし円（show_center=Trueの場合）
        - サブ  : 外側輪郭のみ
        """
        border_color = QColor("#1a1a1a") if color == "B" else QColor("#ffffff")
        border_color.setAlpha(235)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(border_color, 2.0))
        p.drawEllipse(QPointF(x, y), rad, rad)
        if is_main and show_center:
            stone_c = QColor("#1a1a1a") if color == "B" else QColor("#ffffff")
            stone_c.setAlpha(235)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(stone_c))
            p.drawEllipse(QPointF(x, y), rad * 0.4, rad * 0.4)

    def _next_moves_overlay(self, p):
        """次の手・分岐のうち、候補手と重ならない位置を描画する。
        候補手と重なる位置のリングは _next_moves_overlay_top で描画する（hints の上）。
        """
        if not self.next_moves: return
        cell = self._cell()
        rad = cell * 0.48
        candidate_positions = self._candidate_positions() if self.show_hints else set()

        for col, row, color, is_main in self.next_moves:
            if (col, row) in candidate_positions:
                continue
            x, y = self._xy(col, row)
            self._draw_next_move_circle(p, x, y, rad, color, is_main=is_main)

    def _next_moves_overlay_top(self, p):
        """候補手と重なる次の手の輪郭リングを描画する（候補手の上に重ねる）。"""
        if not self.next_moves: return
        cell = self._cell()
        rad = cell * 0.48
        candidate_positions = self._candidate_positions()

        for col, row, color, is_main in self.next_moves:
            if (col, row) not in candidate_positions:
                continue
            x, y = self._xy(col, row)
            self._draw_next_move_circle(p, x, y, rad, color, is_main=is_main, show_center=False)

    def _candidate_positions(self) -> set:
        """実際に盤面に描画される候補手の座標セットを返す。
        _hints と同じ visit_threshold フィルタを適用する。
        """
        if not self.candidates:
            return set()
        total_visits = sum(mi.visits for mi in self.candidates)
        visit_threshold = max(1, total_visits * 0.01)
        positions = set()
        for mi in self.candidates:
            if mi.visits < visit_threshold:
                continue
            pos = sgf_coord_to_pos(self._gtp2sgf(mi.move))
            if pos:
                positions.add(pos)
        return positions

    def _get_last_move_color(self) -> QColor:
        """直前の手の評価色を返す。未解析はグレー。"""
        if self.blunder:
            c = QColor(T().BLUNDER.get(self.blunder.category, T().TEXT2))
        else:
            c = QColor(T().TEXT2)  # 未解析: グレー
        return c

    def _move_number_overlay(self, p):
        """盤上の石に手順番号を描画する。

        パフォーマンス対策: 全手順番号を 1 枚の pixmap (盤面全体サイズ) に
        事前レンダリングし、paintEvent では drawPixmap 1 回だけで終わら
        せる。手順番号は最大 300 個程度になることがあり、毎フレーム
        QPainterPath で描画すると 30ms 程度かかる(60fps が破綻する)が、
        pixmap キャッシュなら ~1ms で済む。

        中央揃え方式: 各数字を QPainterPath にベクター変換して
        boundingRect 中央が石中心に来るよう描画する。drawText のグリフ
        別ヒンティング差(列によって 1px 程度ずれる現象)を回避する。

        キャッシュキー: move_numbers の内容、cell サイズ、テーマ識別、
        dpr の組。 1つでも変われば再レンダリング。
        """
        if not self.move_numbers:
            return
        from PyQt6.QtGui import QPainterPath, QPixmap
        cell = self._cell()
        dpr = self.devicePixelRatioF()
        # キャッシュキー
        # move_numbers は dict なので順序固定の tuple に変換してハッシュ可能に
        mn_key = tuple(sorted(self.move_numbers.items()))
        # 石配置(stones)も一緒に固定: 同じ手順番号でも石位置が違うと
        # スキップ判定が変わるため。
        stones_key = tuple(sorted(self.stones.items()))
        theme_id = (id(T()), T().is_dark)
        # 盤面サイズ
        W = self.width()
        H = self.height()
        cache_key = (mn_key, stones_key, round(cell, 2),
                     round(dpr, 3), theme_id, W, H,
                     self._render_flipped())

        if getattr(self, "_mn_pm_key", None) != cache_key \
                or getattr(self, "_mn_pm", None) is None:
            # 再レンダリング
            font_size = max(6, int(cell * 0.44))
            f = F(font_size, True)
            sz_phys = (max(1, int(W * dpr)), max(1, int(H * dpr)))
            pm = QPixmap(sz_phys[0], sz_phys[1])
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pm)
            pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pp.setPen(Qt.PenStyle.NoPen)
            brush_white = QBrush(QColor("#ffffff"))
            brush_black = QBrush(QColor("#1a1a1a"))
            for (col, row), num in self.move_numbers.items():
                color = self.stones.get((col, row))
                if color is None:
                    continue
                x, y = self._xy(col, row)
                label = str(num)
                path = QPainterPath()
                path.addText(0, 0, f, label)
                br = path.boundingRect()
                path.translate(x - (br.left() + br.width() / 2),
                               y - (br.top() + br.height() / 2))
                pp.setBrush(brush_white if color == "B" else brush_black)
                pp.drawPath(path)
            pp.end()
            self._mn_pm = pm
            self._mn_pm_key = cache_key

        # キャッシュ済み pixmap を盤面に貼り付け (常に左上 (0, 0) から)
        p.drawPixmap(0, 0, self._mn_pm)

    def _gtp2sgf(self, gtp):
        if not gtp or gtp.lower()=="pass": return ""
        try:
            ci=COLS.index(gtp[0].upper()); ri=self.board_size-int(gtp[1:])
            return pos_to_sgf_coord(ci,ri)
        except: return ""

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            ox,oy=self._orig(); c=self._cell()
            col=round((ev.position().x()-ox)/c)
            row=round((ev.position().y()-oy)/c)
            if 0<=col<self.board_size and 0<=row<self.board_size:
                # 盤面反転中: 画面上のマス位置 → 論理座標へ逆変換
                # (180度回転は対合なので _flip_pos を再度かけるだけでよい)
                col, row = self._flip_pos(col, row)
                self.stone_clicked.emit(col,row)
        elif ev.button() == Qt.MouseButton.RightButton:
            self.stone_clicked.emit(-1, -1)  # 右クリック: 1手戻るシグナル



# ── Board container (centers board as square) ───────────────────────────────
class BoardContainer(QWidget):
    """碁盤を常に正方形・中央配置するコンテナ。

    棋譜ファイル(SGF) のドラッグ&ドロップ受付は MainWindow 側で
    画面全体に対して行うため、本コンテナでは D&D は扱わない。
    """

    def __init__(self, board: BoardWidget):
        super().__init__()
        self._board = board
        self._board.setParent(self)
        self.setStyleSheet(f"background:{T().BG.name()};")

    def resizeEvent(self, ev):
        # BoardWidget はコンテナ全体を使う（描画は BoardWidget 内部で中央寄せ）
        self._board.setGeometry(0, 0, self.width(), self.height())
