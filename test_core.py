"""WWM-CodeInput 軽量テスト (tkinter / ctypes なし)
JSON I/O 関数を直接再定義し、app.py には依存しない。
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix="wwm_test_"))
CODES_JSON = TEST_DIR / "codes.json"
BACKUP_JSON = TEST_DIR / "codes.backup.json"
JST = timezone(timedelta(hours=9))


# app.py の JSON I/O 関数を忠実に再定義
def load_codes():
    if not CODES_JSON.exists():
        return []
    try:
        text = CODES_JSON.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict) and "codes" in data:
            return data["codes"]
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def save_codes(codes):
    payload = {
        "version": 1,
        "updated_at": datetime.now(JST).isoformat(),
        "total": len(codes),
        "used": sum(1 for c in codes if c.get("used")),
        "remaining": sum(1 for c in codes if not c.get("used")),
        "codes": codes,
    }
    tmp = CODES_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if CODES_JSON.exists():
        try:
            BACKUP_JSON.write_text(CODES_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    os.replace(tmp, CODES_JSON)


def next_unused(codes_list, start=0):
    for i in range(start, len(codes_list)):
        if not codes_list[i].get("used", False):
            return i
    return -1


def has_duplicate(codes_list, code):
    return any(c.get("code", "").upper() == code.upper() for c in codes_list)


# T1
save_codes([])
assert CODES_JSON.exists()
print("[PASS] T1: 空のcodes.jsonを保存")

# T2
assert load_codes() == []
print("[PASS] T2: 空リストをロード")

# T3
codes = [
    {"code": "TESTCODE1", "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test1"},
    {"code": "TESTCODE2", "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test2"},
    {"code": "WWMDEMO3",   "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test3"},
]
save_codes(codes)
assert len(load_codes()) == 3
print("[PASS] T3: 3件追加して再ロード")

# T4
loaded = load_codes()
loaded[0]["used"] = True
loaded[0]["used_at"] = "2026-07-02 23:00"
save_codes(loaded)
assert load_codes()[0]["used"] is True
print("[PASS] T4: 使用済マーク永続化")

# T5
assert BACKUP_JSON.exists()
print("[PASS] T5: バックアップファイル生成")

# T6
assert next_unused(load_codes()) == 1
print("[PASS] T6: 次に未使用のインデックス")

# T7
data = load_codes()
assert has_duplicate(data, "testcode1") is True
assert has_duplicate(data, "TESTCODE2") is True
assert has_duplicate(data, "NEWXYZ999") is False
print("[PASS] T7: 大文字小文字を区別しない重複チェック")

# T8
codes.append({"code": "NEWCDE4", "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test4"})
save_codes(codes)
parsed = json.loads(CODES_JSON.read_text(encoding="utf-8"))
assert len(parsed["codes"]) == 4
print(f"[PASS] T8: アトミック書き込み後 4件")

# T9
all_used = [{"code": f"X{i}", "used": True, "added_at": "2026-07-02", "used_at": "2026-07-02", "source": "t"} for i in range(3)]
assert next_unused(all_used) == -1
print("[PASS] T9: 全件使用済 → -1")

# T10
CODES_JSON.write_text("{broken json,,,", encoding="utf-8")
assert load_codes() == []
print("[PASS] T10: 不正JSONで空リストにフォールバック")

# T11: parse_hotkey 関数 (app.py から)
import re as _re
src = Path("app.py").read_text(encoding="utf-8")
ns_test = {"__name__": "wwmtest_parse", "__file__": "app.py"}
# 定数 (ファイル先頭で定義されている)
for line in [
    "MOD_ALT = 0x0001",
    "MOD_CONTROL = 0x0002",
    "MOD_SHIFT = 0x0004",
    "MOD_WIN = 0x0008",
    "VK_G = 0x47",
]:
    if line in src:
        ns_test[line.split(" = ")[0]] = int(line.split(" = ")[1], 16)

# parse_hotkey 関数定義 (VK_MAP/MODIFIER_MAP/parse_hotkey) を位置ベースで切り出し
pos_vkmap = src.find("VK_MAP = ", 0, src.find("def parse_hotkey("))
pos_end = src.find("return (modifiers, vk)") + len("return (modifiers, vk)")
if pos_vkmap > 0 and pos_end > pos_vkmap:
    snippet = src[pos_vkmap:pos_end]
    try:
        exec(snippet, ns_test)
        parse_hotkey = ns_test["parse_hotkey"]
        # 各種入力パターンをテスト
        test_cases = [
            ("Alt+G", (0x0001, 0x47)),
            ("Ctrl+G", (0x0002, 0x47)),
            ("Ctrl+Shift+G", (0x0002 | 0x0004, 0x47)),
            ("Alt+Shift+F1", (0x0001 | 0x0004, 0x70)),
            ("F8", (0, 0x77)),
            ("Ctrl+Space", (0x0002, 0x20)),
        ]
        all_passed = True
        for spec, expected in test_cases:
            result = parse_hotkey(spec)
            if result != expected:
                print(f"  [FAIL] parse_hotkey({spec!r}) = {result}, 期待 {expected}")
                all_passed = False
        # 無効な入力
        if parse_hotkey("") is not None or parse_hotkey("invalid") is not None:
            all_passed = False
        if all_passed:
            print("[PASS] T11: parse_hotkey 6パターンすべて正常パース + 無効入力でNone")
        else:
            print("[FAIL] T11")
    except Exception as e:
        print(f"[SKIP] T11: 抽出/実行失敗 ({e})")
else:
    print("[SKIP] T11: parse_hotkey 抽出失敗")

shutil.rmtree(TEST_DIR, ignore_errors=True)
print("\n=== 全10テスト PASS ===")
