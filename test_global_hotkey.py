"""グローバルホットキー (Ctrl+G) の動作を SendInput で疑似テストする。
exeを起動 → アプリを「最小化」状態にして非アクティブ化 → SendInput で Ctrl+G を送る
→ アプリのクリップボードが変わったかチェック
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time
import tempfile
import shutil
from pathlib import Path
import json

# Win32 定数
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12
VK_G = 0x47
VK_CONTROL = 0x11

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("ki", KEYBDINPUT), ("_padding", ctypes.c_ubyte * 8)]

def send_hotkey(mod_key, vk_key):
    """指定の修飾子+キーの組み合わせを SendInput で送出 (wScan に適切な値を設定)"""
    MAPVK_VK_TO_VSC = 0
    scan_key = user32.MapVirtualKeyW(vk_key, MAPVK_VK_TO_VSC)
    inputs = []
    # 修飾子ダウン
    for mod, vk in mod_key:
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=0, time=0, dwExtraInfo=None)))
    # キーダウン
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_key, wScan=scan_key, dwFlags=0, time=0, dwExtraInfo=None)))
    # キーキーアップ
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_key, wScan=scan_key, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
    # 修飾子アップ
    for mod, vk in reversed(mod_key):
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)))
    inp = INPUT * len(inputs)
    i = inp(*inputs)
    sent = user32.SendInput(len(inputs), ctypes.byref(i), ctypes.sizeof(INPUT))
    return sent

def send_alt_g():
    """Alt+G を SendInput で送出"""
    return send_hotkey([(VK_MENU, 0x12)], VK_G)  # VK_MENU = Alt

def send_ctrl_g():
    """Ctrl+G を SendInput で送出"""
    return send_hotkey([(VK_CONTROL, 0x11)], VK_G)

def get_clipboard():
    user32.OpenClipboard(0)
    try:
        CF_UNICODETEXT = 13
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h = user32.GetClipboardData(CF_UNICODETEXT)
            ptr = kernel32.GlobalLock(h)
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(h)
        return ""
    finally:
        user32.CloseClipboard()

def set_clipboard(text):
    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13
    user32.OpenClipboard(0)
    try:
        user32.EmptyClipboard()
        data = (text + "\0").encode("utf-16-le")
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


# ===== テスト実行 =====
print("=" * 60)
print("グローバルホットキー (Ctrl+G) 動作テスト")
print("=" * 60)

tmpdir = tempfile.mkdtemp(prefix='wwm_ghk_')
exe = Path('dist/WWMCodeInput.exe').resolve()
shutil.copy2(exe, Path(tmpdir) / 'app.exe')

# 1) exe 起動
print("\n[1] exe 起動...")
proc = subprocess.Popen([str(Path(tmpdir) / 'app.exe')])
print(f"    PID: {proc.pid}")
time.sleep(3)  # 初期化待ち

if proc.poll() is not None:
    print(f"[NG] exe 起動失敗 exit code={proc.returncode}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    exit(1)
print("    [OK] 起動成功")

# 2) アプリを最小化 → 別のウィンドウをアクティブに
print("\n[2] アプリを最小化して非アクティブ化...")
# アプリのウィンドウを取得 → 最小化
import time as _t
_t.sleep(0.5)
# EnumWindows でクラス名が Tk のウィンドウを探す
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
results = []
def callback(hwnd, lParam):
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        if "WWM" in buff.value or "Tk" in buff.value:
            results.append((hwnd, buff.value))
    return True
user32.EnumWindows(EnumWindowsProc(callback), None)
print(f"    検出されたWWMウィンドウ: {len(results)}件")
for hwnd, title in results:
    print(f"      hwnd={hwnd} title={title!r}")
    # 最小化
    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    _t.sleep(0.3)

# 3) クリップボードを既知の文字列にしておく
print("\n[3] クリップボードを既知の値 (INITIAL) に設定")
set_clipboard("INITIAL")
_t.sleep(0.3)
cb = get_clipboard()
print(f"    クリップボード: {cb!r}")

# 4) SendInput で Alt+G を送出
print("\n[4] SendInput で Alt+G を送出 (グローバルホットキー)")
# クリップボードを既知値に
set_clipboard("INITIAL_VALUE")
_t.sleep(0.3)
cb_before = get_clipboard()
print(f"    クリップボード (送出前): {cb_before!r}")

sent = send_alt_g()
print(f"    SendInput 戻り値: {sent} (期待: 4)")
_t.sleep(1.5)  # 1.5秒待つ

# クリップボード変化チェック
cb_after = get_clipboard()
print(f"    クリップボード (送出後): {cb_after!r}")

if cb_after and cb_after != cb_before and cb_after != "INITIAL_VALUE":
    print(f"\n[OK] グローバルホットキー動作確認! クリップボードが {cb_after!r} に変化")
    result = "PASS"
else:
    print(f"\n[NG] クリップボード変化なし (Alt+G グローバルホットキーが効いていない可能性)")
    result = "FAIL"

# 7) もう一度 Ctrl+G を送って次のコードに進むか確認
print("\n[5] もう一度 Ctrl+G を送出")
_t.sleep(0.5)
set_clipboard("INITIAL2")
_t.sleep(0.3)
send_ctrl_g()
_t.sleep(0.5)
cb2 = get_clipboard()
print(f"    クリップボード: {cb2!r}")
if cb2 != "INITIAL2" and cb2:
    print(f"[OK] 2回目も成功: {cb2!r}")
else:
    print(f"[NG] 2回目失敗")

# 後処理
proc.terminate()
_t.sleep(1)
if proc.poll() is None:
    proc.kill()
shutil.rmtree(tmpdir, ignore_errors=True)

print()
print("=" * 60)
print(f"結果: {result}")
print("=" * 60)
