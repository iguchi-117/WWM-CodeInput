#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WWM 自動入力 (デスクトップ版・半自動) — Computer Use + Jev 枠組み

前提: ゲームはブラウザではなくデスクトップクライアント (Steam版 wwm.exe)。
browser-use/jev-ultrafast のような DOM state space は使えないため、
スナップショット = ウィンドウキャプチャ + OCRテキスト → Jev で判定する構成。

パイプライン:
  codes.json 次の未使用 → クリップボード → ゲームへ Ctrl+V →
  結果メッセージ領域をキャプチャ → OCRテキスト化 → Jev 3問並列で判定 →
  outcome/confidence に応じて 次へ / リトライ / 人間確認

使い方 (ゲーム起動なしで試せるもののみ):
  python auto_redeem.py --list-windows
  python auto_redeem.py --result-text "Already used. This code was claimed."
  python auto_redeem.py --dry-run --limit 3
  python auto_redeem.py --once --yes --interval 8   # 実投入 (要ゲーム起動・要目視確認)

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
    """フォーカス中ウィンドウへ Ctrl+V を送る (SendInput相当: keybd_event)。"""
    import win32api
    import win32con

    try:
        VK_C = 0x56
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        time.sleep(0.03)
        win32api.keybd_event(VK_C, 0, 0, 0)
        time.sleep(0.02)
        win32api.keybd_event(VK_C, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(delay_ms / 1000.0)
        return True
    except Exception:
        return False


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


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="WWM 自動入力 (デスクトップ半自動枠組み)")
    ap.add_argument("--list-windows", action="store_true", help="可視ウィンドウ一覧")
    ap.add_argument("--result-text", default="", help="Jev判定ゲートのみ試す (ゲーム不要)")
    ap.add_argument("--dry-run", action="store_true", help="保存せず次コード+判定を表示")
    ap.add_argument("--once", action="store_true", help="1件だけ実投入フロー (要 --yes で保存)")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--interval", type=float, default=8.0, help="件間隔秒 (既定8)")
    ap.add_argument("--conf-floor", type=float, default=0.5)
    ap.add_argument("--yes", action="store_true", help="codes.json へ保存する")
    ap.add_argument("--shot", default="", help="指定ウィンドウをキャプチャ (要ゲーム起動)")
    args = ap.parse_args(argv)

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
        capture_window(gw["handle"], out)
        print("saved:", out)
        text = ocr_image(out)
        print("OCR chars:", len(text))
        if text:
            print(text[:1000])
        else:
            print("(winocr未導入のためOCR空。結果文を --result-text でJev判定へ)")
        return 0

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
    if not set_clipboard(code):
        print("clipboard失敗。中止 (未保存)")
        return 2
    send_ctrl_v()
    print(f"pasted: {code}")
    print("結果メッセージを確認し、--result-text '...' でJev判定 → マークへ進む (半自動)")
    print("※ 全自動連続投入は頻度制限・運用規約の確認後に有効化する")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
