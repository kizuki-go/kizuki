# Kizuki ビルド手順

## 前提条件

- Windows 10/11（Windowsでビルドする必要があります）
- Python 3.10 以上
- 依存パッケージがインストール済み（`pip install -r requirements.txt`）

---

## 1. PyInstaller のインストール

```
pip install pyinstaller
```

---

## 2. ビルド実行

プロジェクトルート（`kizuki.spec` があるフォルダ）で以下を実行します。

```
pyinstaller kizuki.spec
```

初回は数分かかります。完了すると以下のフォルダが生成されます。

```
dist/
└── Kizuki/          ← これが配布物
    ├── Kizuki.exe
    ├── gui/
    ├── sounds/
    ├── katago/
    └── ...（依存ファイル多数）
```

---

## 3. 動作確認

`dist/Kizuki/Kizuki.exe` をダブルクリックして起動を確認します。

チェックポイント:
- [ ] スプラッシュ画面が表示される
- [ ] KataGoが起動してメイン画面が開く
- [ ] SGFファイルをドラッグ&ドロップで開ける
- [ ] 解析結果（候補手・形勢グラフ）が表示される
- [ ] 効果音が鳴る

---

## 4. zipにまとめて配布

動作確認が取れたら `dist/Kizuki/` フォルダをzipに圧縮します。

```
# PowerShellの場合
Compress-Archive -Path dist\Kizuki -DestinationPath Kizuki_v1.0.0.zip
```

このzipをGitHub ReleasesにアップロードしてLPのダウンロードリンクに設定します。

---

## トラブルシューティング

### 起動時に「DLLが見つからない」エラーが出る場合

`kizuki.spec` の `binaries` に不足しているDLLを追加してください。

```python
binaries=[
    ('path/to/missing.dll', '.'),
],
```

### pyqtgraph関連のエラーが出る場合

`hiddenimports` にモジュールを追加してください。

```python
hiddenimports=[
    'pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyqt6',
    'pyqtgraph.graphicsItems.PlotItem.plotConfigTemplate_pyqt6',
],
```

### KataGoが起動しない場合

`katago/` フォルダ内のファイルが `dist/Kizuki/katago/` に正しくコピーされているか確認してください。
`kizuki.spec` の `datas` セクションのパスが正しいかも確認してください。

---

## アイコンを設定する場合

1. `gui/assets/icon.ico` を用意する（256x256推奨）
2. `kizuki.spec` の以下の行のコメントを外す

```python
# icon='gui/assets/icon.ico',  # .icoファイルがあれば有効化
↓
icon='gui/assets/icon.ico',
```
