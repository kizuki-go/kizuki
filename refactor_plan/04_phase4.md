# Phase 4: dialogs / menus / startup 抽出

## 目的

ダイアログ群、メニュー周辺、起動処理を専用モジュールに切り出す。これで `main_window.py` は `MainWindow` クラス本体だけになる。

## 抽出対象

| 新ファイル | 含めるクラス・関数 | 元の行範囲 | 行数 |
|---|---|---|---|
| `gui/dialogs.py` | ColorAdjustmentDialog, _WarningIconWidget, _UnsavedChangesDialog, _FirstLaunchRankDialog | L9026-10240 | ~1,210 |
| `gui/menus.py` | _SubMenuPositioner, _install_submenu_positioner, _KomiCustomWidget | L959-1002, L7140-7411 | ~400 |
| `gui/startup.py` | _check_models_or_exit, _SplashScreen, main, _build_startup_engine | L16006-16455 | ~450 |

---

## Step 4-1: gui/dialogs.py

### 含めるクラス
- `class ColorAdjustmentDialog(QDialog):` (L9026-9357)
- `class _WarningIconWidget(QWidget):` (L9358-9518)
- `class _UnsavedChangesDialog(QDialog):` (L9519-9761)
- `class _FirstLaunchRankDialog(QDialog):` (L9762-10240)

### 必要な import
```python
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QComboBox, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QSize, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath

from gui.theme import T, SP_XS, SP_SM, SP_MD, SP_LG, R_MD, R_LG
from gui.fonts import F, Font_XS, Font_SM, Font_MD, Font_LG
from gui.icons import make_icon, menu_qss
from gui.widgets.common import FlatSlider, _RankItemDelegate
```

### 注意点
- フェーズ1 で `_FirstLaunchRankDialog.reject` にコメントを追加済み(削除しない方針)
- `_start_close_animation` メソッドが `_UnsavedChangesDialog` と `_FirstLaunchRankDialog` の両方にある(同名重複)。各クラス独自の実装なので、そのままで OK

### 検証
- カラー調整ダイアログを開く・操作・閉じる(設定メニュー → カラー調整)
- 未保存変更ダイアログ: SGF を開いて石を打ってから別のSGFを開く → ダイアログ表示
- 初回起動ランクダイアログ: `QSettings("Kizuki","Kizuki").remove("player_rank")` で初期化して起動
- ダイアログのフェードイン・フェードアウト

```bash
git commit -m "refactor: extract dialog classes to gui/dialogs.py"
```

---

## Step 4-2: gui/menus.py

### 含めるクラス・関数
- `class _SubMenuPositioner(QObject):` (L959-990)
- `_submenu_positioner_instance = None` (L991, モジュールレベル変数)
- `def _install_submenu_positioner(qmenu) -> None:` (L993-1002)
- `class _KomiCustomWidget(QWidget):` (L7140-7411)

### 必要な import
```python
from PyQt6.QtWidgets import QWidget, QMenu, QWidgetAction
from PyQt6.QtCore import Qt, QObject, QEvent, QPoint, pyqtSignal
from gui.theme import T, SP_XS, SP_SM, SP_MD
from gui.fonts import F, Font_XS, Font_SM
from gui.icons import menu_qss, style_qmenu  # icons.py で公開
```

### 注意点
- `_submenu_positioner_instance` はモジュールレベルの状態。シングルトン的に扱われている
- `_install_submenu_positioner` 関数の使用箇所(`main_window.py` 内の `_build_menu` 内など)を grep で全て見つけ、import を追加する

### 検証
- 設定メニューの各サブメニュー位置が正しい(画面端でも切れない)
- コミの「その他...」を選択 → カスタムコミウィジェット表示

```bash
git commit -m "refactor: extract menu helpers and _KomiCustomWidget to gui/menus.py"
```

---

## Step 4-3: gui/startup.py

### 含めるクラス・関数
- `def _check_models_or_exit():` (L16006-16047)
- `class _SplashScreen(QWidget):` (L16048-16343)
- `def main():` (L16344-16434)
- `def _build_startup_engine() -> "KataGoEngine":` (L16435-16455)

### 必要な import
```python
import sys
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

from core.katago_engine import KataGoEngine
from gui.theme import T
from gui.fonts import Font_LG, Font_XL
from gui.infra import _profile

# MainWindow は遅延 import (循環防止)
# main() の中で from gui.main_window import MainWindow とする
```

### 注意点
- `main()` 内では `from gui.main_window import MainWindow` を遅延 import で書く(循環 import 防止)
- `_build_startup_engine` も `KataGoEngine` をすぐ使うので、`core.katago_engine` の import で問題なし

### 起動スクリプトの修正

既存の `囲碁AI解析.bat` や Python 起動方法を見直す必要がある。

`gui/startup.py` の最後に:
```python
if __name__ == "__main__":
    main()
```

を追加すると、以下のいずれかで起動できる:
```bash
python -m gui.startup
# または
python gui/startup.py
```

ただし、現在 `main_window.py` の末尾に `if __name__ == "__main__":` で main 呼出 があれば、それは削除する。

### 検証
- スプラッシュスクリーン表示
- KataGo エンジン起動
- メイン画面への遷移
- model が見つからない場合のエラーダイアログ表示
- 既存の起動方法でアプリが起動する

```bash
git commit -m "refactor: extract startup logic (main, splash, engine boot) to gui/startup.py"
```

---

## フェーズ全体の検証チェックリスト

- [ ] `gui/dialogs.py` 作成 + 構文OK + 起動確認
- [ ] `gui/menus.py` 作成 + 構文OK + 起動確認
- [ ] `gui/startup.py` 作成 + 構文OK + 起動確認
- [ ] アプリ起動が今までと同じ方法で動く(`.bat` ファイル等のスクリプトに変更が必要なら明示)
- [ ] スプラッシュ → メイン画面 → 操作 → 終了 が一連で動作
- [ ] 各種ダイアログ動作確認
- [ ] `main_window.py` の行数が ~7,000 → ~5,800 に減っていることを `wc -l` で確認

このフェーズ完了時点で `main_window.py` は `MainWindow` クラスとその直接依存だけになっているはず。

## 次フェーズへ

完了したら `05_phase5.md` へ。
