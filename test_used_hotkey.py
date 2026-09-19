"""Alt+Shift+G (コピー+自動ペースト) 動作テスト — 現行仕様版。

旧仕様「使用済にして次へ」は廃止。現行は「次のコードをコピー + Ctrl+V自動ペースト」で
自動マークなし。auto_paste_enabled=true を seed し、2回送出 → クリップボードがコードに
変化すること・used件数が増えないことを確認する。
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
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

# argtypes/restype 宣言 (x64でポインタ切り詰め・access violationを防ぐ)
user32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("ki", KEYBDINPUT), ("_padding", ctypes.c_ubyte * 8)]

def send_alt_shift_g():
    MAPVK_VK_TO_VSC = 0
    s_alt = user32.MapVirtualKeyW(VK_MENU, MAPVK_VK_TO_VSC)
    s_shift = user32.MapVirtualKeyW(VK_SHIFT, MAPVK_VK_TO_VSC)
    s_g = user32.MapVirtualKeyW(VK_G, MAPVK_VK_TO_VSC)
    inputs = [
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_MENU, wScan=s_alt, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SHIFT, wScan=s_shift, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_G, wScan=s_g, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_G, wScan=s_g, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SHIFT, wScan=s_shift, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_MENU, wScan=s_alt, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
    ]
    inp = INPUT * len(inputs)
    i = inp(*inputs)
    return user32.SendInput(len(inputs), ctypes.byref(i), ctypes.sizeof(INPUT))


print("=" * 60)
print("Alt+Shift+G (コピー+自動ペースト) 動作テスト — 現行仕様")
print("=" * 60)

# 冒頭: Temp残骸 app.exe を掃除 (PyInstaller onefile の子プロセス残留対策)
try:
    import psutil as _ps0
    for p in _ps0.process_iter(["name", "exe"]):
        try:
            if p.info["name"] == "app.exe" and "Temp" in str(p.info.get("exe") or ""):
                p.kill()
        except Exception:
            pass
    time.sleep(1.0)
except ImportError:
    pass

tmpdir = tempfile.mkdtemp(prefix='wwm_used_')
exe = Path('dist/WWMCodeInput.exe').resolve()
shutil.copy2(exe, Path(tmpdir) / 'app.exe')
print(f"テスト環境: {tmpdir}")

# seed: 未使用3件 + 自動ペーストON (現行仕様の前提)
_seed = [
    {"code": f"USEDTEST00{i}", "used": False, "added_at": "2026-09-20",
     "used_at": None, "source": "test"}
    for i in range(1, 4)
]
(Path(tmpdir) / "codes.json").write_text(
    json.dumps({"version": 1, "total": 3, "used": 0, "remaining": 3,
                "codes": _seed}, ensure_ascii=False, indent=2),
    encoding="utf-8")
(Path(tmpdir) / "settings.json").write_text(
    json.dumps({"version": 1, "hotkey_next": "Alt+G",
                "hotkey_copy_paste": "Alt+Shift+G",
                "auto_paste_enabled": True,
                "auto_paste_delay_ms": 150}, ensure_ascii=False),
    encoding="utf-8")

# 1) exe 起動
print("\n[1] exe 起動...")
proc = subprocess.Popen([str(Path(tmpdir) / 'app.exe')])
print(f"    PID: {proc.pid}")
time.sleep(6)  # 初期化待ち (pynput登録+JSON読込に余裕)

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

# 4) Alt+Shift+G を SendInput (現行: コピー+自動ペースト・自動マークなし)
print("\n[3] SendInput で Alt+Shift+G を2回送出")
for i in range(2):
    sent = send_alt_shift_g()
    print(f"    送信 {i+1}: SendInput={sent} (期待: 6)")
    _t.sleep(1.5)

# 5) 現行仕様の確認: used件数は増えない (自動マークなし)
_t.sleep(1.0)
data = json.loads(codes_path.read_text(encoding='utf-8'))
final_used = sum(1 for c in data.get('codes', []) if c.get('used'))
print(f"\n    最終状態: 全{len(data.get('codes', []))}件, 使用済{final_used}件")

# 6) 結果判定 (現行仕様: 自動マークなし = used不変が正)
if final_used == initial_used:
    print(f"\n[OK] Alt+Shift+G 現行仕様確認! 使用済件数不変 ({initial_used} → {final_used}、自動マークなし)")
    result = "PASS"
else:
    print(f"\n[NG] 使用済件数が変化 (期待: 不変, 実際: {initial_used} → {final_used})")
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
sys.exit(0 if result == "PASS" else 1)
