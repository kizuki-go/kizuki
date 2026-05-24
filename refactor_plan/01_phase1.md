# Phase 1: デッドコード削除

## 目的

リファクタ前にコードベースから「呼ばれていない」「呼ばれても何もしない」「死に分岐」を取り除く。これをフェーズ1で行うことで、後のフェーズでの「これも移すべき?」という判断を減らす。

## 作業前提

- 作業ブランチを切る: `git checkout -b refactor/phase1-dead-code`
- 起動確認: 作業前に必ず一度アプリを起動して、ベースラインの挙動を確認する
- 行番号は本計画書執筆時点(2026年5月)のもの。実際の作業時には `grep -n` で再確認すること

## 削除対象一覧

### 1. InfoPanel 内の死メソッド (L3675-3745 周辺)

**削除するもの**:

```python
def update_summary(self, analyses):
    pass

def set_katago_status(self, connected: bool):
    pass

def get_comment(self) -> str:
    return ""
```

**確認手順**:
```bash
# 呼出元を全検索 — 自身の定義行以外にヒットしないことを確認
grep -n "update_summary\|set_katago_status\|\.get_comment(" gui/main_window.py
```

呼び出し元が定義行のみ(または存在しない)であることを確認してから削除。

**追加で削除**: `__init__` 内の `self._wr_bar = self._scoreboard` (誰も参照していない)

```bash
grep -n "_wr_bar" gui/main_window.py
# → L3705 (定義) のみヒットすればOK
```

---

### 2. MoveInfoCard 内の死メソッドと死分岐 (L4367-5003 周辺)

**削除するもの (空メソッド)**:

```python
def set_comment_text(self, text: str):
    pass

def get_comment_text(self) -> str:
    return ""
```

**削除するもの (apply_theme 内の死分岐 L4925-4928)**:

```python
# コメント欄のスタイル更新
if hasattr(self, "_comment_btn"):
    has_comment = bool(self.get_comment_text().strip())
    self._update_comment_btn_style(has_comment)
```

このブロックの問題点:
- `_comment_btn` は MoveInfoCard 内で一度も作られていない (`__init__` にもなし) → `hasattr` 常に False
- 仮に True になっても呼び出す `_update_comment_btn_style` の定義が存在しない (AttributeError)
- コメントボタンのスタイル更新は `NavBar._apply_comment_btn_style` (L7457, L7507, L7633) が担当している

**確認手順**:
```bash
# _comment_btn が MoveInfoCard 内では使われていないことを確認
grep -n "_comment_btn" gui/main_window.py
# → L4926, L4928 (両方とも MoveInfoCard.apply_theme 内 = 削除対象) + L7457, L7507, L7633 (NavBar 内 = 残す) が出るはず

# _update_comment_btn_style が定義されていないことを確認
grep -n "def _update_comment_btn_style" gui/main_window.py
# → 何もヒットしないはず

# get_comment_text の呼出元
grep -n "get_comment_text" gui/main_window.py
# → 定義行 + apply_theme 内の呼出のみのはず(両方とも削除対象)
```

---

### 3. BoardWidget._blunder_ring (L3027)

```python
def _blunder_ring(self, p):
    pass
```

**重要**: このメソッドには呼出元がある (`L1860: self._blunder_ring(p)`)。

呼出元も同時に削除する。L1860 周辺の paint パイプライン(`_paint_board_inner` 内)を grep して、

```bash
grep -n "_blunder_ring" gui/main_window.py
# L1860 と L3027 の2箇所がヒットする
```

L1860 の `self._blunder_ring(p)` 行と L3027 の定義の両方を削除。

---

### 4. NavBar.update_move_label (L7611)

```python
def update_move_label(self, cur, total):
    pass
```

```bash
grep -n "update_move_label" gui/main_window.py
# 定義行のみがヒットすることを確認
```

定義のみ削除。呼出元なし。

---

### 5. _FirstLaunchRankDialog.reject (L10232)

```python
def reject(self):
    pass
```

これは `QDialog.reject` のオーバーライドで、本来は ESC キー押下時の動作を無効化するもの。**ただし pass のみだと「ESC が効かない」という意図的な挙動になる可能性がある**ため、削除する前に確認が必要。

**判断**: 初回起動ダイアログは「棋力を選ばないと進めない」ためのものなので、ESC 無効化は意図的な可能性が高い。

**結論**: **このメソッドは削除しない**。代わりに、なぜ pass なのかをコメントで明示する:

```python
def reject(self):
    # ESC キーや × ボタンでの閉じる操作を無効化する。
    # このダイアログは棋力選択が必須のため、選択完了まで閉じられない設計。
    pass
```

---

## 検証チェックリスト

- [ ] `git status` で変更ファイルが `gui/main_window.py` のみ
- [ ] `python -m py_compile gui/main_window.py` がエラーなく通る
- [ ] `python -c "import gui.main_window"` がエラーなく通る
- [ ] アプリ起動 → SGF読込 → 数手進める → 戻る → テーマ切替 → 終了 が全て成功
- [ ] 起動時の初回ランクダイアログが正しく表示される(`QSettings("Kizuki", "Kizuki").remove("player_rank")` で初回状態にしてテスト)
- [ ] 初回ランクダイアログで ESC キーが効かない (= 5番目の判断通り)
- [ ] 削除した行数: 約 30〜40 行(MoveInfoCard の死分岐 + 各空メソッド)

## コミット

```bash
git add gui/main_window.py
git commit -m "refactor: remove dead code in InfoPanel/MoveInfoCard/BoardWidget

- InfoPanel: remove unused update_summary/set_katago_status/get_comment
- InfoPanel: remove unused self._wr_bar attribute
- MoveInfoCard: remove empty set_comment_text/get_comment_text
- MoveInfoCard: remove dead branch in apply_theme (_comment_btn never created)
- BoardWidget: remove empty _blunder_ring method and its call site
- NavBar: remove empty update_move_label
- _FirstLaunchRankDialog.reject: add comment explaining intentional pass"
```

## 次フェーズへ

完了したら `02_phase2.md` へ。
