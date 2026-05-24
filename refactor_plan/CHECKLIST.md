# 最終チェックリスト

このファイルはリファクタリング全工程の **ユーザー側用** チェックリストです。各フェーズ完了時にここで進捗を管理してください。

---

## Phase 0: 準備

- [ ] `refactor_plan/` フォルダがプロジェクトルートに配置されている
- [ ] git のワーキングツリーが clean(未コミット変更がない)
- [ ] `git checkout -b refactor/main` で作業ブランチを作成済み
- [ ] アプリ起動 → 基本シナリオ完走 → ベースライン確認 OK
- [ ] Claude Code セッション開始 → `99_initial_prompt.md` の内容を投入

## Phase 1: デッドコード削除

- [ ] InfoPanel: `update_summary`, `set_katago_status`, `get_comment`, `_wr_bar` 削除
- [ ] MoveInfoCard: `set_comment_text`, `get_comment_text`, apply_theme 内の死分岐 削除
- [ ] BoardWidget: `_blunder_ring` と呼出元削除
- [ ] NavBar: `update_move_label` 削除
- [ ] `_FirstLaunchRankDialog.reject`: コメント追加(削除はしない)
- [ ] 構文OK / import OK / 起動OK / 全機能動作確認
- [ ] commit 済み

## Phase 2: 基盤レイヤ抽出

- [ ] `gui/theme.py` 作成 + 動作確認 + commit
- [ ] `gui/fonts.py` 作成 + 動作確認 + commit
- [ ] `gui/icons.py` 作成 + 動作確認 + commit
- [ ] `gui/infra.py` 作成 + 動作確認 + commit
- [ ] `gui/widgets/__init__.py` 作成
- [ ] `gui/widgets/common.py` 作成 + 動作確認 + commit
- [ ] `main_window.py` 行数が大幅減(本来 16,482 → 約 14,500)
- [ ] 全機能動作確認(テーマ切替・スライダ・サウンド)

## Phase 3: UIコンポーネント抽出

- [ ] `gui/widgets/board.py` (BoardWidget, BoardContainer) + 動作確認 + commit
- [ ] `gui/widgets/graph.py` (WinRateGraph 等) + 動作確認 + commit
- [ ] `gui/widgets/titlebar.py` (_CustomTitleBar) + 動作確認 + commit
- [ ] `gui/widgets/navbar.py` (NavBar 等) + 動作確認 + commit
- [ ] `gui/widgets/welcome.py` (WelcomePane 等) + 動作確認 + commit
- [ ] `gui/widgets/panels.py` (ScoreBoard, InfoPanel, MoveInfoCard 等) + 動作確認 + commit
- [ ] `gui/widgets/branchtree.py` (BranchTreeWidget) + 動作確認 + commit
- [ ] `main_window.py` 行数 ~7,000 になっている
- [ ] 全機能動作確認

## Phase 4: dialogs / menus / startup 抽出

- [ ] `gui/dialogs.py` 作成 + 動作確認 + commit
- [ ] `gui/menus.py` 作成 + 動作確認 + commit
- [ ] `gui/startup.py` 作成 + 動作確認 + commit
- [ ] 既存の起動方法でアプリが起動できる(必要なら起動スクリプト調整)
- [ ] `main_window.py` 行数 ~5,800 になっている
- [ ] 全ダイアログ動作確認

## Phase 5: Mixin 型補助モジュール作成

- [ ] `gui/_mixins/__init__.py` 作成
- [ ] `gui/_mixins/_types.py` 作成
- [ ] `MainWindowProto` 内に MainWindow の主要属性が宣言されている
- [ ] 構文チェック OK
- [ ] commit 済み

## Phase 6: MainWindow を Mixin に分割

- [ ] `gui/_mixins/theme_ctrl.py` 作成 + 動作確認 + commit
- [ ] `gui/_mixins/comments.py` 作成 + 動作確認 + commit
- [ ] `gui/_mixins/file_io.py` 作成 + 動作確認 + commit
- [ ] `gui/_mixins/window_mgmt.py` 作成 + 動作確認 + commit
- [ ] `gui/_mixins/engine_ctrl.py` 作成 + 動作確認 + commit
- [ ] `gui/_mixins/navigation.py` 作成 + 動作確認 + commit
- [ ] `MainWindow` クラス定義が 6 Mixin + `QMainWindow` 継承
- [ ] eventFilter / mousePressEvent 等は MainWindow 本体に残っている
- [ ] `main_window.py` 行数 ~1,000 になっている
- [ ] 全機能動作確認(特にイベント処理: キーボード・マウス・D&D)

## Phase 7: hasattr 整理

- [ ] `MainWindow.__init__` 冒頭に属性初期化ブロックがある
- [ ] `grep -c "hasattr(self" gui/main_window.py` が 20 以下
- [ ] `MoveInfoCard` の hasattr が大幅減
- [ ] 他クラスの hasattr も整理
- [ ] 全機能動作確認(遅延生成属性の動作: コメントオーバーレイ等)

## Phase 8: 最終整理

- [ ] 各ファイルの不要 import 削除
- [ ] import 順序統一(標準 → サードパーティ → core → gui)
- [ ] 各モジュール冒頭の docstring が充実
- [ ] セクションコメント追加(必要箇所のみ)
- [ ] README.md 更新(任意)
- [ ] 最終動作確認シナリオ全完走
- [ ] commit 済み

---

## 最終確認

リファクタリング完了の指標:

| 項目 | 目標 |
|---|---|
| `main_window.py` 行数 | ~1,000 |
| 最大ファイル行数 | ~1,900 |
| `MainWindow` メソッド数 | ~30 |
| `hasattr(self, ...)` 数 | <30 |
| 既知バグ・潜在バグ | 0(フェーズ1で除去) |
| 動作 | リファクタ前と完全一致 |

---

## 問題発生時

### ロールバック

リファクタ途中で取り返しのつかない問題が起きた場合:

```bash
# 直前のフェーズ完了時点に戻す
git reset --hard HEAD~1   # 直前のコミットを取り消し

# または全フェーズ取り消し(merge前)
git checkout main
git branch -D refactor/main
```

### Claude Code が暴走しそうな時

- "STOP" と入力して作業を止める
- 進捗を確認し、必要なら git reset
- 計画書のどこで詰まったかを把握して再開
