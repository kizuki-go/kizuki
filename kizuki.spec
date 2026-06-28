# -*- mode: python ; coding: utf-8 -*-
# Kizuki — PyInstaller spec ファイル
#
# ビルド手順:
#   pip install pyinstaller
#   pyinstaller kizuki.spec
#
# 成果物: dist/Kizuki/ フォルダ（そのままzipして配布）

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['gui/startup.py'],          # エントリポイント
    pathex=['.'],
    binaries=[],
    datas=[
        # SVGロゴ
        ('gui/assets/*.svg',       'gui/assets'),
        # 効果音
        ('sounds/*.wav',           'sounds'),
        # KataGoエンジン本体・DLL・設定ファイル
        ('katago/katago.exe',      'katago'),
        ('katago/*.dll',           'katago'),
        ('katago/*.cfg',           'katago'),
        ('katago/*.pem',           'katago'),
        # KataGoモデル
        ('katago/models/*.bin.gz', 'katago/models'),
    ],
    hiddenimports=[
        'PyQt6.QtSvg',
        'PyQt6.QtMultimedia',
        'pyqtgraph',
        'pyqtgraph.graphicsItems',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'pandas',
        'IPython',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Kizuki',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # コンソールウィンドウを非表示
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='gui/assets/icon.ico',  # .icoファイルがあれば有効化
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Kizuki',        # dist/Kizuki/ フォルダに出力
)
