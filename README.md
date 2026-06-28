# Kizuki — 無料で使える囲碁AI解析ソフト

KataGo を用いたデスクトップ向けの囲碁棋譜解析ソフトです。SGF 形式の棋譜を読み込み、形勢推移・悪手判定・候補手をリアルタイムに表示します。

---

## 動作環境

- OS: Windows 10 / 11
- Python: 3.10 以上
- GPU: OpenCL 対応（NVIDIA / AMD / Intel）

---

## ソースからの起動

依存パッケージをインストールして起動します。

```
pip install PyQt6 pyqtgraph
py gui\startup.py
```

初回起動時は KataGo が GPU の自動チューニングを行うため、数分〜10分ほどかかります。チューニング結果はキャッシュされ、2回目以降は数秒で起動します。

---

## ディレクトリ構成

```
kizuki/
├── Kizuki.bat            # 起動用バッチ
├── build.bat             # PyInstaller クリーンビルド
├── kizuki.spec           # PyInstaller 設定
├── requirements.txt
├── test_core.py          # SGFパーサー回帰テスト
├── core/                 # KataGoエンジン制御・棋譜パーサ・解析ロジック
├── gui/                  # GUI レイヤ (PyQt6)
│   ├── startup.py        # エントリポイント
│   ├── main_window.py    # MainWindow (6 Mixin 合成)
│   ├── infra.py          # get_base_dir / SoundPlayer / Toast 等
│   ├── theme.py          # 配色・UI定数
│   ├── fonts.py          # フォントユーティリティ
│   ├── icons.py          # SVG生成・アイコン
│   ├── dialogs.py        # 各種ダイアログ
│   ├── menus.py          # メニューユーティリティ
│   ├── widgets/          # UIコンポーネント
│   └── _mixins/          # MainWindow を構成する Mixin 群
├── katago/
│   ├── katago.exe        # KataGo 本体（OpenCL版）
│   ├── analysis.cfg      # 解析設定
│   └── models/           # モデルファイル（.bin.gz）
└── sounds/               # 効果音
```

---

## ライセンス

- 本プロジェクト: MIT License
- KataGo 本体（`katago/katago.exe` ほか）: Apache License 2.0
  - https://github.com/lightvector/KataGo
- KataGo ニューラルネットワーク（`*.bin.gz`）: KataGo Networks の配布条件に従う
  - https://katagotraining.org/networks/
- 効果音（`sounds/`）: On-Jin ～音人～
  - https://on-jin.com/
