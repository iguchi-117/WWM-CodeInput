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


VK_SPACE = 0x20
VK_ESCAPE = 0x1B
RATE_WAIT_CAP_SEC = 60.0


def send_key(vk: int, delay_ms: int = 100) -> bool:
    """単一キー押下を送出する (Space確定/Esc戻り用。send_ctrl_v と同型: keybd_event)。"""
    import win32api
    import win32con

    try:
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(delay_ms / 1000.0)
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
            if not set_clipboard(code):
                print("clipboard失敗。中止 (未保存分あり)")
                return False
            send_ctrl_v()
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
