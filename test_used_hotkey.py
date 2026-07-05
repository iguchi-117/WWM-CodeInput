"""Alt+Shift+G (使用済にして次へ) 動作テスト
exeを起動 → 2回 Alt+Shift+G を SendInput → codes.json の used 件数が増えることを確認
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import time
import tempfile
import shutil
import json
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_G = 0x47

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("ki", KEYBDINPUT), ("_padding", ctypes.c_ubyte * 8)]

def send_alt_shift_g():
    MAPVK_VK_TO_VSC = 0
    s_alt = user32.MapVirtualKeyW(VK_MENU, MAPVK_VK_TO_VSC)
    s_shift = user32.MapVirtualKeyW(VK_SHIFT, MAPVK_VK_TO_VSC)
    s_g = user32.MapVirtualKeyW(VK_G, MAPVK_VK_TO_VSC)
    inputs = [
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_MENU, wScan=s_alt, dwFlags=0, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SHIFT, wScan=s_shift, dwFlags=0, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_G, wScan=s_g, dwFlags=0, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_G, wScan=s_g, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SHIFT, wScan=s_shift, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_MENU, wScan=s_alt, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
    ]
    inp = INPUT * len(inputs)
    i = inp(*inputs)
    return user32.SendInput(len(inputs), ctypes.byref(i), ctypes.sizeof(INPUT))


print("=" * 60)
print("Alt+Shift+G (使用済にして次へ) 動作テスト")
print("=" * 60)

tmpdir = tempfile.mkdtemp(prefix='wwm_used_')
exe = Path('dist/WWMCodeInput.exe').resolve()
shutil.copy2(exe, Path(tmpdir) / 'app.exe')
print(f"テスト環境: {tmpdir}")

# 1) exe 起動
print("\n[1] exe 起動...")
proc = subprocess.Popen([str(Path(tmpdir) / 'app.exe')])
print(f"    PID: {proc.pid}")
time.sleep(4)  # 初期化待ち

if proc.poll() is not None:
    print(f"[NG] exit code={proc.returncode}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    exit(1)
print("    [OK] 起動成功")

# 2) 初期 codes.json 状態確認
codes_path = Path(tmpdir) / 'codes.json'
data = json.loads(codes_path.read_text(encoding='utf-8'))
initial_used = sum(1 for c in data.get('codes', []) if c.get('used'))
total = len(data.get('codes', []))
print(f"    初期状態: 全{total}件, 使用済{initial_used}件")

# 3) アプリ最小化
print("\n[2] アプリ最小化...")
import time as _t
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
results = []
def callback(hwnd, lParam):
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        if "WWM" in buff.value:
            results.append((hwnd, buff.value))
    return True
user32.EnumWindows(EnumWindowsProc(callback), None)
for hwnd, title in results:
    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    _t.sleep(0.2)
print(f"    {len(results)}ウィンドウ最小化")

# 4) Alt+Shift+G を SendInput
print("\n[3] SendInput で Alt+Shift+G を2回送出")
for i in range(2):
    sent = send_alt_shift_g()
    print(f"    送信 {i+1}: SendInput={sent}")
    _t.sleep(1.0)

# 5) codes.json の used 件数チェック
_t.sleep(1.0)
data = json.loads(codes_path.read_text(encoding='utf-8'))
final_used = sum(1 for c in data.get('codes', []) if c.get('used'))
print(f"\n    最終状態: 全{len(data.get('codes', []))}件, 使用済{final_used}件")

# 6) 結果判定
if final_used >= 2:
    print(f"\n[OK] Alt+Shift+G 動作確認! 使用済件数: {initial_used} → {final_used}")
    used_codes = [c["code"] for c in data["codes"] if c.get("used")]
    print(f"    使用済コード: {used_codes}")
    result = "PASS"
else:
    print(f"\n[NG] 使用済件数が増えず (期待: +2, 実際: +{final_used - initial_used})")
    result = "FAIL"

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
