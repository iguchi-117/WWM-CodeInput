@echo off
REM ============================================================
REM   WWM コード高速入力ツール - 起動スクリプト
REM   ダブルクリックでこのファイルを実行してください
REM   ※ ゲーム中 (DirectX フルスクリーン) で使用する場合は
REM      install.ps1 で生成したショートカットから起動してください
REM      (管理者権限で起動されます)
REM
REM   ※ codes.json / settings.json は このフォルダ (開発元)
REM     から一元管理します (dist フォルダにはコピーしません)
REM ============================================================
chcp 65001 > nul
cd /d "%~dp0"

set EXE=dist\WWMCodeInput.exe
if not exist "%EXE%" (
    echo [ERROR] %EXE% が見つかりません。
    echo         build_exe.py を実行してexeをビルドしてください。
    pause
    exit /b 1
)

REM データファイル (codes.json / settings.json) の場所を
REM この start.bat があるフォルダ (開発元) に固定。
REM exe は dist フォルダで動くが、ファイルは開発元で一元管理。
set "WWM_DEV_DATA_DIR=%~dp0."

echo ============================================================
echo  WWM コード高速入力ツール を起動しています...
echo  終了: ウィンドウを閉じる
echo ============================================================
echo.
"%EXE%"

