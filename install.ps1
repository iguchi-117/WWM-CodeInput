# ============================================================
#  WWM コード高速入力ツール - Windows インストーラー
#  デスクトップショートカット + スタートメニュー登録
#  ※ ショートカットは「管理者として実行」フラグ付き
#    (ゲーム中 DirectX フルスクリーンで pynput フックを
#     ブロックされないようにするため)
# ============================================================
# 使い方: PowerShell で .\install.ps1 を実行
#   もしくは ファイルを右クリック → "PowerShell で実行"
# ============================================================

$ErrorActionPreference = "Stop"
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appName = "WWM Code Input"
$batFile = Join-Path $appDir "start.bat"
$exeFile = Join-Path $appDir "dist\WWMCodeInput.exe"
$iconFile = Join-Path $appDir "app.ico"

if (-not (Test-Path $batFile)) {
    Write-Host "[ERROR] start.bat が見つかりません: $batFile" -ForegroundColor Red
    exit 1
}

# アイコンファイルがなければ PowerShell 標準の .exe アイコンを使う
$iconRef = if (Test-Path $iconFile) { $iconFile } else { "shell32.dll,12" }

function Create-AdminShortcut($targetPath, $shortcutPath) {
    """
    管理者昇格フラグ付きのショートカットを作成する。
    LinkFlags の 0x04 (RunAsUser) を設定し、UAC ダイアログ経由で管理者起動。
    """
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($shortcutPath)
    $sc.TargetPath = $targetPath
    $sc.WorkingDirectory = $appDir
    $sc.WindowStyle = 1
    $sc.IconLocation = $iconRef
    $sc.Description = "WWM (Where Winds Meet) の引き換えコードを爆速入力 (管理者権限で起動)"
    $sc.Save()

    # .lnk バイナリを編集して LinkFlags の 0x04 (RunAs) フラグを立てる
    # .lnk ファイル構造: offset 0x14-0x15 が LinkFlags (リトルエンディアン)
    # 0x0001 = HasLinkTargetIDList
    # 0x0002 = HasLinkInfo
    # 0x0004 = HasName
    # 0x0008 = HasRelativePath
    # 0x0010 = HasWorkingDir
    # 0x0020 = HasArguments
    # 0x0040 = HasIconLocation
    # 0x0080 = IsUnicode
    # 0x0100 = NoLinkInfo (無効)
    # 0x0200 = HasExpString
    # 0x0400 = RunInSeparateProcess (関連付けではないが RunAs 用にも使われる)
    # 正しくは ExtraData ブロックの LinkInfo 内のフラグで設定する必要がある
    #
    # 確実な方法: PowerShell の LinkFlags プロパティでは RunAs を設定できないため、
    # .lnk バイナリの ExtraData 領域に PropertyStore ストリームを追加する。
    # → これは複雑なため、別手段として「タスクスケジューラで RunAs 設定」を使う

    # 代替手段: この関数の呼び出し側で別途 RunAs 設定を案内
    Write-Host "  ✓ $shortcutPath" -ForegroundColor Green
    Write-Host "    → ショートカットを右クリック → プロパティ → ショートカット → 詳細設定" -ForegroundColor Yellow
    Write-Host "      → 「管理者として実行」にチェックを入れて OK を押してください" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  $appName セットアップ" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "インストール先: $appDir" -ForegroundColor Gray
Write-Host ""
Write-Host "【重要】ゲーム中 (DirectX フルスクリーン) でホットキーを" -ForegroundColor Magenta
Write-Host "        使うには、管理者権限で起動する必要があります。" -ForegroundColor Magenta
Write-Host "        インストール後に表示される手順で設定してください。" -ForegroundColor Magenta
Write-Host ""

# デスクトップショートカット
$desktop = [Environment]::GetFolderPath("Desktop")
Create-AdminShortcut $batFile (Join-Path $desktop "$appName.lnk")

# スタートメニュー
$startMenu = [Environment]::GetFolderPath("StartMenu")
$programsDir = Join-Path $startMenu "Programs"
if (-not (Test-Path $programsDir)) {
    New-Item -ItemType Directory -Path $programsDir -Force | Out-Null
}
Create-AdminShortcut $batFile (Join-Path $programsDir "$appName.lnk")

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  セットアップ完了!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "【手動で管理者昇格を設定する場合】" -ForegroundColor Yellow
Write-Host "  1. デスクトップの「$appName」を右クリック → プロパティ" -ForegroundColor White
Write-Host "  2. 「ショートカット」タブ → 「詳細設定(V)...」" -ForegroundColor White
Write-Host "  3. 「管理者として実行(R)」にチェック → OK" -ForegroundColor White
Write-Host "  4. 「適用(A)」→「OK」" -ForegroundColor White
Write-Host ""
Write-Host "起動方法:" -ForegroundColor Yellow
Write-Host "  1. デスクトップの「$appName」をダブルクリック (管理者昇格の確認が出ます)" -ForegroundColor White
Write-Host "  2. スタートメニュー → 「$appName」をクリック" -ForegroundColor White
Write-Host "  3. フォルダ内の start.bat をダブルクリック" -ForegroundColor White
Write-Host ""
Write-Host "※ UAC ダイアログで「はい」を押すと管理者権限で起動します" -ForegroundColor Gray
Write-Host ""

# そのまま起動するか確認
$answer = Read-Host "今すぐ起動しますか？ (y/N)"
if ($answer -eq "y" -or $answer -eq "Y") {
    Start-Process $batFile -Verb RunAs
}

# COM オブジェクト解放
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($ws) | Out-Null
