#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WWM コード高速入力ツール - exe ビルドスクリプト
PyInstaller で単一実行ファイル (.exe) を生成する。

使い方:
    pip install pyinstaller
    python build_exe.py
"""
import os
import sys
import subprocess
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
APP_PY = APP_DIR / "app.py"
ICON_PY = APP_DIR / "make_icon.py"
ICON_ICO = APP_DIR / "app.ico"
DIST_DIR = APP_DIR / "dist"
BUILD_DIR = APP_DIR / "build"
EXE_NAME = "WWMCodeInput"


def run(cmd, cwd=None, check=True):
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, check=check)
    return result.returncode == 0


def main():
    print("=" * 60)
    print("WWM コード高速入力ツール - exe ビルド")
    print("=" * 60)

    # 1. PyInstaller がインストールされているか確認
    print("\n[1/4] PyInstaller チェック...")
    try:
        import PyInstaller
        print(f"  PyInstaller {PyInstaller.__version__} OK")
    except ImportError:
        print("  PyInstaller がインストールされていません")
        ans = input("  今インストールしますか？ (y/N): ")
        if ans.lower() != "y":
            print("  中止しました。 'pip install pyinstaller' を実行してから再実行してください。")
            return 1
        if not run(f"{sys.executable} -m pip install pyinstaller"):
            return 1

    # 2. アイコン生成（無ければ）
    if not ICON_ICO.exists():
        print(f"\n[2/4] アイコン生成: {ICON_ICO}")
        if ICON_PY.exists():
            run(f"{sys.executable} {ICON_PY}")
        else:
            print(f"  ⚠ {ICON_PY} が無いのでデフォルトアイコンを使います")
    else:
        print(f"\n[2/4] アイコン既存: {ICON_ICO}")

    # 3. クリーンビルド
    print("\n[3/4] クリーンビルド...")
    if DIST_DIR.exists():
        run(f"rmdir /s /q {DIST_DIR}", check=False)
    if BUILD_DIR.exists():
        run(f"rmdir /s /q {BUILD_DIR}", check=False)

    # 4. PyInstaller 実行
    print("\n[4/4] PyInstaller 実行中...")
    icon_arg = f'--icon="{ICON_ICO}"' if ICON_ICO.exists() else ""
    cmd = (
        f'pyinstaller '
        f'--onefile '
        f'--windowed '
        f'--name="{EXE_NAME}" '
        f'{icon_arg} '
        f'--collect-all tkinter '
        f'--collect-all pynput '
        f'--noconfirm '
        f'--noupx '
        f'--manifest=app.manifest '
        f'"{APP_PY}"'
    )
    # build_exe.py 自体の GeneratedBy ログが出ないように stderr を抑制しない
    run(cmd, cwd=str(APP_DIR), check=False)  # exit code が 0/1 どちらでも結果確認へ進む

    # 結果確認
    exe_path = DIST_DIR / f"{EXE_NAME}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n{'=' * 60}")
        print(f"✅ ビルド成功!")
        print(f"   exe: {exe_path}")
        print(f"   サイズ: {size_mb:.1f} MB")
        print(f"{'=' * 60}")
        print(f"\n配布方法:")
        print(f"  • {exe_path} をzipにまとめて配布")
        print(f"  • 受け取った側は展開するだけ（Pythonインストール不要）")
        print(f"\n注意:")
        print(f"  • Windows Defender が初回実行時に警告を出す場合があります")
        print(f"  • codes.json / settings.json は exe と同じフォルダから自動読み込み")
        print(f"    (exe 起動時、なければ自動作成。編集は exe と同じフォルダで)")
        return 0
    else:
        print(f"\n❌ ビルド失敗: {exe_path} が見つかりません")
        return 1


if __name__ == "__main__":
    sys.exit(main())
