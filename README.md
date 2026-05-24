# Kizuki — 囲碁AI解析

KataGo を用いたデスクトップ向けの囲碁棋譜解析ソフトです。SGF 形式の棋譜を読み込み、形勢推移・悪手判定・候補手をリアルタイムに表示します。

- ライセンス: MIT
- プラットフォーム: Windows
- 必要環境: Python 3.10+ / PyQt6 / KataGo (Apache 2.0、同梱)

---

## 起動方法

同梱の `囲碁AI解析.bat` をダブルクリックするか、リポジトリのルートで以下を実行します。

```
py gui\main_window.py
```

初回起動時は KataGo が GPU の自動チューニングを行うため、数分〜10分ほどかかることがあります（特に高精度モデル選択時）。チューニング結果はキャッシュされ、2回目以降は数秒で起動します。

依存パッケージは PyQt6 と pyqtgraph です。

```
pip install PyQt6 pyqtgraph
```

---

## ディレクトリ構成

```
files/
├── 囲碁AI解析.bat        # 起動用バッチ
├── gui/
│   └── main_window.py    # メインGUI
├── core/                 # KataGoエンジン制御・棋譜パーサ・解析ロジック
├── katago/
│   ├── katago.exe        # KataGo本体（OpenCL版を同梱）
│   ├── analysis.cfg      # 解析エンジン設定
│   └── models/           # KataGoモデル（.bin.gz）を置く場所
├── fonts/                # 表示用フォント
├── sounds/               # 着手・打ち上げ効果音
└── demo.sgf              # 起動時に読み込まれるサンプル棋譜
```

---

## モデルの追加と切り替え

メニュー「AIモデル」から、使用するニューラルネットを切り替えられます。

### 同梱モデル

| 表示名 | ファイル名 | サイズ | 備考 |
|---|---|---|---|
| 標準 | `kata1-b18c384nbt-s9996604416-d4316597426.bin.gz` | 約98MB | 起動が速く、軽快に動作。デフォルト |
| 高精度 | `kata1-zhizi-b28c512nbt-muonfd2.bin.gz` | 約250MB | より強力なネット。GPU性能が必要 |

切り替えは即時再起動方式です。チューニング済みのモデルなら数秒、未チューニングのモデルは初回のみチューニングのため数分待ちが発生します。

### ユーザー定義モデルの追加

`katago/models/` フォルダに任意の `.bin.gz` モデルファイルを配置してアプリを起動すると、「AIモデル」メニューに自動的に追加されます。区切り線の下にファイル名（`.bin.gz` を除いたもの）で表示されます。

モデルは KataGo 公式のネットワーク配布ページから入手できます。

- https://katagotraining.org/networks/
- https://github.com/lightvector/KataGo（READMEに各種ネットへのリンクあり）

ただし、KataGo のバージョン（同梱は OpenCL 版）と互換性のあるネットワーク世代を選んでください。

---

## CUDA / TensorRT 版 KataGo への差し替え（上級者向け）

同梱の `katago.exe` は OpenCL 版です。OpenCL は対応 GPU が広く（NVIDIA / AMD / Intel）、特別な追加インストールが不要という利点がある一方、NVIDIA GPU では TensorRT 版のほうが大幅に高速になることが多いです。NVIDIA GPU 環境では、自分で `katago.exe` を差し替えて使うことができます。

### 大まかな手順

1. https://github.com/lightvector/KataGo/releases から、自分の環境に合わせたビルドをダウンロードする。
   - **TensorRT 版**: NVIDIA GPU 向け。`katago-vX.Y.Z-trt10.2.0-cuda12.5-windows-x64.zip` のように、リリース名にライブラリの対応バージョンが書かれているので、それと**完全に一致するバージョン**の CUDA Toolkit と TensorRT を NVIDIA から別途インストールする必要がある。
   - **CUDA 版**: NVIDIA GPU 向け。CUDA + cuDNN が必要。性能は TensorRT 版に劣ることが多いが、TensorRT より手軽。
   - **OpenCL 版**: 同梱版と同じ。差し替え不要。
   - **Eigen / Eigen-AVX2**: GPU を持たない環境向けの CPU 版。
2. ダウンロードした zip を展開し、`katago.exe` および**同梱の DLL 一式すべて**を、本リポジトリの `katago/` フォルダの中身と置き換える。DLL の差し替え漏れがあると起動に失敗します。
3. `katago/models/` 内のモデル（`.bin.gz`）はそのまま使えます（OpenCL/CUDA/TensorRT すべてで共通）。
4. 起動。初回は TensorRT 用のエンジンキャッシュ生成があり、数分かかります。

### 注意点

- **CUDA / TensorRT のバージョンは厳密に合わせてください**。例えば `trt10.2.0-cuda12.5` ビルドなら、TensorRT 10.2.x と CUDA 12.5.x をインストールします。バージョンがずれていると DLL ロード時にエラーになります。
- うまくいかない場合は、ダウンロードしておいた OpenCL 版に戻せばすぐ動作確認に戻れます。
- TensorRT 版は起動がやや遅い代わりに、解析速度（visits/秒）は OpenCL 版の 1.5〜2 倍以上になることがあります。

---

## ライセンス

- 本プロジェクト: MIT License
- KataGo 本体（`katago/katago.exe` ほか）: Apache License 2.0
  - https://github.com/lightvector/KataGo
- KataGo ニューラルネットワーク (`*.bin.gz`): KataGo Networks の配布条件に従う
  - 詳細は https://katagotraining.org/networks/ を参照
- 同梱フォント (`fonts/NotoSansJP*`, `fonts/NotoSansMono*`): SIL Open Font License 1.1
- Lizzie のコードは一切使用していません（設計の参考のみ）
