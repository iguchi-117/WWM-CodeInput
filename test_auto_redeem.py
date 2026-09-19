"""auto_redeem 連続投入ループの単体テスト (ゲーム・通信不要)。

TDD RED→GREEN用。Jev通信・Win32実入力はすべて注入/スタブ化する。
実行: python test_auto_redeem.py
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import auto_redeem as ar

PASS = 0


def check(name, cond, extra=""):
    global PASS
    assert cond, f"[FAIL] {name} {extra}"
    PASS += 1
    print(f"[PASS] {name}")


def dummy_gate(outcome, conf=1.0):
    return {"outcome": outcome, "confidence": conf, "is_success": 1.0 if outcome == "success" else 0.0,
            "certainty": 0.9, "elapsed_ms": 1, "usage": {}}


def make_codes(n=4, used_first=1):
    return [{"code": f"CODE{i:04d}", "used": i < used_first,
             "added_at": "2026-09-20", "used_at": "2026-09-20" if i < used_first else None,
             "source": "test"} for i in range(n)]


# A1: next_unused 順序
codes = make_codes()
check("A1-1 先頭未使用", ar.next_unused(codes) == 1)
check("A1-2 start指定", ar.next_unused(codes, start=2) == 2)
check("A1-3 全使用済→-1", ar.next_unused([dict(c, used=True) for c in codes]) == -1)

# A2: decide_action 5分岐 (+低conf/human)
cases = [("success", 1.0, "next"), ("already_used", 0.9, "next"), ("expired", 0.8, "next"),
         ("rate_limited", 1.0, "retry_same"), ("other_error", 1.0, "human"),
         ("success", 0.3, "human"), ("?", 1.0, "human")]
check("A2 decide_action 7分岐", all(ar.decide_action(o, c) == e for o, c, e in cases))

# A3: rate待機の倍増と上限60秒
check("A3-1 基本8秒", ar.compute_wait(8.0, 0) == 8.0)
check("A3-2 倍増16秒", ar.compute_wait(8.0, 1) == 16.0)
check("A3-3 倍増32秒", ar.compute_wait(8.0, 2) == 32.0)
check("A3-4 上限制限60秒", ar.compute_wait(8.0, 3) == 60.0 and ar.compute_wait(8.0, 10) == 60.0)

# A4: dry-runは保存もマークもしない (N件先頭表示のみ)
codes = make_codes()
snapshot = copy.deepcopy(codes)
saved = []
orig_save = ar.save_codes
ar.save_codes = lambda c: saved.append(copy.deepcopy(c))
try:
    res = ar.run_loop(codes, limit=3, dry_run=True,
                      input_fn=lambda code: (_ for _ in ()).throw(AssertionError("dry-runで実入力禁止")),
                      sleep_fn=lambda s: None)
finally:
    ar.save_codes = orig_save
check("A4-1 dry-run無保存", saved == [])
check("A4-2 dry-run無マーク", codes == snapshot)
check("A4-3 dry-run 3件表示", [i["code"] for i in res["items"]] == ["CODE0001", "CODE0002", "CODE0003"])
check("A4-4 dry-run停止理由limit", res["stopped"] == "limit")

# A5: yes時のみ next→マーク+保存 (Jevモック: success)
codes = make_codes()
saved = []
ar.save_codes = lambda c: saved.append(copy.deepcopy(c))
sleeps = []
try:
    res = ar.run_loop(codes, limit=2, yes=True, dry_run=False,
                      classify_fn=lambda text: dummy_gate("success"),
                      input_fn=lambda code: True,
                      shot_fn=lambda code: "You received rewards",
                      sleep_fn=sleeps.append)
finally:
    ar.save_codes = orig_save
check("A5-1 2件マーク", [c["code"] for c in codes if c["used"]] == ["CODE0000", "CODE0001", "CODE0002"],
      f"{codes}")
check("A5-2 保存2回", len(saved) == 2)
check("A5-3 件間隔8秒", 8.0 in sleeps, f"{sleeps}")

# A6: humanで停止し勝手に進めない (other_error)
codes = make_codes()
saved = []
ar.save_codes = lambda c: saved.append(True)
try:
    res = ar.run_loop(codes, limit=5, yes=True, dry_run=False,
                      classify_fn=lambda text: dummy_gate("other_error", 0.9),
                      input_fn=lambda code: True,
                      shot_fn=lambda code: "Something went wrong",
                      sleep_fn=lambda s: None)
finally:
    ar.save_codes = orig_save
check("A6-1 human停止", res["stopped"] == "human" and len(res["items"]) == 1)
check("A6-2 human無マーク無保存", saved == [] and not codes[1]["used"])
check("A6-3 ログにOCR文保持", res["items"][0]["ocr"] == "Something went wrong")

# A7: rate_limitedは同コード再試行+待機倍増
codes = make_codes()
seq = ["rate_limited", "rate_limited", "success"]
calls = {"n": 0}
sleeps = []
ar.save_codes = lambda c: None
def seq_classify(text):
    o = seq[min(calls["n"], 2)]
    calls["n"] += 1
    return dummy_gate(o)
orig_save2 = ar.save_codes
try:
    res = ar.run_loop(codes, limit=3, yes=True, dry_run=False, interval=8.0,
                      classify_fn=seq_classify,
                      input_fn=lambda code: True,
                      shot_fn=lambda code: "Operated too frequently",
                      sleep_fn=sleeps.append)
finally:
    ar.save_codes = orig_save
check("A7-1 同コード3試行", [i["code"] for i in res["items"][:3]] == ["CODE0001"] * 3, f"{res['items']}")
check("A7-2 待機倍増 16→32", 16.0 in sleeps and 32.0 in sleeps, f"{sleeps}")
check("A7-3 最後にマーク", codes[1]["used"] is True)

# A8: 中央クロップ幾何
check("A8 中央クロップ", ar.crop_center_box(1920, 1080, 0.6) == (384, 216, 1536, 864))

# A9: capture_result center は実PNGを中央切抜き (PILのみ・ゲーム不要)
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
check("A9 中央切抜き100x50", got == (100, 50), f"{got}")

# A10: send_key は down→up の順で SendInput 統一経路に送出 (_vk_seq注入・実送出なし)
events = []
orig_vk10 = ar._vk_seq
ar._vk_seq = lambda seq: (events.extend(list(seq)), len(list(seq)))[1]
try:
    ok = ar.send_key(0x20, delay_ms=0)
finally:
    ar._vk_seq = orig_vk10
check("A10 send_key Space down/up", ok is True and events == [(0x20, 0), (0x20, 2)], f"{events}")

# A10b: send_ctrl_v は SendInput 統一経路 (vk_seq 注入・実キー送出なし)
seqs = []
orig_vk = ar._vk_seq
ar._vk_seq = lambda seq: (seqs.append(list(seq)), len(seq))[1]
try:
    ok2 = ar.send_ctrl_v(delay_ms=0)
finally:
    ar._vk_seq = orig_vk
check("A10b send_ctrl_vはCtrl down→V down/up→Ctrl up", ok2 is True and seqs and seqs[0] == [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)], f"{seqs}")

# A10c: focus確認ヘルパ (スタブ注入・実ウィンドウ不要)
class _FakeGui:
    def __init__(self, fg, title="Where Winds Meet"):
        self._fg = fg
        self._title = title
    def GetForegroundWindow(self): return self._fg
    def GetWindowText(self, h): return self._title if h == self._fg else "other"
    def ShowWindow(self, h, c): return True
    def SetForegroundWindow(self, h): return True
check("A10c foreground_matches正常", ar.foreground_matches(777, _FakeGui(777)) is True)
check("A10c foreground_matches不一致", ar.foreground_matches(888, _FakeGui(777)) is False)
check("A10c foreground_matches例外時False", ar.foreground_matches(777, None) is False)

# A10d: focus_check は診断dictを返す (win32gui注入・実ウィンドウ不要)
import types as _types
_fg_mod = _types.ModuleType("wwm_fake_gui")
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
check("A10d focus_check診断dict", fc["focused"] is True and fc["fg"] == 777 and isinstance(fc["admin"], bool), f"{fc}")

# A10e: click_at/press_tab (注入・実送出なし)
clicks = []
tabs = []
orig_click = ar.click_at
orig_tab = ar.press_tab
ar.click_at = lambda x, y: (clicks.append((x, y)), True)[1]
ar.press_tab = lambda n=1: (tabs.append(n), True)[1]
try:
    ok_c = ar.click_at(960, 540)
    ok_t = ar.press_tab(2)
finally:
    ar.click_at = orig_click
    ar.press_tab = orig_tab
check("A10e click_at/press_tab注入", ok_c and ok_t and clicks == [(960, 540)] and tabs == [2], f"{clicks} {tabs}")

# A10f: focus_field は click→tab の順で適用し、設定なしなら何もしない
calls = []
ar.click_at = lambda x, y: (calls.append(("click", x, y)), True)[1]
ar.press_tab = lambda n=1: (calls.append(("tab", n)), True)[1]
try:
    r1 = ar.focus_field({"click_xy": [100, 200], "tabs": 1})
    r2 = ar.focus_field({})
finally:
    ar.click_at = orig_click
    ar.press_tab = orig_tab
check("A10f focus_field設定ありはclick→tab", r1 is True and calls == [("click", 100, 200), ("tab", 1)], f"{calls} {r1}")
check("A10f focus_field設定なしはnoop", r2 is True)

# A10g: 貼付手段切替 (paste_method 注入・実送出なし)
seqs2 = []
orig_vk2 = ar._vk_seq
ar._vk_seq = lambda seq: (seqs2.append(list(seq)), len(list(seq)))[1]
try:
    ok_cv = ar.send_paste("ctrl_v", delay_ms=0)
    ok_si = ar.send_paste("shift_insert", delay_ms=0)
    ok_bad = ar.send_paste("unknown_method", delay_ms=0)
finally:
    ar._vk_seq = orig_vk2
check("A10g ctrl_vはCtrl down→V down/up→Ctrl up",
      ok_cv is True and seqs2[0] == [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)], f"{seqs2}")
check("A10g shift_insertはShift down→Insert down/up→Shift up",
      ok_si is True and seqs2[1] == [(0x10, 0), (0x2D, 0), (0x2D, 2), (0x10, 2)], f"{seqs2}")
check("A10g 未知手段はFalse", ok_bad is False)

# A10h: type_text は1文字ずつ down/up で送出 (注入・実送出なし)
seqs3 = []
ar._vk_seq = lambda seq: (seqs3.append(list(seq)), len(list(seq)))[1]
try:
    ok_t = ar.send_paste("type_text:AB", delay_ms=0)
finally:
    ar._vk_seq = orig_vk2
check("A10h type_textは2文字=2シーケンス",
      ok_t is True and len(seqs3) == 2
      and seqs3[0] == [(0x41, 0), (0x41, 2)] and seqs3[1] == [(0x42, 0), (0x42, 2)], f"{seqs3}")

# A11: app.py側 jev_decide_action との一致 (重複片寄せの振る舞い同一)
src_app = (Path(__file__).parent / "app.py").read_text(encoding="utf-8")
pos = src_app.find("def jev_decide_action(")
ns = {"__name__": "wwmtest_jev2"}
end = src_app.find('CODE_RE = ', pos)
exec(src_app[pos:end if end > 0 else pos + 1500], ns)
app_decide = ns["jev_decide_action"]
probes = [("success", 1.0), ("already_used", 0.4), ("expired", 0.9),
          ("rate_limited", 1.0), ("other_error", 1.0), ("?", 0.0), ("success", 0.49)]
check("A11 判定一致7probe", all(app_decide(o, c) == ar.decide_action(o, c) for o, c in probes))

print(f"\n=== 全テスト PASS ({PASS}件) ===")
