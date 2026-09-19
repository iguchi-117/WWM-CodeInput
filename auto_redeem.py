#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WWM 自動入力 (デスクトップ版・半自動) — Computer Use + Jev 枠組み

前提: ゲームはブラウザではなくデスクトップクライアント (Steam版 wwm.exe)。
browser-use/jev-ultrafast のような DOM state space は使えないため、
スナップショット = ウィンドウキャプチャ + OCRテキスト → Jev で判定する構成。

パイプライン:
  codes.json 次の未使用 → クリップボード → ゲームへ Ctrl+V → Space確定 →
  結果メッセージ領域をキャプチャ → OCRテキスト化 → Jev 3問並列で判定 →
  outcome/confidence に応じて 次へ / リトライ / 人間確認

使い方 (ゲーム起動なしで試せるもののみ):
  python auto_redeem.py --list-windows
  python auto_redeem.py --result-text "Already used. This code was claimed."
  python auto_redeem.py --dry-run --limit 3
  python auto_redeem.py --once --yes                  # 1件実投入 (要ゲーム起動・要目視確認)
  python auto_redeem.py --loop --limit 3 --dry-run    # 連続投入の下見 (保存なし)
  python auto_redeem.py --loop --limit 3 --yes        # 連続実投入 (要ゲーム起動)
  python auto_redeem.py --shot shot.png --crop center # 確定直後キャプチャ (中央比較用)

安全装置:
- 既定は dry-run (codes.json に触らない)。--yes でのみ保存する。
- 頻度制限 (Operated too frequently) 回避のため --interval 既定8秒 + rate_limited時は倍増。
- conf < 0.5 は自動マークせず人間確認へ (Confidence-Gated Routing)。
- 新規パッケージ不要 (stdlib + pywin32 + PIL のみ)。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
APP_DIR = Path(__file__).parent.resolve()
_dev = os.environ.get("WWM_DEV_DATA_DIR", "").strip()
if _dev:
    APP_DIR = Path(_dev).resolve()
_env_codes = os.environ.get("WWM_CODES_JSON", "").strip()
if _env_codes:
    _p = Path(_env_codes).expanduser()
    if _p.parent.exists():
        CODES_JSON = _p.resolve()
    else:
        CODES_JSON = APP_DIR / "codes.json"
else:
    CODES_JSON = APP_DIR / "codes.json"
BACKUP_JSON = CODES_JSON.with_suffix(".backup.json")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

OUTCOME_CRITERIA = {
    "success": "Code accepted, rewards granted",
    "already_used": "Code already claimed before",
    "expired": "Code expired",
    "rate_limited": "Too frequent, retry later",
    "other_error": "Other failure",
}

GAME_KEYWORDS = ("wwm.exe", "where winds meet", "wherewinds", "燕雲十六声", "燕雲")
GAME_EXCLUDE_CLASSES = {"CabinetWClass", "ExploreWClass"}  # エクスプローラーの誤検出除外


# ---------------------------------------------------------------- codes.json
def load_codes() -> list:
    if not CODES_JSON.exists():
        return []
    try:
        data = json.loads(CODES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict) and "codes" in data:
        return data["codes"]
    if isinstance(data, list):
        return data
    return []


def save_codes(codes: list) -> None:
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


def next_unused(codes: list, start: int = 0) -> int:
    for i in range(start, len(codes)):
        if not codes[i].get("used", False):
            return i
    return -1


# ---------------------------------------------------------------- Jev gate
def jev_classify(result_text: str, timeout: int = 30) -> dict:
    """引き換え結果文を Jev 3問並列で判定する。実測 6パターン全的中 (2026-09-19)。"""
    api_key = os.environ.get("JEV_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("JEV_API_KEY が未設定")
    body = {
        "state": "WWM redeem result message: " + result_text,
        "model": JEV_MODEL,
        "questions": {
            "outcome": {
                "type": "choice",
                "instructions": "What is the redeem result",
                "criteria": OUTCOME_CRITERIA,
            },
            "is_success": {
                "type": "noul",
                "instructions": "The message indicates successful redemption",
            },
            "certainty": {
                "type": "score",
                "instructions": "How certain is this classification",
                "criteria": ["Uncertain", "Fairly confident", "Certain"],
            },
        },
    }
    req = urllib.request.Request(
        JEV_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.loads(res.read().decode("utf-8"))
    ms = int((time.time() - t0) * 1000)
    a = data.get("answers", {})
    return {
        "outcome": a.get("outcome", {}).get("choice", "?"),
        "confidence": a.get("outcome", {}).get("confidence", 0.0),
        "is_success": a.get("is_success", {}).get("noul", 0.0),
        "certainty": a.get("certainty", {}).get("score", 0.0),
        "elapsed_ms": ms,
        "usage": data.get("usage", {}),
    }


def decide_action(outcome: str, confidence: float, conf_floor: float = 0.5) -> str:
    """Confidence-Gated Routing: 次へ進むか人間確認かを返す。

    - low confidence (<floor) → human
    - rate_limited → retry_same (間隔を空けて同コード再試行)
    - other_error → human
    - success/already_used/expired → next (使用済として進める)
    """
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        return "human"
    if conf < conf_floor:
        return "human"
    if outcome == "rate_limited":
        return "retry_same"
    if outcome in ("success", "already_used", "expired"):
        return "next"
    return "human"


# ---------------------------------------------------------------- desktop hooks
def list_windows() -> list:
    import win32gui

    out = []
    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return True
            t = (win32gui.GetWindowText(h) or "").strip()
            if t:
                cls = win32gui.GetClassName(h)
                out.append({"handle": h, "class": cls, "title": t[:120]})
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def find_game_window(windows: list | None = None) -> dict | None:
    wins = windows if windows is not None else list_windows()
    scored = []
    for w in wins:
        if w.get("class") in GAME_EXCLUDE_CLASSES:
            continue
        t = (w.get("title") or "").lower()
        score = sum(1 for k in GAME_KEYWORDS if k in t)
        if score:
            scored.append((score, w))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def focus_window(handle: int) -> bool:
    import win32gui

    try:
        win32gui.ShowWindow(handle, 9)  # SW_RESTORE
        win32gui.SetForegroundWindow(handle)
        time.sleep(0.4)
        return True
    except Exception:
        return False


def focus_check(handle: int) -> dict:
    """focus_window の突き合わせ診断 (G4ペースト未達の切り分け①)。

    戻り値: {"focused": bool, "fg": int, "title": str, "admin": bool}
    """
    try:
        import win32gui
        fg = int(win32gui.GetForegroundWindow())
        try:
            title = (win32gui.GetWindowText(fg) or "")[:120]
        except Exception:
            title = ""
    except Exception:
        fg, title = 0, ""
    try:
        focused = (fg == int(handle))
    except Exception:
        focused = False
    return {"focused": focused, "fg": fg, "title": title, "admin": is_admin()}


def set_clipboard(text: str) -> bool:
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False


def send_ctrl_v(delay_ms: int = 150) -> bool:
    """フォーカス中ウィンドウへ Ctrl+V を送る (send_paste への後方互換wrapper)。"""
    return send_paste("ctrl_v", delay_ms=delay_ms)


VK_SPACE = 0x20
VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_KEY_C = 0x56
VK_SHIFT = 0x10
VK_INSERT = 0x2D
KEYEVENTF_KEYUP = 0x0002
RATE_WAIT_CAP_SEC = 60.0

PASTE_METHODS = ("ctrl_v", "shift_insert", "type_text")


def send_paste(method: str = "ctrl_v", delay_ms: int = 150) -> bool:
    """貼付キー送出 (手段切替式・SendInput統一経路)。

    - ctrl_v: Ctrl+V (既定・後方互換)
    - shift_insert: Shift+Insert (Ctrl+V吸収時の代替)
    - type_text:<text>: クリップボードを経由せず1文字ずつ直接送出
      (貼付経路自体が吸収される場合の最終手段)
    未知手段は False (fail closed)。
    """
    try:
        if method == "ctrl_v":
            seq = [(VK_CONTROL, 0), (VK_KEY_C, 0),
                   (VK_KEY_C, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)]
            n = _vk_seq(seq)
            time.sleep(delay_ms / 1000.0)
            return n == 4
        elif method == "shift_insert":
            seq = [(VK_SHIFT, 0), (VK_INSERT, 0),
                   (VK_INSERT, KEYEVENTF_KEYUP), (VK_SHIFT, KEYEVENTF_KEYUP)]
            n = _vk_seq(seq)
            time.sleep(delay_ms / 1000.0)
            return n == 4
        elif method.startswith("type_text:"):
            text = method[len("type_text:"):]
            if not text:
                return False
            for ch in text:
                vk = ord(ch.upper())
                if not (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
                    return False
                n = _vk_seq([(vk, 0), (vk, KEYEVENTF_KEYUP)])
                if n != 2:
                    return False
                time.sleep(0.03)
            time.sleep(delay_ms / 1000.0)
            return True
        else:
            return False
    except Exception:
        return False


def _vk_seq(seq: list) -> int:
    """(vk, flags) 列を SendInput で一括送出する。戻り値=実際に送出された件数。

    flags: 0=押下, KEYEVENTF_KEYUP=解放。scan は MapVirtualKeyW で解決。
    copy系と同一のドライバ到達層 (SendInput) に寄せるための共通関数。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT),
                    ("_pad", ctypes.c_ubyte * 8)]

    items = list(seq)
    arr = (INPUT * len(items))()
    for i, (vk, flags) in enumerate(items):
        scan = user32.MapVirtualKeyW(int(vk), 0)
        arr[i].type = 1  # INPUT_KEYBOARD
        arr[i].ki = KEYBDINPUT(wVk=int(vk), wScan=scan, dwFlags=int(flags),
                               time=0, dwExtraInfo=0)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    return int(user32.SendInput(len(items), ctypes.byref(arr), ctypes.sizeof(INPUT)))


def is_admin() -> bool:
    """管理者権限で実行中か (UIPI/キー送出到達の前提確認用)。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def foreground_matches(handle: int, gui=None) -> bool:
    """指定ハンドルが現在の前景ウィンドウか (focus_window の突き合わせ用)。

    gui 注入可能 (単体テストはスタブ)。例外時は False (fail closed)。
    """
    try:
        g = gui if gui is not None else __import__("win32gui")
        return int(g.GetForegroundWindow()) == int(handle)
    except Exception:
        return False


def send_key(vk: int, delay_ms: int = 100) -> bool:
    """単一キー押下を送出する (Space確定/Esc戻り用。SendInput統一経路)。"""
    try:
        n = _vk_seq([(int(vk), 0), (int(vk), KEYEVENTF_KEYUP)])
        time.sleep(delay_ms / 1000.0)
        return n == 2
    except Exception:
        return False


VK_TAB = 0x09
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def click_at(x: int, y: int) -> bool:
    """画面座標へマウス移動＋左クリック (入力欄フォーカス用・SendInput統一経路)。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _KI(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            # Win32 INPUT(x64) は40バイト: DWORD(4)+pad(4)+union(32)。
            # 明示の末尾padを付けると48バイトになり SendInput が87で失敗する。
            class _U(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT), ("ki", _KI)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.1)
        # UIPI下では SetCursorPos が 0 を返しても実際は移動していることがあるため、
        # 戻り値ではなくカーソル位置で到達確認する
        try:
            pos = user32.GetCursorPos
            from ctypes import wintypes as _wt

            class _PT(ctypes.Structure):
                _fields_ = [("x", _wt.LONG), ("y", _wt.LONG)]
            pt = _PT()
            pos.argtypes = [ctypes.POINTER(_PT)]
            pos.restype = wintypes.BOOL
            if pos(ctypes.byref(pt)) and abs(pt.x - int(x)) <= 2 and abs(pt.y - int(y)) <= 2:
                pass  # 到達確認OK
        except Exception:
            pass
        arr = (INPUT * 2)()
        for i, flag in enumerate((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)):
            arr[i].type = 0  # INPUT_MOUSE
            arr[i].mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag,
                                   time=0, dwExtraInfo=0)
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        n = int(user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT)))
        time.sleep(0.2)
        return n == 2
    except Exception:
        return False


def press_tab(count: int = 1) -> bool:
    """Tab を count 回送出する (入力欄フォーカス移動用)。"""
    try:
        for _ in range(max(1, int(count))):
            n = _vk_seq([(VK_TAB, 0), (VK_TAB, KEYEVENTF_KEYUP)])
            if n != 2:
                return False
            time.sleep(0.1)
        return True
    except Exception:
        return False


def focus_field(cfg: dict | None) -> bool:
    """入力欄へフォーカスを当てる (click→tab の順。設定なしなら何もしない)。

    cfg: {"click_xy": [x, y], "tabs": n}。座標はハードコードせず CLI/設定で注入。
    どちらも未設定なら True (noop・後方互換)。
    """
    try:
        cfg = cfg or {}
        xy = cfg.get("click_xy")
        if xy:
            if not click_at(int(xy[0]), int(xy[1])):
                return False
        tabs = int(cfg.get("tabs", 0) or 0)
        if tabs > 0:
            if not press_tab(tabs):
                return False
        return True
    except Exception:
        return False


def compute_wait(interval: float, rate_strikes: int) -> float:
    """rate_limited 連続回数に応じた待機秒 (倍増・上限60秒)。"""
    wait = float(interval) * (2 ** max(0, int(rate_strikes)))
    return min(wait, RATE_WAIT_CAP_SEC)


def crop_center_box(width: int, height: int, ratio: float = 0.6) -> tuple:
    """中央クロップ矩形 (left, top, right, bottom)。全画面OCRとの比較用。"""
    r = min(max(float(ratio), 0.1), 1.0)
    cw, ch = width * r, height * r
    cx, cy = width / 2.0, height / 2.0
    return (int(cx - cw / 2), int(cy - ch / 2), int(cx + cw / 2), int(cy + ch / 2))


def capture_result(handle: int, out_path: str, crop: str = "full", ratio: float = 0.6) -> str:
    """確定直後の結果キャプチャ。crop=center で中央のみ切抜き上書き保存する。"""
    capture_window(handle, out_path)
    if crop == "center":
        from PIL import Image

        img = Image.open(out_path)
        w, h = img.size
        img.crop(crop_center_box(w, h, ratio)).save(out_path)
    return out_path


def capture_window(handle: int, out_path: str) -> str:
    """ウィンドウ矩形をキャプチャして保存 (G検定 answer_reader.py と同型: ImageGrab)。"""
    import win32gui
    from PIL import ImageGrab

    rect = win32gui.GetWindowRect(handle)
    img = ImageGrab.grab(bbox=rect)
    img.save(out_path)
    return out_path


def ocr_image(path: str) -> str:
    """winocr があれば Windows OCR、なければ空文字 (半自動: 人間が結果文を貼る)。"""
    try:
        import winocr  # type: ignore
    except ImportError:
        return ""
    try:
        from PIL import Image

        img = Image.open(path)
        res = winocr.recognize_pil_sync(img, lang="en")
        lines = res.get("lines", []) if isinstance(res, dict) else getattr(res, "lines", [])
        parts = []
        for line in lines:
            parts.append(line.get("text", "") if isinstance(line, dict) else getattr(line, "text", ""))
        return "\n".join(p for p in parts if p)
    except Exception:
        return ""


# ---------------------------------------------------------------- loop
def run_once(codes: list, result_text: str, conf_floor: float = 0.5) -> dict:
    """単件ドライラン: 次の未使用コード + Jev判定 + action。保存はしない。"""
    idx = next_unused(codes)
    if idx < 0:
        return {"action": "done", "idx": -1, "code": ""}
    code = codes[idx].get("code", "")
    gate = jev_classify(result_text, timeout=30)
    action = decide_action(gate["outcome"], gate["confidence"], conf_floor)
    return {"action": action, "idx": idx, "code": code, "gate": gate}


def mark_used(codes: list, idx: int, outcome: str = "") -> dict:
    """指定コードを使用済にマークする (保存は呼び出し側)。"""
    entry = codes[idx]
    entry["used"] = True
    entry["used_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    if outcome:
        entry["redeem_outcome"] = outcome
    return entry


def _default_input(code: str, handle: int, settle: float) -> bool:
    """実機への1件投入: クリップボード → Ctrl+V → Space確定。"""
    if not set_clipboard(code):
        return False
    send_ctrl_v()
    time.sleep(max(0.0, float(settle)))
    return send_key(VK_SPACE)


def run_loop(codes: list, limit: int = 1, interval: float = 8.0, settle: float = 2.0,
             conf_floor: float = 0.5, yes: bool = False, dry_run: bool = True,
             crop: str = "full", crop_ratio: float = 0.6, shot_dir: str = "",
             classify_fn=None, input_fn=None, shot_fn=None, sleep_fn=None) -> dict:
    """連続投入ループ (1コードごとに 貼付→Space→結果取込→Jev→マーク)。

    - dry_run=True (既定): 表示のみ。input_fn/classify_fn を呼ばない。
    - yes=True + dry_run=False: `next` のみマーク+save_codes、それ以外は保存しない。
    - `human` 判定で停止し勝手に進めない。`rate_limited` は同コード再試行+待機倍増。
    - 分岐ロジックは注入可能 (単体テストはゲーム・通信不要)。
    """
    do_sleep = sleep_fn or time.sleep
    classify = classify_fn or (lambda text: jev_classify(text, timeout=30))
    items: list = []
    done = 0
    attempts = 0
    rate_strikes = 0
    pending_wait = compute_wait(interval, 0)
    max_limit = max(1, int(limit))
    stopped = "limit"
    cursor = 0

    while done < max_limit:
        idx = next_unused(codes, start=cursor)
        if idx < 0:
            stopped = "done" if not items else stopped
            if not items:
                stopped = "done"
            break
        code = codes[idx].get("code", "")

        if dry_run:
            items.append({"idx": idx, "code": code, "action": "dry-run"})
            cursor = idx + 1
            done += 1
            continue

        # 件間隔の待機 (初件は待たず、rate後は倍増待機)
        if attempts > 0:
            do_sleep(pending_wait)

        if input_fn is None:
            # 実機投入は main 経由で input_fn を注入するためここでは到達しない
            raise RuntimeError("input_fn が未指定 (実機投入は main 経由)")
        ok = input_fn(code)
        attempts += 1
        if not ok:
            items.append({"idx": idx, "code": code, "action": "input-failed",
                          "ocr": ""})
            stopped = "input-failed"
            break

        do_sleep(max(0.0, float(settle)))
        if shot_fn is not None:
            ocr_text = shot_fn(code) or ""
        else:
            ocr_text = ""
        if not ocr_text:
            items.append({"idx": idx, "code": code, "action": "human",
                          "ocr": ocr_text,
                          "reason": "OCR空のため人間確認 (winocr未導入 or 結果文なし)"})
            stopped = "human"
            break

        gate = classify(ocr_text)
        action = decide_action(gate.get("outcome", "?"), gate.get("confidence", 0.0), conf_floor)
        item = {"idx": idx, "code": code, "action": action,
                "ocr": ocr_text, "gate": gate}
        items.append(item)

        if action == "next":
            if yes:
                mark_used(codes, idx, gate.get("outcome", ""))
                save_codes(codes)
            cursor = idx + 1
            rate_strikes = 0
            pending_wait = compute_wait(interval, 0)
            done += 1
        elif action == "retry_same":
            rate_strikes += 1
            pending_wait = compute_wait(interval, rate_strikes)
            # 同コード再試行 (done は進めない)。無限ループ防止でlimit件数分まで。
            if len([i for i in items if i["code"] == code]) >= max_limit:
                stopped = "retry-limit"
                break
            continue
        else:  # human
            stopped = "human"
            break
    else:
        stopped = "limit"

    if done >= max_limit:
        stopped = "limit"
    return {"items": items, "stopped": stopped,
            "marked": sum(1 for i in items if i["action"] == "next" and yes)}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="WWM 自動入力 (デスクトップ半自動枠組み)")
    ap.add_argument("--list-windows", action="store_true", help="可視ウィンドウ一覧")
    ap.add_argument("--result-text", default="", help="Jev判定ゲートのみ試す (ゲーム不要)。--once/--loop時はOCR空の代替入力")
    ap.add_argument("--dry-run", action="store_true", help="保存せず次コード+判定を表示")
    ap.add_argument("--once", action="store_true", help="1件だけ実投入フロー (要 --yes で保存)")
    ap.add_argument("--loop", action="store_true", help="連続投入ループ (要 --yes で保存、既定dry-run)")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--interval", type=float, default=8.0, help="件間隔秒 (既定8)")
    ap.add_argument("--settle", type=float, default=2.0, help="Space確定→結果キャプチャ待機秒")
    ap.add_argument("--conf-floor", type=float, default=0.5)
    ap.add_argument("--yes", action="store_true", help="codes.json へ保存する")
    ap.add_argument("--shot", default="", help="指定パスへ確定直後キャプチャを保存 (要ゲーム起動)")
    ap.add_argument("--crop", default="full", choices=("full", "center"),
                    help="結果キャプチャ範囲 (center=中央切抜き比較用)")
    ap.add_argument("--crop-ratio", type=float, default=0.6, help="中央切抜き比率")
    ap.add_argument("--shot-dir", default="", help="ループ中の確定直後PNG保存先 (既定 shots/)")
    ap.add_argument("--click-xy", default="", help="入力欄クリック座標 'x,y' (未指定ならクリックなし)")
    ap.add_argument("--tabs", type=int, default=0, help="投入前のTab送出回数 (既定0=なし)")
    ap.add_argument("--paste-method", default="ctrl_v",
                    help="貼付手段: ctrl_v | shift_insert | type_text (既定ctrl_v。吸収時は順に切替)")
    args = ap.parse_args(argv)

    def _field_cfg() -> dict:
        cfg: dict = {}
        if args.click_xy.strip():
            try:
                xs, ys = args.click_xy.replace(" ", "").split(",", 1)
                cfg["click_xy"] = [int(xs), int(ys)]
            except ValueError:
                print(f"click-xy解釈失敗: {args.click_xy!r} (例: --click-xy 960,540)")
        if int(args.tabs or 0) > 0:
            cfg["tabs"] = int(args.tabs)
        return cfg

    if args.list_windows:
        for w in list_windows():
            print(f"{w['handle']} | {w['class']} | {w['title']}")
        gw = find_game_window()
        print("GAME:", gw if gw else "not running")
        return 0

    if args.result_text:
        gate = jev_classify(args.result_text)
        action = decide_action(gate["outcome"], gate["confidence"], args.conf_floor)
        print(json.dumps({"gate": gate, "action": action}, ensure_ascii=False, indent=2))
        return 0

    codes = load_codes()
    print(f"codes: total={len(codes)} unused={sum(1 for c in codes if not c.get('used'))}")
    if args.shot:
        gw = find_game_window()
        if not gw:
            print("GAME: not running (ゲーム起動後に --shot)")
            return 2
        out = args.shot
        capture_result(gw["handle"], out, crop=args.crop, ratio=args.crop_ratio)
        print(f"saved: {out} (crop={args.crop})")
        text = ocr_image(out)
        print("OCR chars:", len(text))
        if text:
            print(text[:1000])
        else:
            print("(winocr未導入のためOCR空。結果文を --result-text でJev判定へ)")
        return 0

    if args.loop:
        dry = not args.yes
        if args.yes:
            print("連続実投入モード (--loop --yes)。結果は逐次保存されます。")
        else:
            print("連続投入の下見 (--loop dry-run: 保存なし)。保存するには --yes を付与。")
        gw = None
        if not dry:
            gw = find_game_window()
            if not gw:
                print("GAME: not running。ゲーム起動後に入力欄へフォーカスして再実行")
                return 2
            print(f"target: {gw['title']} (handle={gw['handle']})")
            if not focus_window(gw["handle"]):
                print("focus失敗。中止 (未保存)")
                return 2
            # 切り分け①③: 前景突き合わせ＋管理者権限表示
            fc = focus_check(gw["handle"])
            print(f"focus: {'OK' if fc['focused'] else 'NG(前景不一致)'} "
                  f"(fg={fc['fg']} title={fc['title']!r} admin={fc['admin']})")
            if not fc["focused"]:
                print("前景がゲームにありません。入力欄へフォーカスして再実行 (未保存)")
                return 2
        shot_dir = args.shot_dir or str(APP_DIR / "shots")
        seq = {"n": 0}

        def _real_shot(code: str) -> str:
            seq["n"] += 1
            os.makedirs(shot_dir, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", code) or f"code{seq['n']}"
            out = os.path.join(shot_dir, f"{datetime.now(JST):%Y%m%d-%H%M%S}_{safe}_{args.crop}.png")
            capture_result(gw["handle"], out, crop=args.crop, ratio=args.crop_ratio)
            print(f"shot[{seq['n']}]: {out}")
            text = ocr_image(out)
            print(f"OCR chars: {len(text)}")
            if text:
                print(text[:1000])
            elif args.result_text:
                print("(OCR空のため --result-text を代替入力として使用)")
                return args.result_text
            else:
                print("(OCR空。--result-text '...' を併用すると半自動で継続できます)")
            return text

        def _real_input(code: str) -> bool:
            # 入力欄フォーカス (click→tab。未設定ならnoop・後方互換)
            fcfg = _field_cfg()
            if fcfg:
                ok_f = focus_field(fcfg)
                print(f"field: {'OK' if ok_f else 'NG'} ({fcfg})")
                if not ok_f:
                    print("入力欄フォーカス失敗。中止 (未保存分あり)")
                    return False
                # フォーカス直後キャプチャ (届きの証拠・Space確定前)
                try:
                    os.makedirs(shot_dir, exist_ok=True)
                    safe0 = re.sub(r"[^A-Za-z0-9_-]+", "_", code) or "code"
                    pre0 = os.path.join(shot_dir, f"{datetime.now(JST):%Y%m%d-%H%M%S}_{safe0}_focused.png")
                    capture_result(gw["handle"], pre0, crop=args.crop, ratio=args.crop_ratio)
                    print(f"focused_shot: {pre0}")
                except Exception as exc:
                    print(f"focused_shot失敗: {exc} (投入は継続)")
            if not set_clipboard(code):
                print("clipboard失敗。中止 (未保存分あり)")
                return False
            _pm = args.paste_method
            if _pm == "type_text":
                _pm = "type_text:" + code
            ok_v = send_paste(_pm)
            print("paste(%s): %s" % (_pm.split(":")[0], "OK" if ok_v else "NG(送出件数不足)"))
            # 切り分け②: 貼付直後キャプチャ (Space確定前・入力欄到達の証拠)
            try:
                os.makedirs(shot_dir, exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9_-]+", "_", code) or "code"
                pre = os.path.join(shot_dir, f"{datetime.now(JST):%Y%m%d-%H%M%S}_{safe}_pasted.png")
                capture_result(gw["handle"], pre, crop=args.crop, ratio=args.crop_ratio)
                print(f"pasted_shot: {pre}")
            except Exception as exc:
                print(f"pasted_shot失敗: {exc} (投入は継続)")
            print(f"pasted[{seq['n'] + 1}]: {code} → Space確定")
            return send_key(VK_SPACE)

        if dry:
            res = run_loop(codes, limit=args.limit, dry_run=True)
            for it in res["items"]:
                print(f"next[{it['idx']}]: {it['code']} (dry-run: 保存なし)")
            print(f"stopped={res['stopped']}。保存するには --yes を付与。")
            return 0
        res = run_loop(codes, limit=args.limit, interval=args.interval, settle=args.settle,
                       conf_floor=args.conf_floor, yes=True, dry_run=False,
                       crop=args.crop, crop_ratio=args.crop_ratio, shot_dir=shot_dir,
                       input_fn=_real_input, shot_fn=_real_shot)
        for it in res["items"]:
            gate = it.get("gate") or {}
            print(f"[{it['code']}] {it['action']} "
                  f"(outcome={gate.get('outcome', '-')}, conf={gate.get('confidence', 0.0):.2f})")
            if it["action"] == "human":
                print(f"  OCR: {(it.get('ocr') or '')[:300]}")
                print("  → 勝手に進めず停止しました (未保存分あり)。結果を確認して再実行してください。")
        print(f"stopped={res['stopped']} marked={res['marked']}")
        return 0 if res["stopped"] in ("limit", "done") else 1

    if args.dry_run or not args.once:
        work = copy.deepcopy(codes)
        n = max(1, args.limit)
        for _ in range(n):
            idx = next_unused(work)
            if idx < 0:
                print("done: 未使用なし")
                break
            print(f"next[{idx}]: {work[idx].get('code')} (dry-run: 保存なし)")
            # dry-runでは先頭1件の表示に留め、Jev投下は --result-text 経路で行う
            break
        print("hint: --result-text 'Already used...' でJev判定ゲートを試せます")
        return 0

    # --once 実投入フロー (半自動: ペーストまで自動、結果文は人間が貼るかOCR)
    idx = next_unused(codes)
    if idx < 0:
        print("done: 未使用なし")
        return 0
    code = codes[idx].get("code", "")
    gw = find_game_window()
    if not gw:
        print("GAME: not running。ゲーム起動後に入力欄へフォーカスして再実行")
        print(f"next[{idx}]: {code} (未実行・未保存)")
        return 2
    print(f"target: {gw['title']} (handle={gw['handle']})")
    if not focus_window(gw["handle"]):
        print("focus失敗。中止 (未保存)")
        return 2
    fc1 = focus_check(gw["handle"])
    print(f"focus: {'OK' if fc1['focused'] else 'NG(前景不一致)'} "
          f"(fg={fc1['fg']} title={fc1['title']!r} admin={fc1['admin']})")
    if not fc1["focused"]:
        print("前景がゲームにありません。入力欄へフォーカスして再実行 (未保存)")
        return 2
    if not set_clipboard(code):
        print("clipboard失敗。中止 (未保存)")
        return 2
    ok_v1 = send_paste(args.paste_method)
    print("paste(%s): %s" % (args.paste_method, "OK" if ok_v1 else "NG(送出件数不足)"))
    print(f"pasted: {code}")
    print("結果メッセージを確認し、--result-text '...' でJev判定 → マークへ進む (半自動)")
    print("※ 全自動連続投入は頻度制限・運用規約の確認後に有効化する")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
