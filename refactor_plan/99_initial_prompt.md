# Claude Code への初期プロンプト

このファイルは、Claude Code で作業を始める時に **最初に投げる** プロンプトです。
コピーして Claude Code セッションの先頭で送ってください。

---

## プロンプト本文(ここから下をコピー)

これから Kizuki(PyQt6 製 KataGo 連携の囲碁 AI 解析アプリ)のリファクタリングを行います。

### 大原則(必ず守ること)

1. **動作を変えない**。UI 表示・操作感・KataGo 連携の挙動は完全に同一を保つ。リファクタは構造変更のみ。
2. **1フェーズずつ git commit する**。各フェーズ完了時にコミット → 動作確認 → 次フェーズ。フェーズを跨いで一気に変更しない。
3. **検証を欠かさない**。各フェーズに「完了条件チェックリスト」がある。これを全て満たさない限り次へ進まない。
4. **不確実なときは止まる**。判断に迷う変更が出たら、その場で勝手に決めず、ユーザーに確認する。
5. **scope creep を避ける**。「ついでに直したい」が湧いてもこの計画書に書かれていない変更はしない。

### 作業フォルダ

リポジトリのルートは現在のディレクトリ。以下の構造を前提とする:

```
.
├── gui/
│   └── main_window.py   (16,482行 — リファクタ対象)
├── core/                 (健全・変更しない)
│   ├── analyzer.py
│   ├── game_state.py
│   ├── katago_engine.py
│   └── sgf_parser.py
├── katago/               (KataGo バイナリ・モデル)
├── sounds/
└── refactor_plan/        (このフォルダに計画書一式)
    ├── 00_REFACTOR_PLAN.md       (全体計画 — 最初に必ず読む)
    ├── 01_phase1.md              (デッドコード削除)
    ├── 02_phase2.md              (基盤レイヤ抽出)
    ├── 03_phase3.md              (UIコンポーネント抽出)
    ├── 04_phase4.md              (dialogs/menus/startup)
    ├── 05_phase5.md              (Mixin 型補助)
    ├── 06_phase6.md              (Mixin 分割本番)
    ├── 07_phase7_and_08_phase8.md (hasattr整理と最終整理)
    └── 99_initial_prompt.md      (このファイル)
```

### 最初にやってほしいこと

以下の手順を **順番に** 実行してください:

1. `refactor_plan/00_REFACTOR_PLAN.md` を `view` で全て読む。これは全体計画書なので必読です。

2. `git status` を確認。ワーキングツリーが clean でなければユーザーに報告して、コミット or stash を提案。

3. `git checkout -b refactor/main` で作業ブランチを作成。

4. **ベースラインの動作確認をユーザーに依頼**:
   - 「リファクタ開始前に、アプリが正常起動して以下が動作することを確認してください: 起動、デモ局面表示、数手進める/戻る、SGF読込、テーマ切替、アプリ終了」
   - ユーザーから「動作確認OK」の返答を待つ。

5. `refactor_plan/01_phase1.md` を `view` で読む。

6. Phase 1 の作業を開始する。**1ファイル変更したら毎回**:
   - `python -m py_compile gui/main_window.py` で構文チェック
   - 構文OKを確認してから次の変更へ
   - Phase 1 全完了時に `python -c "import gui.main_window"` で import チェック
   - ユーザーに「Phase 1 完了。動作確認をお願いします」と依頼
   - ユーザー OK 後にコミット

### 各フェーズの進め方

- フェーズ N を始める時、まず `refactor_plan/0N_phaseN.md` を読む。
- 計画書の「Step」を順に実行する。
- 各 Step の「検証チェックリスト」を全項目クリアしてから次へ。
- **判断に迷ったら必ず質問する**。例:
  - 「この import は使われていないように見えるが、削除して良いか?」
  - 「この hasattr は別 Mixin の属性を参照しているが、フェーズ7まで残すべきか?」
  - 「セクションコメントの追加は後でも良いか?」

### 報告の形式

各 Step 完了時、以下の形式でユーザーに簡潔に報告してください:

```
## Phase X / Step Y 完了

### 変更内容
- (どのファイルを) (どう変えたか)

### 検証結果
- [x] 構文チェック
- [x] import チェック
- [ ] 動作確認(ユーザー依頼中)

### 次のアクション
- 動作確認をお願いします。OK でしたら commit します。
```

### バックアップ

念のため、Phase ごとに開始前に元の `main_window.py` のバックアップを取ってください:

```bash
mkdir -p .refactor_backup
cp gui/main_window.py .refactor_backup/main_window.py.phase_$N_start.bak
```

### 環境情報

- OS: Windows (ユーザー環境はデスクトップ PC, NVIDIA RTX3070)
- KataGo: OpenCL 版同梱
- モデル: b18c384nbt
- Python: 3.10+ 想定
- 主依存: PyQt6, pyqtgraph (オプション)

### コンセプト

Kizuki のコンセプトは「**誰でも簡単に使える**」「**エンジンの切り替えや一部のユーザーを切り捨てない**」。リファクタ後もこのコンセプトを破る変更はしないこと(例: NVIDIA 専用化、OpenCL 切り捨て、設定の複雑化など)。

---

それでは、最初のステップ「`refactor_plan/00_REFACTOR_PLAN.md` を view で読む」から始めてください。
