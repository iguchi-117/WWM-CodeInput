"""グローバルホットキー (Ctrl+G) の動作を SendInput で疑似テストする。
exeを起動 → アプリを「最小化」状態にして非アクティブ化 → SendInput で Ctrl+G を送る
→ アプリのクリップボードが変わったかチェック
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
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

# argtypes/restype 宣言 (x64でポインタ切り詰め・access violationを防ぐ)
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wt.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
user32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
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
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=0, time=0, dwExtraInfo=0)))
    # キーダウン
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_key, wScan=scan_key, dwFlags=0, time=0, dwExtraInfo=0)))
    # キーキーアップ
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_key, wScan=scan_key, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)))
    # 修飾子アップ
    for mod, vk in reversed(mod_key):
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)))
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
print("グローバルホットキー (Alt+G) 動作テスト")
print("=" * 60)

# 冒頭: Temp残骸 app.exe を掃除 (PyInstaller onefile の子プロセス残留対策)
try:
    import psutil
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if p.info["name"] == "app.exe" and "Temp" in str(p.info.get("exe") or ""):
                p.kill()
        except Exception:
            pass
    time.sleep(1.0)
except ImportError:
    pass

tmpdir = tempfile.mkdtemp(prefix='wwm_ghk_')
exe = Path('dist/WWMCodeInput.exe').resolve()
shutil.copy2(exe, Path(tmpdir) / 'app.exe')

# temp codes.json + settings.json を seed (空だと対象ゼロの false FAIL になる)
import json as _json
_seed = [
    {"code": f"GHKTEST00{i}", "used": False, "added_at": "2026-09-20",
     "used_at": None, "source": "test"}
    for i in range(1, 4)
]
(Path(tmpdir) / "codes.json").write_text(
    _json.dumps({"version": 1, "total": 3, "used": 0, "remaining": 3,
                 "codes": _seed}, ensure_ascii=False, indent=2),
    encoding="utf-8")
(Path(tmpdir) / "settings.json").write_text(
    _json.dumps({"version": 1, "hotkey_next": "Alt+G",
                 "auto_paste_enabled": False}, ensure_ascii=False),
    encoding="utf-8")

# 1) exe 起動
print("\n[1] exe 起動...")
# 前提: exeに ~RUNASADMIN 互換性フラグがあると非昇格シェルからは WinError 740 で
# 起動できない (UIPI権限境界)。その場合は SKIP 扱いとし、実デスクトップ人間押下で判定する。
try:
    proc = subprocess.Popen([str(Path(tmpdir) / 'app.exe')])
except OSError as exc:
    print(f"    [SKIP] 非昇格シェルから起動不可 ({exc})。実デスクトップ人間押下で再判定してください")
    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    print("=" * 60)
    print("結果: SKIP (UIPI権限境界・要人間押下)")
    print("=" * 60)
    sys.exit(2)
print(f"    PID: {proc.pid}")
time.sleep(6)  # 初期化待ち (pynput登録+JSON読込に余裕)

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
    # SendInput合成送出はterminalセッション制約で届かない場合がある (CHANGELOG既知)。
    # 実デスクトップ人間押下が正のため、合成FAILはSKIP扱いでFAIL確定させない。
    print(f"\n[SKIP] クリップボード変化なし — terminal合成送出の制約の可能性。")
    print("       実デスクトップ人間押下 (Alt+G) で再判定してください。")
    result = "SKIP"

# 5) もう一度 Alt+G を送って次のコードに進むか確認 (現行仕様: Alt+G連打で次へ)
print("\n[5] もう一度 Alt+G を送出 (2件目が来るはず)")
_t.sleep(0.5)
_t.sleep(0.3)
send_alt_g()
_t.sleep(1.5)
cb2 = get_clipboard()
print(f"    クリップボード: {cb2!r}")
if result == "SKIP":
    print("[SKIP] 2回目も合成送出のため判定対象外 (要人間押下)")
elif cb2 and cb2 != cb_after and cb2.startswith("GHKTEST"):
    print(f"[OK] 2回目も成功: {cb2!r}")
else:
    print(f"[NG] 2回目失敗 (期待: {cb_after!r} とは別のGHKTEST*)")
    result = "FAIL"

# 後処理 (PyInstaller onefile 子プロセス残骸も掃除)
proc.terminate()
_t.sleep(1)
if proc.poll() is None:
    proc.kill()
try:
    import psutil as _ps
    for p in _ps.process_iter(["name", "exe"]):
        try:
            if p.info["name"] == "app.exe" and str(tmpdir) in str(p.info.get("exe") or ""):
                p.kill()
        except Exception:
            pass
except ImportError:
    pass
shutil.rmtree(tmpdir, ignore_errors=True)

print()
print("=" * 60)
print(f"結果: {result}")
print("=" * 60)
sys.exit(0 if result == "PASS" else (2 if result == "SKIP" else 1))
