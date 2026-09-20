#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WWM コード入力支援CLI (デスクトップ版) — Win32実入力ヘルパ

前提: ゲームはブラウザではなくデスクトップクライアント (Steam版 wwm.exe)。
入力の実経路は手動 (Alt+G コピー → 人間 Ctrl+V)。本CLIは下見・貼付テスト・
キャプチャの補助。実デスクトップ操作が前提 (ゲーム排他全画面では Alt+Tab 運用)。

使い方:
  python auto_redeem.py --list-windows                 # 可視ウィンドウ一覧 + ゲーム検出
  python auto_redeem.py --dry-run --limit 3            # 次の未使用コードを表示 (保存なし)
  python auto_redeem.py --once                         # 1件貼付テスト (要ゲーム起動・保存なし)
  python auto_redeem.py --shot shot.png --crop center  # 確定直後キャプチャ

安全装置:
- codes.json へ保存する経路は持たない (表示と貼付テストのみ・dry-run相当)。
- 新規パッケージ不要 (stdlib + pywin32 + PIL のみ)。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time
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


def next_unused(codes: list, start: int = 0) -> int:
    for i in range(start, len(codes)):
        if not codes[i].get("used", False):
            return i
    return -1


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


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="WWM コード入力支援CLI (下見・貼付テスト・キャプチャ)")
    ap.add_argument("--list-windows", action="store_true", help="可視ウィンドウ一覧")
    ap.add_argument("--dry-run", action="store_true", help="次の未使用コードを表示 (保存なし)")
    ap.add_argument("--once", action="store_true", help="1件貼付テスト (要ゲーム起動・保存なし)")
    ap.add_argument("--limit", type=int, default=1, help="dry-run表示件数")
    ap.add_argument("--shot", default="", help="指定パスへ確定直後キャプチャを保存 (要ゲーム起動)")
    ap.add_argument("--crop", default="full", choices=("full", "center"),
                    help="キャプチャ範囲 (center=中央切抜き)")
    ap.add_argument("--crop-ratio", type=float, default=0.6, help="中央切抜き比率")
    ap.add_argument("--click-xy", default="", help="入力欄クリック座標 'x,y' (未指定ならクリックなし)")
    ap.add_argument("--tabs", type=int, default=0, help="投入前のTab送出回数 (既定0=なし)")
    ap.add_argument("--paste-method", default="ctrl_v",
                    help="貼付手段: ctrl_v | shift_insert | type_text (既定ctrl_v)")
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
            print("(winocr未導入のためOCR空)")
        return 0

    if args.dry_run or not args.once:
        work = copy.deepcopy(codes)
        n = max(1, args.limit)
        cursor = 0
        shown = 0
        for _ in range(n):
            idx = next_unused(work, start=cursor)
            if idx < 0:
                break
            print(f"next[{idx}]: {work[idx].get('code')} (dry-run: 保存なし)")
            cursor = idx + 1
            shown += 1
        if shown == 0:
            print("done: 未使用なし")
        else:
            print(f"({shown}件表示・保存なし)")
        return 0

    # --once 貼付テスト (ペーストまで実行・保存なし。結果確認は人間)
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
    fcfg = _field_cfg()
    if fcfg:
        ok_f = focus_field(fcfg)
        print(f"field: {'OK' if ok_f else 'NG'} ({fcfg})")
        if not ok_f:
            print("入力欄フォーカス失敗。中止 (未保存)")
            return 2
    if not set_clipboard(code):
        print("clipboard失敗。中止 (未保存)")
        return 2
    ok_v1 = send_paste(args.paste_method)
    print("paste(%s): %s" % (args.paste_method, "OK" if ok_v1 else "NG(送出件数不足)"))
    print(f"pasted: {code}")
    print("結果メッセージはゲーム上で確認してください (マークはGUIで手動)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
