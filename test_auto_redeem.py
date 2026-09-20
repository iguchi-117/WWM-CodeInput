"""WWM-CodeInput コード入力支援CLI (auto_redeem) の単体テスト (ゲーム不要)。

Win32実入力はすべて _vk_seq 注入/スタブ化する。Jev判定・連続投入ループは撤去済み。
実行: python test_auto_redeem.py
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import auto_redeem as ar

PASS = 0


def check(name, cond, extra=""):
    global PASS
    assert cond, f"[FAIL] {name} {extra}"
    PASS += 1
    print(f"[PASS] {name}")


def make_codes(n=4, used_first=1):
    return [{"code": f"CODE{i:04d}", "used": i < used_first,
             "added_at": "2026-09-20", "used_at": "2026-09-20" if i < used_first else None,
             "source": "test"} for i in range(n)]


# B1: next_unused 順序
codes = make_codes()
check("B1-1 先頭未使用", ar.next_unused(codes) == 1)
check("B1-2 start指定", ar.next_unused(codes, start=2) == 2)
check("B1-3 全使用済→-1", ar.next_unused([dict(c, used=True) for c in codes]) == -1)

# B2: dry-run は表示のみ・保存なし (隔離データで main 経由)
tmpd = Path(tempfile.mkdtemp(prefix="wwm_dry_"))
cf = tmpd / "codes.json"
cf.write_text(json.dumps({"version": 1, "total": 3, "used": 0, "remaining": 3,
                          "codes": make_codes(n=3, used_first=0)}, ensure_ascii=False),
              encoding="utf-8")
md5_before = hashlib.md5(cf.read_bytes()).hexdigest()
orig_c = ar.CODES_JSON
ar.CODES_JSON = cf
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        rc = ar.main(["--dry-run", "--limit", "3"])
finally:
    ar.CODES_JSON = orig_c
out = buf.getvalue()
check("B2-1 dry-run 3件表示", rc == 0 and out.count("next[") == 3, repr(out))
check("B2-2 dry-run 無保存(md5一致)", hashlib.md5(cf.read_bytes()).hexdigest() == md5_before)
check("B2-3 バックアップ無生成", not (tmpd / "codes.backup.json").exists())

# B2b: 未使用なし → done 表示・無保存
cf.write_text(json.dumps({"version": 1, "total": 1, "used": 1, "remaining": 0,
                          "codes": [{"code": "USED001", "used": True, "added_at": "2026-09-20",
                                     "used_at": "2026-09-20", "source": "t"}]}, ensure_ascii=False),
              encoding="utf-8")
md5_b = hashlib.md5(cf.read_bytes()).hexdigest()
buf = io.StringIO()
ar.CODES_JSON = cf
try:
    with contextlib.redirect_stdout(buf):
        rc = ar.main(["--dry-run", "--limit", "3"])
finally:
    ar.CODES_JSON = orig_c
check("B2b 未使用なしでdone表示・無保存",
      rc == 0 and "done: 未使用なし" in buf.getvalue()
      and hashlib.md5(cf.read_bytes()).hexdigest() == md5_b, repr(buf.getvalue()))

# B3: 撤去オプション (loop/result-text/yes/settle/shot-dir) は拒否される
rejected = []
for bad in (["--loop"], ["--result-text", "x"], ["--yes"], ["--settle", "2"], ["--shot-dir", "x"]):
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ar.main(bad)
    except SystemExit:
        rejected.append(bad[0])
check("B3 撤去オプション5種は拒否", len(rejected) == 5, f"{rejected}")

# B4: 中央クロップ幾何
check("B4 中央クロップ", ar.crop_center_box(1920, 1080, 0.6) == (384, 216, 1536, 864))

# B5: capture_result center は実PNGを中央切抜き (PILのみ・ゲーム不要)
tmp = Path(tempfile.mkdtemp(prefix="wwm_crop_"))
src = tmp / "full.png"
dst = tmp / "out.png"
from PIL import Image
Image.new("RGB", (200, 100), (255, 0, 0)).save(src)
orig_cap = ar.capture_window
ar.capture_window = lambda h, p: (Image.open(src).save(p), p)[1]
try:
    ar.capture_result(12345, str(dst), crop="center", ratio=0.5)
    got = Image.open(dst).size
finally:
    ar.capture_window = orig_cap
check("B5 中央切抜き100x50", got == (100, 50), f"{got}")

# B6: send_key は down→up の順で _vk_seq に送出 (注入・実送出なし)
events = []
orig_vk = ar._vk_seq
ar._vk_seq = lambda seq: (events.extend(list(seq)), len(list(seq)))[1]
try:
    ok = ar.send_key(0x20, delay_ms=0)
finally:
    ar._vk_seq = orig_vk
check("B6 send_key Space down/up", ok is True and events == [(0x20, 0), (0x20, 2)], f"{events}")

# B7: send_ctrl_v は Ctrl down→V down/up→Ctrl up
seqs = []
ar._vk_seq = lambda seq: (seqs.append(list(seq)), len(seq))[1]
try:
    ok2 = ar.send_ctrl_v(delay_ms=0)
finally:
    ar._vk_seq = orig_vk
check("B7 send_ctrl_v 4件シーケンス",
      ok2 is True and seqs and seqs[0] == [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)], f"{seqs}")

# B8: focus確認ヘルパ (スタブ注入・実ウィンドウ不要)
class _FakeGui:
    def __init__(self, fg, title="Where Winds Meet"):
        self._fg = fg
        self._title = title
    def GetForegroundWindow(self): return self._fg
    def GetWindowText(self, h): return self._title if h == self._fg else "other"
check("B8-1 foreground_matches正常", ar.foreground_matches(777, _FakeGui(777)) is True)
check("B8-2 foreground_matches不一致", ar.foreground_matches(888, _FakeGui(777)) is False)
check("B8-3 foreground_matches例外時False", ar.foreground_matches(777, None) is False)

# B9: focus_check は診断dictを返す (win32gui注入・実ウィンドウ不要)
_fg_mod = types.ModuleType("wwm_fake_gui")
_fg_mod.GetForegroundWindow = lambda: 777
_fg_mod.GetWindowText = lambda h: "Where Winds Meet"
_fg_mod.ShowWindow = lambda h, c: True
_fg_mod.SetForegroundWindow = lambda h: True
_orig_gui = sys.modules.get("win32gui")
sys.modules["win32gui"] = _fg_mod
try:
    fc = ar.focus_check(777)
finally:
    if _orig_gui is not None:
        sys.modules["win32gui"] = _orig_gui
    else:
        del sys.modules["win32gui"]
check("B9 focus_check診断dict",
      fc["focused"] is True and fc["fg"] == 777 and isinstance(fc["admin"], bool), f"{fc}")

# B10: click_at/press_tab/focus_field (注入・実送出なし)
clicks = []
tabs = []
orig_click = ar.click_at
orig_tab = ar.press_tab
ar.click_at = lambda x, y: (clicks.append((x, y)), True)[1]
ar.press_tab = lambda n=1: (tabs.append(n), True)[1]
ok_c = ar.click_at(960, 540)
ok_t = ar.press_tab(2)
check("B10-1 click_at/press_tab注入", ok_c and ok_t and clicks == [(960, 540)] and tabs == [2], f"{clicks} {tabs}")

calls = []
ar.click_at = lambda x, y: (calls.append(("click", x, y)), True)[1]
ar.press_tab = lambda n=1: (calls.append(("tab", n)), True)[1]
try:
    r1 = ar.focus_field({"click_xy": [100, 200], "tabs": 1})
    r2 = ar.focus_field({})
finally:
    ar.click_at = orig_click
    ar.press_tab = orig_tab
check("B10-2 focus_field設定ありはclick→tab",
      r1 is True and calls == [("click", 100, 200), ("tab", 1)], f"{calls} {r1}")
check("B10-3 focus_field設定なしはnoop", r2 is True)

# B11: 貼付ラダー (ctrl_v / shift_insert / 未知 / type_text。注入・実送出なし)
seqs2 = []
orig_vk2 = ar._vk_seq
ar._vk_seq = lambda seq: (seqs2.append(list(seq)), len(list(seq)))[1]
try:
    ok_cv = ar.send_paste("ctrl_v", delay_ms=0)
    ok_si = ar.send_paste("shift_insert", delay_ms=0)
    ok_bad = ar.send_paste("unknown_method", delay_ms=0)
finally:
    ar._vk_seq = orig_vk2
check("B11-1 ctrl_vはCtrl down→V down/up→Ctrl up",
      ok_cv is True and seqs2[0] == [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)], f"{seqs2}")
check("B11-2 shift_insertはShift down→Insert down/up→Shift up",
      ok_si is True and seqs2[1] == [(0x10, 0), (0x2D, 0), (0x2D, 2), (0x10, 2)], f"{seqs2}")
check("B11-3 未知手段はFalse", ok_bad is False)

seqs3 = []
ar._vk_seq = lambda seq: (seqs3.append(list(seq)), len(list(seq)))[1]
try:
    ok_tt = ar.send_paste("type_text:AB", delay_ms=0)
finally:
    ar._vk_seq = orig_vk2
check("B11-4 type_textは2文字=2シーケンス",
      ok_tt is True and len(seqs3) == 2
      and seqs3[0] == [(0x41, 0), (0x41, 2)] and seqs3[1] == [(0x42, 0), (0x42, 2)], f"{seqs3}")

# B12: Jev/連続投入の撤去確認 (公開シンボルに残っていない)
names = [n.lower() for n in dir(ar)]
check("B12-1 Jev系シンボルなし", not any(n.startswith("jev") for n in names), f"{names}")
check("B12-2 判定/ループ/保存系なし",
      not any(n in names for n in
              ("run_loop", "run_once", "decide_action", "compute_wait", "mark_used", "save_codes")),
      f"{names}")

print(f"\n=== 全テスト PASS ({PASS}件) ===")
