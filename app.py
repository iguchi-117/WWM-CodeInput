"""
WWM コード高速入力ツール (WWM Code Input Helper)
=================================================
ゲーム内コード入力ページを高速に埋めるためのデスクトップアプリ。

【主要機能】
- コード一覧 (codes.json) を ttk.Treeview で表示
- 選択行で Ctrl+C → コードをクリップボードにコピー
- グローバルホットキー Ctrl+G → 「次のコード」を自動コピー
  (※ Ctrl+V はクリップボードを変更しないためホットキーで対応)
- Alt+Tab でアプリにフォーカスが戻ったとき「次の準備完了」表示
- コピー済みコードは「使用済み」マーク + codes.json を自動更新
- コンテキストメニュー: 使用済に戻す / コード追加 / 削除
- アプリ起動中、codes.json の外部変更を 5秒ごとに再読込

【ホットキー一覧】
- アプリ内: Ctrl+C        選択コードをコピー
- アプリ内: Delete         選択コードを使用済に
- アプリ内: Insert         新規コード追加ダイアログ
- グローバル: Ctrl+G       「次の未使用コード」を自動コピー
- グローバル: Ctrl+Shift+G 選択行を「使用済」にして次へ

【実行方法】
- 標準 Python 3.11+ (Windows) で追加インストール不要
- python app.py
"""
import json
import os
import re
import sys
import time
import webbrowser
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# keyboard ライブラリ (グローバルキーボードフック)
try:
    from pynput import keyboard as pynput_keyboard
    # pynput 内部ログを抑制 (stderr に '[pynput] o^s: ...' が出る)
    import logging
    logging.getLogger("pynput").setLevel(logging.WARNING)
    _KEYBOARD_AVAILABLE = True
except ImportError:
    _KEYBOARD_AVAILABLE = False

# pynput 用コールバック (app.py の _kb_next_callback 設定)
_kb_next_callback = None
_kb_copy_paste_callback = None
_keyboard_handles = []  # pynput GlobalHotKeys インスタンスを保持


def modvk_to_hotkey_name(mod: int, vk: int) -> str:
    """(mod, vk) → 'alt+shift+g' 形式 (keyboard.add_hotkey 用)"""
    parts = []
    if mod & MOD_CONTROL: parts.append("ctrl")
    if mod & MOD_SHIFT:   parts.append("shift")
    if mod & MOD_ALT:     parts.append("alt")
    if mod & MOD_WIN:     parts.append("windows")
    # キー名逆引き
    for name, code in VK_MAP.items():
        if code == vk:
            parts.append(name.lower())
            break
    else:
        # 数字キー
        if 0x30 <= vk <= 0x39:
            parts.append(chr(vk))
        else:
            parts.append(f"0x{vk:x}")
    return "+".join(parts)

# ---------------------------------------------------------------------------
# 定数・パス
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).parent.resolve()

# PyInstaller (--onefile) 対応:
# --onefile で固めた exe は実行時にテンポラリフォルダ (_MEIPASS) で展開されるため、
# データファイル (codes.json) は exe と同じフォルダに置きたい。
# 通常: sys.executable の親 (= exe と同じフォルダ)
# テスト時: 環境変数 WWM_APP_DIR または引数 --app-dir=PATH で上書き可能
# 開発一元化: 環境変数 WWM_DEV_DATA_DIR があればその場所を最優先で使う
import sys as _sys
# 開発一元化の指定 (frozen か否かに関わらず最優先)
_dev_data_dir = os.environ.get("WWM_DEV_DATA_DIR", "")
if _dev_data_dir:
    APP_DIR = Path(_dev_data_dir).resolve()
elif getattr(_sys, "frozen", False):
    _app_dir_override = os.environ.get("WWM_APP_DIR", "")
    if not _app_dir_override:
        for arg in _sys.argv[1:]:
            if arg.startswith("--app-dir="):
                _app_dir_override = arg.split("=", 1)[1]
                break
    if _app_dir_override:
        APP_DIR = Path(_app_dir_override).resolve()
    else:
        APP_DIR = Path(_sys.executable).parent.resolve()
else:
    # 開発モード (app.py 直接実行): WWM_APP_DIR 環境変数も見る
    _app_dir_override = os.environ.get("WWM_APP_DIR", "")
    if not _app_dir_override:
        for arg in _sys.argv[1:]:
            if arg.startswith("--app-dir="):
                _app_dir_override = arg.split("=", 1)[1]
                break
    if _app_dir_override:
        APP_DIR = Path(_app_dir_override).resolve()

CODES_JSON = APP_DIR / "codes.json"
BACKUP_JSON = APP_DIR / "codes.backup.json"

# テスト用: APP_DIR をデバッグログ
_TEST_SOCKET_ON = (os.environ.get("WWM_TEST_SOCKET", "0") == "1") or any(
    arg == "--test-socket" for arg in _sys.argv[1:]
)
if _TEST_SOCKET_ON:
    try:
        with open(APP_DIR / "_dbg.txt", "w", encoding="utf-8") as f:
            f.write(f"APP_DIR={APP_DIR}\n")
            f.write(f"frozen={getattr(_sys, 'frozen', False)}\n")
            f.write(f"executable={_sys.executable}\n")
            f.write(f"__file__={__file__}\n")
            f.write(f"argv={_sys.argv}\n")
            f.write(f"sys._MEIPASS={getattr(_sys, '_MEIPASS', 'NO')}\n")
            f.write(f"WWM_APP_DIR env={os.environ.get('WWM_APP_DIR', '')}\n")
            f.write(f"WWM_TEST_SOCKET env={os.environ.get('WWM_TEST_SOCKET', '')}\n")
            f.write(f"CODES_JSON={CODES_JSON}\n")
            f.write(f"CODES_JSON exists={CODES_JSON.exists()}\n")
    except Exception as e:
        try:
            with open(APP_DIR / "_dbg_err.txt", "w", encoding="utf-8") as f:
                f.write(f"EXCEPTION: {e}\n")
        except Exception:
            pass

JST = timezone(timedelta(hours=9))

# Win32 グローバルホットキー定数
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
VK_G = 0x47

# デフォルトホットキー (Alt+G / Alt+Shift+G — Ctrl+G は Windows が Xbox Game Bar 用に予約済)
DEFAULT_HOTKEY_NEXT = (MOD_ALT, VK_G)
DEFAULT_HOTKEY_USED = (MOD_ALT | MOD_SHIFT, VK_G)

HOTKEY_ID_NEXT = 1
HOTKEY_ID_USED = 2

# アプリ内ホットキー (tkinter キー名) デフォルト
DEFAULT_KEY_NEXT = "<Alt-g>"
DEFAULT_KEY_USED = "<Alt-Shift-g>"

# ポーリング間隔
CLIPBOARD_POLL_MS = 500      # クリップボードポーリング (ms)
JSON_RELOAD_MS = 5000        # 外部JSON再読込間隔 (ms)
WINDOW_POLL_MS = 200         # ホットキーメッセージ処理間隔 (ms)


# ---------------------------------------------------------------------------
# JSON I/O (アトミック書き込み)
# ---------------------------------------------------------------------------
SETTINGS_JSON = APP_DIR / "settings.json"


def load_settings() -> dict:
    """settings.json を読み込む。存在しなければデフォルト値を返す。"""
    defaults = {
        "version": 1,
        "hotkey_next": "Alt+G",  # 次のコードをコピー
        "hotkey_copy_paste": "Alt+Shift+G",  # 次のコードをコピー + 自動ペースト (Ctrl+V)
        "auto_paste_enabled": False,  # 自動ペースト機能 (Ctrl+V) を使うか
        "auto_paste_delay_ms": 100,   # コピー後、ペーストまでの遅延 (ms)
        "codes_json_path": "",        # 任意の codes.json ファイルパス (空ならデフォルト)
    }
    if not SETTINGS_JSON.exists():
        return defaults
    try:
        text = SETTINGS_JSON.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return defaults
        # 足りないキーをデフォルトで補完
        for k, v in defaults.items():
            data.setdefault(k, v)
        # 旧バージョンの hotkey_used キーは無視 (もう使わない)
        data.pop("hotkey_used", None)
        return data
    except (json.JSONDecodeError, OSError):
        return defaults


def save_settings(settings: dict) -> None:
    """settings.json をアトミックに書き出す。"""
    payload = {
        "version": 1,
        "updated_at": datetime.now(JST).isoformat(),
        **settings,
    }
    tmp = SETTINGS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if SETTINGS_JSON.exists():
        try:
            SETTINGS_JSON.with_suffix(".json.bak").write_text(
                SETTINGS_JSON.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass
    os.replace(tmp, SETTINGS_JSON)


# ---------------------------------------------------------------------------
# ホットキー文字列パース (例: "Alt+Shift+G" → (MOD_ALT|MOD_SHIFT, VK_G))
# ---------------------------------------------------------------------------
# よく使う仮想キーコード (アルファベット大文字のみ)
VK_MAP = {chr(c): c - ord('A') + 0x41 for c in range(ord('A'), ord('Z') + 1)}
VK_MAP.update({
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "ESCAPE": 0x1B, "TAB": 0x09, "SPACE": 0x20,
    "INSERT": 0x2D, "INS": 0x2D, "DELETE": 0x2E, "DEL": 0x2E,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "PAUSE": 0x13, "BREAK": 0x13, "SCROLL": 0x91, "SCROLLLOCK": 0x91,
    "PRINTSCREEN": 0x2C, "PRTSC": 0x2C,
    "BACKSPACE": 0x08, "BS": 0x08,
    "ENTER": 0x0D, "RETURN": 0x0D,
    "GRAVE": 0xC0,  # `
    "TAB": 0x09,
})

MODIFIER_MAP = {
    "CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL,
    "SHIFT": MOD_SHIFT,
    "ALT": MOD_ALT,
    "WIN": MOD_WIN, "WINDOWS": MOD_WIN, "META": MOD_WIN,
}


def parse_hotkey(spec: str):
    """'Alt+Shift+G' → (MOD_ALT|MOD_SHIFT, VK_G) を返す。失敗時は None。"""
    if not spec or not isinstance(spec, str):
        return None
    parts = [p.strip().upper() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    modifiers = 0
    key_part = None
    for p in parts:
        if p in MODIFIER_MAP:
            modifiers |= MODIFIER_MAP[p]
        else:
            if key_part is not None:
                # 2つ以上のキー部分は不正
                return None
            key_part = p
    if key_part is None:
        return None
    vk = VK_MAP.get(key_part)
    if vk is None:
        # 数字キー
        if key_part.isdigit():
            num = int(key_part)
            if 0 <= num <= 9:
                vk = 0x30 + num
        elif len(key_part) == 1:
            ch = key_part[0]
            if 'A' <= ch <= 'Z':
                vk = ord(ch)
            elif '0' <= ch <= '9':
                vk = ord(ch)
    if vk is None:
        return None
    return (modifiers, vk)


def hotkey_to_display(spec: str) -> str:
    """'Alt+Shift+G' → 'Alt + Shift + G' (表示用)"""
    if not spec:
        return ""
    return " + ".join(p.strip().upper() for p in spec.split("+"))


def is_hotkey_available(mod: int, vk: int) -> bool:
    """指定のホットキーが登録可能かチェック (pynput ライブラリ使用)。"""
    if not _KEYBOARD_AVAILABLE:
        return True
    try:
        # pynput の場合、登録してみないと分からないので常に True を返す
        # 実際のエラーは register_global_hotkeys で検出する
        return True
    except Exception:
        return False


# Windows 標準ショートカットのリスト
# 自動ペースト機能 (Ctrl+V 送信) と組み合わせると、Windows 側の標準機能で
# 重複発火する可能性があるため、ユーザーに警告する
# 形式: (mod_mask, vk_code) → 説明
WINDOWS_RESERVED_HOTKEYS = {
    # Ctrl+Shift+V = "書式なしで貼り付け" (Edge/Office/多数のアプリで発動)
    # 自動ペーストで Ctrl+V を送る際、元のキーが Ctrl+Shift+V だと
    # アプリ発動 → Ctrl+V 送信 → Windows が Ctrl+Shift+V として別途発動 → 2回ペースト
    (MOD_CONTROL | MOD_SHIFT, 0x56): "Windows: 書式なしで貼り付け (Ctrl+Shift+V)",
    (MOD_CONTROL | MOD_SHIFT, 0x43): "Windows: コピー (Ctrl+Shift+C は多くのアプリで標準)",
    (MOD_CONTROL | MOD_SHIFT, 0x58): "Windows: 切り取り (Ctrl+Shift+X)",  # 念のため
    (MOD_CONTROL | MOD_SHIFT, 0x5A): "Windows: やり直し (Ctrl+Shift+Z)",  # 念のため
    # Ctrl+Shift+S = "名前を付けて保存" (多くのアプリ)
    (MOD_CONTROL | MOD_SHIFT, 0x53): "Windows: 名前を付けて保存 (Ctrl+Shift+S)",
    # Ctrl+Shift+N = "新規ウィンドウ/シークレットモード" (ブラウザ)
    (MOD_CONTROL | MOD_SHIFT, 0x4E): "ブラウザ: 新規シークレットウィンドウ (Ctrl+Shift+N)",
    # Ctrl+Shift+T = "閉じたタブを再度開く" (ブラウザ)
    (MOD_CONTROL | MOD_SHIFT, 0x54): "ブラウザ: 閉じたタブを再度開く (Ctrl+Shift+T)",
    # Ctrl+Shift+Delete = "閲覧履歴削除" (ブラウザ/Windows)
    (MOD_CONTROL | MOD_SHIFT, 0x2E): "Windows: 閲覧履歴の削除 (Ctrl+Shift+Delete)",
    # Alt+Tab 系は基本発動しないが、記録用に
    # Win+Shift+S = "スクリーンショット" (Windows)
}


def is_windows_reserved_hotkey(mod: int, vk: int) -> str:
    """指定のホットキーがWindows標準ショートカットと重複していたら説明文字列を返す。
    重複していなければ空文字列。
    自動ペースト機能との組み合わせで重複発火する可能性があるものを検出。
    """
    if not mod or not vk:
        return ""
    # 部分一致: mod_mask が一部の組み合わせでも警告 (例: Ctrl+Shift+V でなく Ctrl+V も警告)
    description = WINDOWS_RESERVED_HOTKEYS.get((mod, vk), "")
    if description:
        return description
    # 自動ペーストが ON の場合、Ctrl+V も警告対象
    # (Ctrl+V は SendInput したペーストと干渉しないが、念のため)
    return ""


def _to_pynput_hotkey(hotkey_str: str) -> str:
    """'alt+shift+g' → '<alt>+<shift>+g' (pynput GlobalHotKeys 用)

    修飾子 (ctrl/alt/shift/cmd/windows) と特殊キー (F1-F24, esc, tab, etc.) は <...> で囲む。
    通常キー (a-z, 0-9, ...) はそのまま。
    例: 'alt+g' → '<alt>+g', 'ctrl+shift+f8' → '<ctrl>+<shift>+<f8>'
    """
    # pynput が認識する特殊キー一覧
    special_keys = {
        "alt", "alt_l", "alt_r", "alt_gr", "backspace", "caps_lock", "cmd", "cmd_r",
        "ctrl", "ctrl_l", "ctrl_r", "delete", "down", "end", "enter", "esc",
        "home", "left", "page_down", "page_up", "right", "shift", "shift_r",
        "space", "tab", "up", "insert", "menu", "num_lock", "pause",
        "print_screen", "scroll_lock", "media_play_pause", "media_stop",
        "media_volume_mute", "media_volume_down", "media_volume_up",
        "media_previous", "media_next",
    }
    # F1-F24 を追加
    for i in range(1, 25):
        special_keys.add(f"f{i}")

    parts = [p.strip() for p in hotkey_str.split("+") if p.strip()]
    out = []
    for p in parts:
        p_low = p.lower()
        if p_low in ("ctrl", "control"):
            out.append("<ctrl>")
        elif p_low == "alt":
            out.append("<alt>")
        elif p_low == "shift":
            out.append("<shift>")
        elif p_low in ("cmd", "windows", "win"):
            out.append("<cmd>")
        elif p_low in special_keys:
            out.append(f"<{p_low}>")
        else:
            out.append(p)
    return "+".join(out)


# ---------------------------------------------------------------------------
# ホットキー文字列 → tkinter キー名
# ---------------------------------------------------------------------------
def hotkey_to_tkinter(spec: str) -> str:
    """'Alt+Shift+G' → '<Alt-Shift-Key-g>' (tkinter bind 用)"""
    if not spec:
        return ""
    parts = [p.strip().upper() for p in spec.replace(" ", "").split("+") if p.strip()]
    modifiers = []
    key = None
    for p in parts:
        if p in ("CTRL", "CONTROL"):
            modifiers.append("Control")
        elif p == "SHIFT":
            modifiers.append("Shift")
        elif p == "ALT":
            modifiers.append("Alt")
        elif p in ("WIN", "WINDOWS", "META"):
            modifiers.append("Meta")
        else:
            key = p
    if not key:
        return ""
    key_lower = key.lower()
    if len(key) == 1:
        tk_key = f"Key-{key_lower}"
    else:
        tk_key = key_lower
    prefix = "-".join(modifiers)
    return f"<{prefix}-{tk_key}>" if prefix else f"<{tk_key}>"


# ---------------------------------------------------------------------------
# コード I/O
# ---------------------------------------------------------------------------
def load_codes() -> list:
    """codes.json を読み込む。存在しなければ空リスト。"""
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
    except (json.JSONDecodeError, OSError) as exc:
        messagebox.showerror("codes.json 読込エラー", f"{exc}\n空のリストで起動します。")
        return []


def save_codes(codes: list) -> None:
    """codes.json をアトミックに書き出す。"""
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
    # バックアップ
    if CODES_JSON.exists():
        try:
            BACKUP_JSON.write_text(CODES_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    os.replace(tmp, CODES_JSON)


# ---------------------------------------------------------------------------
# Win32 クリップボード / グローバルホットキー
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL

    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = ctypes.c_uint32

    def get_clipboard_text() -> str:
        try:
            if not user32.OpenClipboard(None):
                return ""
            try:
                if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return ""
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        except Exception:
            return ""
        return ""

    def set_clipboard_text(text: str, hwnd: int = 0) -> bool:
        if not text:
            return False
        # OpenClipboard に HWND を渡すと他プロセスからの読み取りが安定する
        open_hwnd = hwnd if hwnd else user32.GetDesktopWindow()
        try:
            if not user32.OpenClipboard(open_hwnd):
                return False
            try:
                user32.EmptyClipboard()
                data = text.encode("utf-16-le") + b"\x00\x00"
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    return False
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    kernel32.GlobalFree(h)
                    return False
                try:
                    ctypes.memmove(ptr, data, len(data))
                finally:
                    kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(CF_UNICODETEXT, h):
                    kernel32.GlobalFree(h)
                    return False
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            return False

    def register_global_hotkeys(
        next_mod: int, next_vk: int,
        copy_paste_mod: int = 0, copy_paste_vk: int = 0,
        used_mod: int = 0, used_vk: int = 0, target_hwnd: int = 0
    ) -> tuple:
        """「次のコードをコピー」と「コピー+自動ペースト」の2つのグローバルホットキーを登録 (pynput 使用)。
        pynput の GlobalHotKeys は内部でキーボードフックを独自実装するため、
        一部ゲームで keyboard ライブラリの WH_KEYBOARD_LL がブロックされる
        ケースでも動作することが多い。
        """
        if not _KEYBOARD_AVAILABLE:
            return (False, False)
        global _keyboard_handles
        # 既存があれば停止・解除
        for h in _keyboard_handles:
            try:
                h.stop()
            except Exception:
                pass
        _keyboard_handles = []
        # 登録するホットキーを dict に集める
        hotkey_map = {}
        next_name_pynput = _to_pynput_hotkey(modvk_to_hotkey_name(next_mod, next_vk))
        hotkey_map[next_name_pynput] = _kb_next_callback
        # 2 つ目 (copy_paste) が指定されていれば追加
        if copy_paste_mod and copy_paste_vk:
            cp_name_pynput = _to_pynput_hotkey(modvk_to_hotkey_name(copy_paste_mod, copy_paste_vk))
            # 同じホットキー名は上書きしない (next と copy_paste が同じ場合に重複しないように)
            if cp_name_pynput != next_name_pynput:
                hotkey_map[cp_name_pynput] = _kb_copy_paste_callback
        if not hotkey_map:
            return (False, False)
        try:
            print(f"[pynput] 登録試行: {list(hotkey_map.keys())}", file=__import__("sys").stderr, flush=True)
            listener = pynput_keyboard.GlobalHotKeys(hotkey_map)
            listener.start()
            _keyboard_handles.append(listener)
            print(f"[pynput] 登録成功: {list(hotkey_map.keys())}", file=__import__("sys").stderr, flush=True)
            return (True, True)
        except Exception as exc:
            print(f"[pynput] 登録失敗: {hotkey_map.keys()} / {exc}", file=__import__("sys").stderr, flush=True)
            return (False, False)

    def unregister_global_hotkeys(target_hwnd: int = 0) -> None:
        global _keyboard_handles
        for h in _keyboard_handles:
            try:
                h.stop()
            except Exception:
                pass
        _keyboard_handles = []

    # メッセージオンリーウィンドウを自前で作成 (tkinter の toplevel は RegisterHotKey に
    # 反応しないことがあるため、独立した message-only ウィンドウを作る)
    HWND_MESSAGE = -3  # Windows 定数

    user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL

    def create_message_only_window() -> int:
        """メッセージオンリーウィンドウを作成。WM_HOTKEY を受け取れる独立HWND。"""
        # クラス名に "STATIC" を使うと CreateWindowExW でメッセージオンリーが作れる
        hwnd = user32.CreateWindowExW(
            0,                  # dwExStyle
            "STATIC",            # lpClassName (built-in)
            "WWMHotkeyMsg",      # lpWindowName
            0,                  # dwStyle
            0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE),  # hWndParent = HWND_MESSAGE
            wintypes.HMENU(),    # hMenu
            wintypes.HINSTANCE(),  # hInstance
            None,                # lpParam
        )
        return hwnd or 0

    def destroy_message_window(hwnd: int) -> None:
        if hwnd:
            user32.DestroyWindow(wintypes.HWND(hwnd))

    # メッセージループ用 API も宣言
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM
    user32.PostThreadMessageW.argtypes = [ctypes.c_uint, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    WM_QUIT = 0x0012

    def start_hotkey_message_loop(hwnd: int, on_hotkey_next, on_hotkey_used) -> int:
        """別スレッドで GetMessage ループを開始。WM_HOTKEY でコールバック呼び出し。
        戻り値: スレッドID (終了時に PostThreadMessage(WM_QUIT) 用)"""
        import threading
        thread_id_box = [0]
        def loop():
            MSG = wintypes.MSG()
            # 自分のスレッドID取得
            thread_id = kernel32.GetCurrentThreadId()
            thread_id_box[0] = thread_id
            while True:
                ret = user32.GetMessageW(ctypes.byref(MSG), None, 0, 0)
                if ret == 0 or ret == -1:
                    break  # WM_QUIT または エラー
                if MSG.message == 0x0312:  # WM_HOTKEY
                    wparam = MSG.wParam
                    if wparam == HOTKEY_ID_NEXT:
                        on_hotkey_next()
                    elif wparam == HOTKEY_ID_USED:
                        on_hotkey_used()
                else:
                    user32.TranslateMessage(ctypes.byref(MSG))
                    user32.DispatchMessageW(ctypes.byref(MSG))
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        # スレッドIDが設定されるまで短時間待つ
        import time as _time
        for _ in range(50):
            if thread_id_box[0]:
                break
            _time.sleep(0.01)
        return thread_id_box[0]

    def stop_hotkey_message_loop(thread_id: int) -> None:
        if thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
else:
    # 非 Windows 環境用スタブ
    def get_clipboard_text() -> str: return ""
    def set_clipboard_text(text: str) -> bool: return False
    def register_global_hotkeys(next_mod: int, next_vk: int, used_mod: int = 0, used_vk: int = 0, target_hwnd: int = 0) -> tuple: return (False, False)
    def unregister_global_hotkeys(target_hwnd: int = 0) -> None: pass
    def create_message_only_window() -> int: return 0
    def destroy_message_window(hwnd: int) -> None: pass
    def start_hotkey_message_loop(hwnd: int, on_hotkey_next, on_hotkey_used) -> int: return 0
    def stop_hotkey_message_loop(thread_id: int) -> None: pass


# ---------------------------------------------------------------------------
# メインアプリ
# ---------------------------------------------------------------------------
class CodeInputApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("WWM コード高速入力ツール")
        self.root.geometry("780x540")
        self.root.minsize(600, 360)

        # 設定ロード
        self.settings = load_settings()

        # codes.json パスの動的決定
        # settings.json に codes_json_path が指定されていればそちらを使う
        global CODES_JSON, BACKUP_JSON
        _custom_codes = self.settings.get("codes_json_path", "").strip()
        if _custom_codes:
            custom_path = Path(_custom_codes).expanduser().resolve()
            if custom_path.parent.exists():
                CODES_JSON = custom_path
                BACKUP_JSON = custom_path.with_suffix(".backup.json")
                self._log(f"📂 設定ファイル指定の codes.json を使用: {CODES_JSON}")
            else:
                self._log(f"⚠ codes_json_path のディレクトリが存在しない: {custom_path.parent} (デフォルトを使用)")
        # 環境変数 WWM_CODES_JSON があれば上書き
        _env_codes = os.environ.get("WWM_CODES_JSON", "").strip()
        if _env_codes:
            env_path = Path(_env_codes).expanduser().resolve()
            if env_path.parent.exists():
                CODES_JSON = env_path
                BACKUP_JSON = env_path.with_suffix(".backup.json")
                self._log(f"📂 環境変数 WWM_CODES_JSON で指定された codes.json を使用: {CODES_JSON}")

        # keyboard ライブラリ用コールバック設定 (app.py のグローバル変数経由)
        global _kb_next_callback, _kb_copy_paste_callback
        _kb_next_callback = self._hotkey_next_action
        _kb_copy_paste_callback = self._hotkey_copy_paste_action

        # 状態
        self.codes: list = []
        self.last_clipboard: str = ""
        self.last_copied_code: str = ""        # アプリが最後にコピーしたコード
        self._json_mtime: float = 0.0
        self._next_after_focus: bool = False  # フォーカスが戻ったら次を準備

        # ウィジェット構築
        self._build_ui()
        self._bind_keys()

        # データロード
        self.reload_codes(initial=True)

        # 初回クリップボード取得
        try:
            self.last_clipboard = get_clipboard_text() or ""
        except Exception:
            self.last_clipboard = ""

        # グローバルホットキー登録 (pynput ライブラリ使用)
        self._hotkey_hwnd = 0
        if not _KEYBOARD_AVAILABLE:
            self._log("⚠ 'pynput' ライブラリが見つかりません。")
            self._log("  pip install pynput を実行してください")
        else:
            try:
                next_spec = self.settings.get("hotkey_next", "Alt+G")
                next_parsed = parse_hotkey(next_spec)
                if not next_parsed:
                    self._log(f"⚠ ホットキー設定の解釈に失敗: next={next_spec}")
                    cp_mod, cp_vk = 0, 0
                else:
                    next_mod, next_vk = next_parsed
                    # 2 つ目: コピー+自動ペースト
                    cp_mod, cp_vk = 0, 0
                    if self.settings.get("auto_paste_enabled", False):
                        cp_spec = self.settings.get("hotkey_copy_paste", "Alt+Shift+G")
                        cp_parsed = parse_hotkey(cp_spec)
                        if cp_parsed:
                            cp_mod, cp_vk = cp_parsed
                    ok1, _ = register_global_hotkeys(next_mod, next_vk, cp_mod, cp_vk, 0, 0, 0)
                    if ok1:
                        next_name = modvk_to_hotkey_name(next_mod, next_vk)
                        self._log(f"✅ グローバル登録: {next_spec} ({next_name}) — 次のコードをコピー")
                        if cp_mod and cp_vk:
                            cp_name = modvk_to_hotkey_name(cp_mod, cp_vk)
                            self._log(f"✅ グローバル登録: {self.settings.get('hotkey_copy_paste','Alt+Shift+G')} ({cp_name}) — コピー+自動ペースト")
                        self._log("   ※ 初回は管理者権限の許可が必要な場合があります")
                    else:
                        self._log(f"⚠ グローバル登録失敗: 別アプリが既に使っている可能性")
            except Exception as exc:
                self._log(f"⚠ ホットキー登録エラー: {exc}")

        # ポーリング開始
        self._schedule_clipboard_poll()
        self._schedule_json_reload()
        self._schedule_next_after_focus_poll()

        # テストソケットサーバー (WWM_TEST_SOCKET=1 で有効)
        self._start_test_socket_server()

        # 終了時クリーンアップ
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------
    def _build_ui(self):
        # スタイル
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview", font=("Consolas", 10))
        style.configure("Treeview.Heading", font=("Yu Gothic", 10, "bold"))

        # 上部: ボタン (2段構成)
        toolbar_container = ttk.Frame(self.root, padding=6)
        toolbar_container.pack(fill=tk.X)

        # --- 1段目: 基本操作 ---
        row1 = ttk.Frame(toolbar_container)
        row1.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(row1, text="📋 コピー", command=self.copy_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="➡ 次をコピー", command=self.copy_next).pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(row1, text="✓ 一括使用済", command=self.bulk_mark_used).pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        # フィルタ (使用済コードの表示/非表示)
        self.hide_used_var = tk.BooleanVar(value=False)
        self.filter_btn = ttk.Checkbutton(
            row1, text="👁 使用済を非表示",
            variable=self.hide_used_var,
            command=self._refresh_tree
        )
        self.filter_btn.pack(side=tk.LEFT, padx=2)
        # 右端に次のコード表示 (ホットキー名)
        next_spec = self.settings.get("hotkey_next", "Alt+G")
        ttk.Label(row1, text=f"次: {next_spec}", foreground="gray").pack(side=tk.RIGHT, padx=4)

        # --- 2段目: 取得・追加・管理 ---
        row2 = ttk.Frame(toolbar_container)
        row2.pack(fill=tk.X)
        ttk.Button(row2, text="🌐 yar.gg を開く", command=self.open_yar_gg).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="＋ 一括追加", command=self.add_code_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(row2, text="↻ 再読込", command=lambda: self.reload_codes()).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="⚙ 設定", command=self.open_settings_dialog).pack(side=tk.LEFT, padx=2)

        # ステータス
        self.status_var = tk.StringVar(value="準備完了")
        status_lbl = ttk.Label(self.root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=(6, 2))
        status_lbl.pack(fill=tk.X, side=tk.BOTTOM)

        # Treeview
        table_frame = ttk.Frame(self.root, padding=6)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("code", "status", "added", "used_at", "source")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                 selectmode="extended")  # 複数選択対応 (Shift/Ctrl+クリック)
        for col, text, w, anchor in [
            ("code",    "コード",     200, tk.W),
            ("status",  "状態",        80, tk.CENTER),
            ("added",   "追加日",      90, tk.CENTER),
            ("used_at", "使用日",      90, tk.CENTER),
            ("source",  "ソース",     240, tk.W),
        ]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col == "source"))

        # タグ（色分け）
        self.tree.tag_configure("unused", foreground="#222")
        self.tree.tag_configure("used",   foreground="#999",
                                background="#f0f0f0")

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # コンテキストメニュー
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="📋 コピー", command=self.copy_selected)
        self.context_menu.add_command(label="✓ 使用済にする", command=lambda: self.mark_selected_used(False))
        self.context_menu.add_command(label="↺ 未使用に戻す", command=lambda: self.mark_selected_used(True))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="➡ これを次へコピー", command=self.copy_selected_as_next)
        self.context_menu.add_command(label="🗑 削除", command=self.delete_selected)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-2>", self._show_context_menu)  # mac / 一部

        # 行ダブルクリック → コピー
        self.tree.bind("<Double-1>", lambda e: self.copy_selected())

    def _bind_keys(self):
        # 設定から動的にバインド
        def _copy(e=None):
            self.copy_selected()
            return "break"
        def _mark_used(e=None):
            self.mark_selected_used(False)
            return "break"
        def _add(e=None):
            self.add_code_dialog()
            return "break"
        def _reload(e=None):
            self.reload_codes()
            return "break"
        def _open_settings(e=None):
            self.open_settings_dialog()
            return "break"

        # アプリ内ホットキー (固定部分)
        self.tree.bind("<Control-c>", _copy)
        self.tree.bind("<Control-C>", _copy)
        self.tree.bind("<Delete>", _mark_used)
        self.tree.bind("<Insert>", _add)
        self.tree.bind("<F5>", _reload)
        self.tree.bind("<Control-comma>", _open_settings)  # Ctrl+, で設定
        # 動的部分: 設定から読み取ったホットキーを後でバインド
        self._key_bindings = []  # (key_name, handler) リスト — 再バインド用

        # 設定のホットキーをバインド
        self._apply_hotkey_bindings()

    def _apply_hotkey_bindings(self):
        """settings から現在のホットキー設定を読み、tkinter にバインド。
        既存のバインドは解除する。
        動的: hotkey_next のみ
        固定: Ctrl+Shift+U = 一括使用済
        """
        # 既存を解除
        for key_name, handler in self._key_bindings:
            try:
                self.tree.unbind(key_name)
                self.root.unbind(key_name)
            except Exception:
                pass
        self._key_bindings = []

        # 動的部分: 設定のホットキー (「次をコピー」のみ)
        next_spec = self.settings.get("hotkey_next", "Alt+G")
        next_tk = hotkey_to_tkinter(next_spec)

        def _next(e=None):
            self._hotkey_next_action()
            return "break"

        if next_tk:
            try:
                self.tree.bind(next_tk, _next)
                self.root.bind(next_tk, _next)
                self._key_bindings.append((next_tk, _next))
            except Exception as exc:
                self._log(f"⚠ ホットキーバインド失敗: {next_spec} ({exc})")

        # 固定: Ctrl+Shift+U = 一括使用済
        try:
            self.tree.bind("<Control-Shift-Key-U>", lambda e: (self.bulk_mark_used(), "break")[1])
            self.tree.bind("<Control-Shift-u>", lambda e: (self.bulk_mark_used(), "break")[1])
            self.tree.bind("<Control-Shift-U>", lambda e: (self.bulk_mark_used(), "break")[1])
            self._key_bindings.append(("<Control-Shift-U>", None))
        except Exception as exc:
            self._log(f"⚠ Ctrl+Shift+U バインド失敗: {exc}")

    # ---------- コード管理 ----------
    def reload_codes(self, initial: bool = False):
        if not initial and CODES_JSON.exists():
            try:
                mtime = CODES_JSON.stat().st_mtime
                if mtime == self._json_mtime:
                    return
                self._json_mtime = mtime
            except OSError:
                pass
        self.codes = load_codes()
        if not initial:
            self._log("codes.json を再読込しました")
        self._refresh_tree()

    def _refresh_tree(self):
        sel = self._selected_code()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        hide_used = self.hide_used_var.get() if hasattr(self, "hide_used_var") else False
        displayed = 0
        for i, c in enumerate(self.codes):
            used = c.get("used", False)
            # フィルタ: 使用済を非表示
            if hide_used and used:
                continue
            tag = "used" if used else "unused"
            self.tree.insert("", tk.END, iid=str(i), values=(
                c.get("code", ""),
                "✓ 使用済" if used else "未入力",
                c.get("added_at", ""),
                c.get("used_at", "") or "—",
                c.get("source", "") or "",
            ), tags=(tag,))
            displayed += 1
        self._restore_selection(sel)
        self._update_status_count()
        # フィルタ状態のログ
        if hide_used:
            total_used = sum(1 for c in self.codes if c.get("used"))
            self._log(f"👁 フィルタ ON: 使用済 {total_used} 件を非表示 (表示中: {displayed} 件)")

    def _update_status_count(self):
        total = len(self.codes)
        used = sum(1 for c in self.codes if c.get("used"))
        rem = total - used
        next_spec = self.settings.get("hotkey_next", "Alt+G")
        if total == 0:
            self._log(f"コードが登録されていません。「＋ 一括追加」で追加してください。"
                      f" ホットキー: 次={next_spec}, 一括使用済=Ctrl+Shift+U")
        else:
            self._log(f"総数 {total} 件 / 使用済 {used} 件 / 未入力 {rem} 件 — "
                      f"グローバル: 次={next_spec} / 一括使用済=Ctrl+Shift+U (Ctrl+, で設定)")

    def _selected_index(self) -> int:
        sel = self.tree.selection()
        if not sel:
            return -1
        try:
            return int(sel[0])
        except (ValueError, IndexError):
            return -1

    def _selected_code(self) -> str:
        i = self._selected_index()
        if 0 <= i < len(self.codes):
            return self.codes[i].get("code", "")
        return ""

    def _restore_selection(self, code: str):
        if not code:
            return
        for i, c in enumerate(self.codes):
            if c.get("code") == code:
                self.tree.selection_set(str(i))
                self.tree.see(str(i))
                return

    # ---------- コピー / 使用済 ----------
    def copy_selected(self):
        i = self._selected_index()
        if i < 0:
            self._log("⚠ 行を選択してください")
            return
        code = self.codes[i].get("code", "")
        if not code:
            return
        if not set_clipboard_text(code):
            self._log("⚠ クリップボードへのコピーに失敗しました")
            return
        self.last_copied_code = code
        self._log(f"📋 コピー: {code}  (Ctrl+G で次へ / Alt+Tab 後にこのコードを使用済に)")

    def copy_selected_as_next(self):
        """コンテキストメニュー: 選択行をクリップボードにコピー"""
        self.copy_selected()

    def copy_next(self):
        """次に使うべき未使用コードを自動コピー。
        last_copied_code の次の位置から検索する (USED 後に次の未入力を正しく取得)。"""
        # last_copied_code のインデックスを特定
        start = 0
        if self.last_copied_code:
            for i, c in enumerate(self.codes):
                if c.get("code", "").upper() == self.last_copied_code.upper():
                    start = i + 1
                    break
        idx = self._next_unused_index(start_from=start)
        if idx < 0 and start > 0:
            # 末尾まで使ったので先頭から再検索
            idx = self._next_unused_index(start_from=0)
        if idx < 0:
            self._log("✅ 全てのコードが使用済です。")
            try:
                self.root.bell()
            except Exception:
                pass
            return
        code = self.codes[idx].get("code", "")
        # アプリの HWND を取得して clipboard ロックを安定化
        hwnd = 0
        try:
            hwnd = int(self.root.winfo_id())
        except Exception:
            pass
        if not set_clipboard_text(code, hwnd):
            self._log(f"⚠ クリップボードへのコピーに失敗しました (code={code})")
            return
        self.last_copied_code = code
        # 選択行も移動
        try:
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))
        except Exception:
            pass
        self._log(f"➡ 次のコードをコピー: {code}  (ゲーム内でペーストしてください)")

    def mark_selected_used(self, unmark: bool = False):
        i = self._selected_index()
        if i < 0:
            self._log("⚠ 行を選択してください")
            return
        c = self.codes[i]
        if unmark:
            if not c.get("used"):
                return
            c["used"] = False
            c["used_at"] = None
            self._log(f"↺ {c.get('code','')} を未使用に戻しました")
        else:
            if c.get("used"):
                return
            c["used"] = True
            c["used_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
            self._log(f"✓ {c.get('code','')} を使用済にマーク")
        save_codes(self.codes)
        self._refresh_tree()

    def bulk_mark_used(self):
        """選択中の行を全て「使用済」にマーク。
        何も選択していない場合は警告。
        確認ダイアログで件数を表示。
        """
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "一括使用済",
                "Treeview 内の行を1つ以上選択してください。\n"
                "(Ctrl+クリック / Shift+クリック で複数選択可)",
                parent=self.root,
            )
            return
        indices = sorted([int(i) for i in sel])
        # 確認
        preview = ", ".join(self.codes[i]["code"] for i in indices[:5])
        if len(indices) > 5:
            preview += f" ... 他 {len(indices) - 5} 件"
        ans = messagebox.askyesno(
            "一括使用済確認",
            f"{len(indices)} 件を使用済としてマークします。\n\n{preview}\n\nよろしいですか？",
            parent=self.root,
        )
        if not ans:
            return
        now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
        marked = 0
        for i in indices:
            c = self.codes[i]
            if not c.get("used"):
                c["used"] = True
                c["used_at"] = now
                marked += 1
        if marked > 0:
            save_codes(self.codes)
        self._refresh_tree()
        self._log(f"✓ 一括使用済: {marked} 件マーク ({len(indices) - marked} 件は元から使用済)")

    def mark_selected_used(self, unmark: bool = False):
        """単一選択行を使用済 (または未使用に) マーク。キーボード Delete/右クリックから呼ばれる。"""
        sel = self.tree.selection()
        if not sel:
            self._log("⚠ 行を選択してください")
            return
        # 複数選択時は bulk_mark_used を使うよう誘導
        if len(sel) > 1:
            self.bulk_mark_used()
            return
        i = int(sel[0])
        c = self.codes[i]
        if unmark:
            if not c.get("used"):
                return
            c["used"] = False
            c["used_at"] = None
            self._log(f"↺ {c.get('code','')} を未使用に戻しました")
        else:
            if c.get("used"):
                return
            c["used"] = True
            c["used_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
            self._log(f"✓ {c.get('code','')} を使用済にマーク")
        save_codes(self.codes)
        self._refresh_tree()

    def _next_unused_index(self, start_from: int = 0) -> int:
        for i in range(start_from, len(self.codes)):
            if not self.codes[i].get("used", False):
                return i
        return -1

    # ---------- yar.gg をブラウザで開く ----------
    def open_yar_gg(self):
        """codes.yar.gg を既定のブラウザで開く。

        注意: 過去はコードを自動取得していたが、サイト側が Vercel の
        bot 防御 (Vercel Security Checkpoint) を導入したため、非ブラウザ
        クライアント (urllib 等) では常に HTTP 429 で弾かれる。
        そのため自動取得を廃止し、ユーザーがブラウザでページを開き、
        コードをコピーして「＋ 一括追加」ダイアログに貼り付ける方式に変更。
        """
        url = "https://codes.yar.gg"
        try:
            ok = webbrowser.open(url, new=2, autoraise=True)
            if ok:
                self._log("🌐 yar.gg をブラウザで開きました。コードをコピーして「＋ 一括追加」に貼り付けてください。")
            else:
                raise OSError("ブラウザの起動に失敗しました")
        except Exception as exc:
            # ブラウザ起動失敗時は URL をクリップボードにコピー
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(url)
                self._log(f"⚠ ブラウザ起動失敗 ({exc})。URL をクリップボードにコピーしました: {url}")
            except Exception:
                self._log(f"⚠ yar.gg を開けません: {url}（手動でブラウザに貼り付けてください）")

    # ---------- 追加 / 削除 ----------
    def add_code_dialog(self):
        """コード一括追加ダイアログ。
        - 複数行テキストにコードを貼り付け (1行=1コード or スペース/カンマ区切り)
        - 「📋 クリップボードから」ボタンでクリップボードの内容を貼り付け
        - 「📂 ファイルから」ボタンでテキストファイル読込
        - 重複時の挙動 (スキップ/上書き) を選択可能
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("コード一括追加")
        dlg.geometry("620x540")
        dlg.transient(self.root)
        dlg.grab_set()

        # 上部: ヘッダ
        ttk.Label(
            dlg, text="コードを一括追加 (1行に1コード または スペース/カンマ区切り)",
            font=("", 10, "bold"),
        ).pack(anchor=tk.W, padx=12, pady=(12, 4))

        # yar.gg からの取得手順ヒント
        ttk.Label(
            dlg, text="※ yar.gg (codes.yar.gg) は bot 防御のため自動取得できません。\n"
                      "  ブラウザでページを開き、コードをコピーして「📋 クリップボードから」で貼り付けてください。",
            font=("", 8), foreground="#888", justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(0, 4))

        # テキストエリア
        text_frame = ttk.Frame(dlg)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        text_widget = tk.Text(
            text_frame, height=15, font=("Consolas", 10),
            yscrollcommand=scrollbar.set, wrap=tk.NONE,
        )
        scrollbar.config(command=text_widget.yview)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.focus_set()

        # ツールバー
        tools = ttk.Frame(dlg)
        tools.pack(fill=tk.X, padx=12, pady=4)

        def paste_from_clipboard():
            try:
                content = get_clipboard_text() or ""
                if content:
                    # 既存の内容をクリアして挿入
                    text_widget.delete("1.0", tk.END)
                    text_widget.insert("1.0", content)
                    self._log(f"📋 クリップボードから {len(content)} 文字を貼付")
                else:
                    messagebox.showinfo("空", "クリップボードが空です", parent=dlg)
            except Exception as e:
                messagebox.showerror("読込失敗", f"クリップボードから読込失敗: {e}", parent=dlg)

        def load_from_file():
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=dlg, title="コードファイルを選択",
                filetypes=[("テキストファイル", "*.txt"), ("すべてのファイル", "*.*")],
            )
            if not path:
                return
            try:
                content = Path(path).read_text(encoding="utf-8")
                text_widget.delete("1.0", tk.END)
                text_widget.insert("1.0", content)
                self._log(f"📂 ファイル読込: {path} ({len(content)} 文字)")
            except Exception as e:
                messagebox.showerror("読込失敗", f"ファイル読込失敗: {e}", parent=dlg)

        def clear_text():
            text_widget.delete("1.0", tk.END)

        ttk.Button(tools, text="📋 クリップボードから", command=paste_from_clipboard).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="📂 ファイルから", command=load_from_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="🗑 クリア", command=clear_text).pack(side=tk.LEFT, padx=2)

        # オプション
        opts = ttk.LabelFrame(dlg, text="オプション", padding=8)
        opts.pack(fill=tk.X, padx=12, pady=4)

        dup_var = tk.StringVar(value="skip")
        ttk.Label(opts, text="重複コードの扱い:").grid(row=0, column=0, sticky=tk.W, padx=4)
        ttk.Radiobutton(opts, text="スキップ", variable=dup_var, value="skip").grid(row=0, column=1, padx=4)
        ttk.Radiobutton(opts, text="上書き (used フラグをリセット)", variable=dup_var, value="overwrite").grid(row=0, column=2, padx=4)
        ttk.Radiobutton(opts, text="両方追加しない (常にスキップ)", variable=dup_var, value="skip").grid(row=0, column=3, padx=4)

        src_var = tk.StringVar(value="手動追加")
        ttk.Label(opts, text="ソース:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=(4, 0))
        ttk.Entry(opts, textvariable=src_var, width=30).grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=4, pady=(4, 0))

        # プレビュー
        preview_var = tk.StringVar(value="コード: 0 件 / 重複: 0 件 / 追加予定: 0 件")
        ttk.Label(dlg, textvariable=preview_var, foreground="#0066cc",
                  font=("", 9)).pack(anchor=tk.W, padx=12, pady=(4, 0))

        def parse_codes(text: str) -> tuple:
            """テキストから コード(英数文字列) のリストを抽出。
            - 1行=1コード
            - 同一行内にスペース/カンマ/タブ/セミコロン区切りで複数コードがあれば分割
            - コード形式: 6-15文字の英数大文字 (WWM コードの典型的なパターン)
            - 該当しない文字列 (日本語、句読点だけの行など) は除外

            戻り値: (codes: list[str], skipped: list[str]) コードリストとスキップされた文字列
            """
            result = []
            skipped = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                # # で始まる行はコメント
                if line.startswith("#") or line.startswith("//"):
                    continue
                # カンマ、スペース、タブ、セミコロンで分割
                for part in re.split(r"[\s,;]+", line):
                    part = part.strip()
                    if not part:
                        continue
                    # コード形式チェック: 6-15文字の英数 (大文字小文字両方 OK、保存時に大文字化)
                    if re.fullmatch(r"[A-Za-z0-9]{6,15}", part):
                        result.append(part)
                    else:
                        # コード形式でないものはスキップ
                        skipped.append(part)
            return result, skipped

        def update_preview(*_):
            text = text_widget.get("1.0", tk.END)
            candidates, skipped = parse_codes(text)
            existing = {c.get("code", "").upper() for c in self.codes}
            dup_count = sum(1 for c in candidates if c.upper() in existing)
            new_count = len(candidates) - dup_count
            msg = f"コード: {len(candidates)} 件 / 重複: {dup_count} 件 / 追加予定: {new_count} 件"
            if skipped:
                msg += f" / 除外: {len(skipped)} 件"
            preview_var.set(msg)

        text_widget.bind("<<Modified>>", lambda e: (text_widget.edit_modified(False), update_preview()))
        # 起動時にも 1 回
        dlg.after(100, update_preview)

        # ボタン
        def ok():
            text = text_widget.get("1.0", tk.END)
            candidates, skipped = parse_codes(text)
            if not candidates:
                if skipped:
                    messagebox.showwarning(
                        "入力エラー",
                        f"コードとして認識できる文字列がありませんでした。\n"
                        f"除外された文字列: {len(skipped)} 件\n"
                        f"形式: 6-15文字の英数 (例: ABCDEFG, ffff1234)\n\n"
                        f"除外例: {', '.join(skipped[:3])}",
                        parent=dlg,
                    )
                else:
                    messagebox.showwarning("入力エラー", "コードを入力してください", parent=dlg)
                return
            if skipped:
                self._log(f"⚠ コード形式でない文字列 {len(skipped)} 件を除外: {skipped[:5]}{'...' if len(skipped) > 5 else ''}")
            existing = {c["code"].upper(): i for i, c in enumerate(self.codes)}
            added, skipped_dup, overwritten = 0, 0, 0
            today = datetime.now(JST).strftime("%Y-%m-%d")
            src = src_var.get().strip() or "手動追加"
            mode = dup_var.get()

            for code in candidates:
                code_upper = code.upper()
                if code_upper in existing:
                    if mode == "overwrite":
                        idx = existing[code_upper]
                        self.codes[idx]["used"] = False
                        self.codes[idx]["used_at"] = None
                        self.codes[idx]["source"] = src
                        self.codes[idx]["added_at"] = today
                        overwritten += 1
                    else:
                        skipped_dup += 1
                else:
                    self.codes.append({
                        "code": code,
                        "used": False,
                        "added_at": today,
                        "used_at": None,
                        "source": src,
                    })
                    existing[code_upper] = len(self.codes) - 1
                    added += 1

            save_codes(self.codes)
            self._refresh_tree()
            msg = f"✅ 追加 {added} 件"
            if overwritten:
                msg += f" / 上書 {overwritten} 件"
            if skipped_dup:
                msg += f" / スキップ {skipped_dup} 件 (重複)"
            self._log(msg)
            dlg.destroy()

        btns = ttk.Frame(dlg)
        btns.pack(fill=tk.X, padx=12, pady=10, side=tk.BOTTOM)
        ttk.Button(btns, text="キャンセル", command=dlg.destroy).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btns, text="追加", command=ok).pack(side=tk.RIGHT, padx=2)
        dlg.bind("<Control-Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def open_settings_dialog(self):
        """設定ダイアログを開く。
        ホットキーは手入力 OR キャプチャボタン (押したキーを自動認識) で設定可能。
        「次のコードをコピー」のホットキーのみ設定可能。
        「一括使用済」は Ctrl+Shift+U の固定バインド (設定不要)。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("設定")
        dlg.geometry("620x620")
        dlg.transient(self.root)
        dlg.grab_set()

        # 状態管理 (キャプチャ用)
        self._capture_target = [None]  # [var_name or None]

        # ホットキー設定
        ttk.Label(dlg, text="グローバルホットキー (どのアプリにフォーカスがあっても効く)",
                  font=("", 10, "bold")).pack(anchor=tk.W, padx=12, pady=(12, 4))

        def make_hotkey_row(parent, label_text, initial_value, capture_command_name):
            """ホットキー入力行を生成 (ラベル + Entry + キャプチャボタン)
            戻り値: (textvariable, frame)"""
            ttk.Label(parent, text=label_text).pack(anchor=tk.W, padx=12)
            row = ttk.Frame(parent)
            row.pack(anchor=tk.W, padx=12, pady=(0, 6), fill=tk.X)

            var = tk.StringVar(value=initial_value)
            entry = ttk.Entry(row, textvariable=var, font=("Consolas", 11), width=20)
            entry.pack(side=tk.LEFT, padx=(0, 4))

            cap_btn = ttk.Button(
                row, text="🎹 キャプチャ",
                command=lambda v=var, e=entry, n=capture_command_name: self._start_capture(dlg, v, e, n)
            )
            cap_btn.pack(side=tk.LEFT)

            # キャプチャ中インジケータ
            indicator = ttk.Label(row, text="", foreground="#0066cc")
            indicator.pack(side=tk.LEFT, padx=8)

            # Entry と Indicator を var に紐づけ
            var._capture_indicator = indicator
            var._capture_entry = entry
            var._capture_btn = cap_btn
            return var, row

        next_var, _ = make_hotkey_row(
            dlg, "※「次のコードをコピー」:",
            self.settings.get("hotkey_next", "Alt+G"), "next"
        )

        # 自動ペースト設定
        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=(8, 4))
        ttk.Label(dlg, text="自動ペースト (Ctrl+V を擬似入力):",
                  font=("", 10, "bold")).pack(anchor=tk.W, padx=12)
        auto_paste_var = tk.BooleanVar(value=self.settings.get("auto_paste_enabled", False))
        ttk.Checkbutton(
            dlg, text="コピー後、自動で Ctrl+V をゲームに送信 (テキスト入力欄にフォーカスがある前提)",
            variable=auto_paste_var
        ).pack(anchor=tk.W, padx=12, pady=(2, 4))

        cp_var, _ = make_hotkey_row(
            dlg, "※「コピー+自動ペースト」ホットキー:",
            self.settings.get("hotkey_copy_paste", "Alt+Shift+G"), "copy_paste"
        )

        # 自動ペーストの遅延 (ms)
        delay_frame = ttk.Frame(dlg)
        delay_frame.pack(anchor=tk.W, padx=12, pady=(0, 8))
        ttk.Label(delay_frame, text="コピー→ペーストの遅延:").pack(side=tk.LEFT)
        delay_var = tk.IntVar(value=int(self.settings.get("auto_paste_delay_ms", 100)))
        ttk.Spinbox(delay_frame, from_=0, to=2000, increment=50, width=6,
                    textvariable=delay_var).pack(side=tk.LEFT, padx=4)
        ttk.Label(delay_frame, text="ms (推奨: 100-300)").pack(side=tk.LEFT, padx=4)

        # 固定ホットキー
        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=(4, 4))
        ttk.Label(dlg, text="固定ホットキー (変更不可):",
                  font=("", 9, "bold")).pack(anchor=tk.W, padx=12)
        fixed_frame = ttk.Frame(dlg)
        fixed_frame.pack(anchor=tk.W, padx=12, pady=(2, 4), fill=tk.X)
        ttk.Label(fixed_frame, text="  Ctrl+Shift+U", font=("Consolas", 10),
                  foreground="#666").pack(side=tk.LEFT)
        ttk.Label(fixed_frame, text="  = 選択中のコードを一括使用済マーク",
                  foreground="#666").pack(side=tk.LEFT)

        # codes.json 参照先
        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12, pady=(8, 4))
        ttk.Label(dlg, text="codes.json の参照先 (空ならデフォルト):",
                  font=("", 10, "bold")).pack(anchor=tk.W, padx=12)
        codes_path_frame = ttk.Frame(dlg)
        codes_path_frame.pack(anchor=tk.W, padx=12, pady=(0, 4), fill=tk.X)
        codes_path_var = tk.StringVar(value=self.settings.get("codes_json_path", ""))
        codes_path_entry = ttk.Entry(codes_path_frame, textvariable=codes_path_var, font=("Consolas", 10))
        codes_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        def browse_codes_path():
            from tkinter import filedialog
            initial = codes_path_var.get().strip() or str(APP_DIR / "codes.json")
            path = filedialog.askopenfilename(
                title="codes.json を選択",
                initialdir=str(Path(initial).parent),
                initialfile=Path(initial).name,
                filetypes=[("JSON ファイル", "*.json"), ("すべてのファイル", "*.*")],
                parent=dlg,
            )
            if path:
                codes_path_var.set(path)
        ttk.Button(codes_path_frame, text="📂 参照", command=browse_codes_path).pack(side=tk.LEFT)
        current_codes_label = ttk.Label(dlg, text=f"現在の参照先: {CODES_JSON}",
                                         foreground="#666", font=("", 9))
        current_codes_label.pack(anchor=tk.W, padx=12, pady=(0, 4))
        ttk.Label(dlg,
                  text="💡 空 = exe と同じフォルダの codes.json を使用 (デフォルト)\n"
                       "   環境変数 WWM_CODES_JSON でも上書き可能",
                  foreground="#666", font=("", 9), justify=tk.LEFT
                  ).pack(anchor=tk.W, padx=12, pady=(0, 8))

        # 書式ヘルプ
        help_text = (
            "書式: Ctrl+Alt+Shift+Win + キー\n"
            "使用可能キー: A-Z, F1-F12, 0-9, Space, Tab, Esc, Insert, Delete,\n"
            "             Home, End, PageUp, PageDown, Pause, Backspace, Enter\n"
            "⚠ Ctrl+G は Windows (Xbox Game Bar) が使用済。"
            "Alt+G を推奨。\n"
            "💡「🎹 キャプチャ」ボタンで実際のキー入力を自動認識します"
        )
        ttk.Label(dlg, text=help_text, foreground="#666",
                  font=("", 9), justify=tk.LEFT).pack(anchor=tk.W, padx=12, pady=(4, 8))

        # プレビュー
        preview_var = tk.StringVar()
        ttk.Label(dlg, textvariable=preview_var, foreground="#0066cc",
                  font=("", 9), justify=tk.LEFT).pack(anchor=tk.W, padx=12, pady=(0, 8))

        def update_preview(*_):
            n_spec = next_var.get()
            c_spec = cp_var.get()
            ap_enabled = auto_paste_var.get()
            msgs = []
            for name, spec in [("次", n_spec), ("コピー+ペースト", c_spec)]:
                if not spec:
                    msgs.append(f"  ⚠ {name}: 未設定")
                    continue
                parsed = parse_hotkey(spec)
                if not parsed:
                    msgs.append(f"  ❌ {name}: '{spec}' の解釈失敗")
                    continue
                mod, vk = parsed
                if not is_hotkey_available(mod, vk):
                    msgs.append(f"  ❌ {name}: '{spec}' はOSで使用中 (別のアプリが登録済)")
                else:
                    msgs.append(f"  ✅ {name}: '{spec}' 利用可能")
                # 自動ペーストが ON のとき、copy_paste がWindows標準と被っていないかチェック
                if ap_enabled and name == "コピー+ペースト":
                    reserved_desc = is_windows_reserved_hotkey(mod, vk)
                    if reserved_desc:
                        msgs.append(f"     ⚠ {reserved_desc}")
                        msgs.append(f"     → 自動ペーストと Windows 機能が重複発火する可能性があります")
                        msgs.append(f"     → 別の修飾子 (例: Alt+Shift+G, F8) を推奨")
            if ap_enabled:
                msgs.append(f"  📋 自動ペースト: ON (遅延 {delay_var.get()}ms)")
            else:
                msgs.append(f"  📋 自動ペースト: OFF")
            preview_var.set("\n".join(msgs))

        next_var.trace_add("write", update_preview)
        cp_var.trace_add("write", update_preview)
        auto_paste_var.trace_add("write", update_preview)
        delay_var.trace_add("write", update_preview)
        update_preview()

        def ok():
            n_spec = next_var.get().strip()
            if not n_spec:
                messagebox.showerror("設定エラー", "「次のコードをコピー」のホットキーが空です", parent=dlg)
                return
            parsed = parse_hotkey(n_spec)
            if not parsed:
                messagebox.showerror("設定エラー",
                                     f"ホットキー '{n_spec}' を解釈できません\n"
                                     f"例: Alt+G, Ctrl+Shift+F1", parent=dlg)
                return
            self.settings["hotkey_next"] = n_spec
            # codes.json 参照先
            new_codes_path = codes_path_var.get().strip()
            old_codes_path = self.settings.get("codes_json_path", "")
            self.settings["codes_json_path"] = new_codes_path
            # 自動ペースト設定
            self.settings["auto_paste_enabled"] = bool(auto_paste_var.get())
            self.settings["auto_paste_delay_ms"] = max(0, int(delay_var.get()))
            if self.settings["auto_paste_enabled"]:
                cp_spec = cp_var.get().strip()
                if not cp_spec:
                    messagebox.showerror("設定エラー",
                                         "「コピー+自動ペースト」を有効にする場合はホットキーを指定してください",
                                         parent=dlg)
                    return
                cp_parsed = parse_hotkey(cp_spec)
                if not cp_parsed:
                    messagebox.showerror("設定エラー",
                                         f"ホットキー '{cp_spec}' を解釈できません",
                                         parent=dlg)
                    return
                # next と同じ場合は無効化
                if cp_parsed == parsed:
                    messagebox.showwarning("設定",
                                           "「次のコードをコピー」と同じホットキーは指定できません。\n"
                                           "「コピー+自動ペースト」を無効化します。", parent=dlg)
                    self.settings["auto_paste_enabled"] = False
                    self.settings["hotkey_copy_paste"] = ""
                else:
                    # Windows 標準ショートカットとの重複を警告
                    reserved_desc = is_windows_reserved_hotkey(cp_parsed[0], cp_parsed[1])
                    if reserved_desc:
                        answer = messagebox.askyesno(
                            "Windows 標準ショートカットと重複",
                            f"「コピー+自動ペースト」ホットキー '{cp_spec}' は:\n\n"
                            f"  {reserved_desc}\n\n"
                            f"自動ペースト機能と Windows 機能が重複発火する可能性があります。\n"
                            f"別の修飾子 (例: Alt+Shift+G, F8) の使用を推奨します。\n\n"
                            f"このまま保存しますか?",
                            parent=dlg,
                        )
                        if not answer:
                            return  # キャンセル
                    self.settings["hotkey_copy_paste"] = cp_spec
            else:
                self.settings["hotkey_copy_paste"] = cp_var.get().strip() or "Alt+Shift+G"
            # 旧キーは確実に削除
            self.settings.pop("hotkey_used", None)
            save_settings(self.settings)
            # 既存グローバルホットキー解除
            if sys.platform == "win32":
                try:
                    if self._hotkey_thread_id:
                        stop_hotkey_message_loop(self._hotkey_thread_id)
                    unregister_global_hotkeys(self._hotkey_hwnd)
                except Exception:
                    pass
                self._re_register_global_hotkeys()
            # tkinter のキーバインド再構築
            self._apply_hotkey_bindings()
            self._log(f"✅ 設定を保存しました: 次={n_spec} / 自動ペースト={self.settings.get('auto_paste_enabled')}")
            # codes.json 参照先が変わった場合、再起動が必要
            if new_codes_path != old_codes_path:
                if new_codes_path:
                    self._log(f"📂 codes.json 参照先変更: {new_codes_path}")
                else:
                    self._log(f"📂 codes.json 参照先をデフォルトに戻しました")
                self._log("⚠ 反映には再起動が必要です")
            dlg.destroy()

        # ボタン
        btns = ttk.Frame(dlg)
        btns.pack(fill=tk.X, padx=12, pady=10, side=tk.BOTTOM)
        ttk.Button(btns, text="キャンセル", command=dlg.destroy).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btns, text="デフォルトに戻す",
                   command=lambda: (next_var.set("Alt+G"), cp_var.set("Alt+Shift+G"),
                                    auto_paste_var.set(False), delay_var.set(100))).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btns, text="保存", command=ok).pack(side=tk.RIGHT, padx=2)
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _start_capture(self, parent_dlg, target_var, entry_widget, capture_name):
        """ホットキーキャプチャモード開始。押されたキーを検出して target_var にセットする。"""
        indicator = getattr(target_var, "_capture_indicator", None)
        btn = getattr(target_var, "_capture_btn", None)
        if indicator is None or btn is None:
            return

        # キャプチャ中の状態
        self._capture_active = True
        self._capture_target_var = target_var
        self._capture_target_entry = entry_widget
        self._capture_modifiers = set()
        self._capture_key = None

        # インジケータ更新
        indicator.config(text="🎹 キーを押してください... (Esc でキャンセル)", foreground="#0066cc")
        btn.config(state="disabled", text="🎹 待機中...")

        # キャプチャ用のキーバインド
        # tkinter キーは大文字/小文字を区別しない仕様だが、修飾子キーは <KeyPress-Control> 等の形で来る
        def on_key_press(event):
            if not getattr(self, "_capture_active", False):
                return
            # 修飾子キーの処理
            mod_map = {
                "Control_L": "Control", "Control_R": "Control",
                "Alt_L": "Alt", "Alt_R": "Alt",
                "Shift_L": "Shift", "Shift_R": "Shift",
                "Win_L": "Windows", "Win_R": "Windows",
            }
            keysym = event.keysym
            if keysym in mod_map:
                self._capture_modifiers.add(mod_map[keysym])
                return  # 修飾子だけなら続行
            # Esc キャンセル
            if keysym == "Escape":
                self._stop_capture(indicator, btn, cancelled=True)
                return
            # Backspace で取り消し
            if keysym == "BackSpace":
                self._capture_modifiers.clear()
                self._capture_key = None
                self._capture_target_var.set("")
                return
            # 通常のキー: キャプチャ確定
            self._capture_key = keysym
            spec = self._build_capture_spec()
            self._capture_target_var.set(spec)
            # 自動で次のキャプチャは行わず、ここで完了
            self._stop_capture(indicator, btn, cancelled=False)

        def on_key_release(event):
            # 修飾子キーが離された
            mod_map = {
                "Control_L": "Control", "Control_R": "Control",
                "Alt_L": "Alt", "Alt_R": "Alt",
                "Shift_L": "Shift", "Shift_R": "Shift",
                "Win_L": "Windows", "Win_R": "Windows",
            }
            keysym = event.keysym
            if keysym in mod_map:
                # 押下中の修飾子から削除 (ただし他の物理キーが押されていると仮定)
                # tkinter では修飾子状態は keysym からは取得できないので、
                # 押した修飾子は押しているものとする
                pass

        # キャプチャ用のバインド (キャプチャダイアログ自体にバインド)
        # 既存の entry バインドはそのまま、entry と dialog 両方にバインド
        for w in [entry_widget, parent_dlg]:
            w.bind("<KeyPress>", on_key_press, add="+")
            w.bind("<KeyRelease>", on_key_release, add="+")

        # キャプチャ対象 Entry にフォーカス移す
        entry_widget.focus_force()

        # 他のキーバインドを一時的に無効化はしない (Entry にフォーカスがある限り OK)
        self._capture_indicator = indicator
        self._capture_btn = btn
        self._capture_entry = entry_widget
        self._capture_dlg = parent_dlg

    def _build_capture_spec(self):
        """キャプチャ中の修飾子とキーから "Ctrl+Alt+G" 形式を構築"""
        parts = []
        if "Control" in self._capture_modifiers:
            parts.append("Ctrl")
        if "Alt" in self._capture_modifiers:
            parts.append("Alt")
        if "Shift" in self._capture_modifiers:
            parts.append("Shift")
        if "Windows" in self._capture_modifiers:
            parts.append("Win")
        if self._capture_key:
            key = self._capture_key
            # 表示名調整
            display = {
                "space": "Space", "Return": "Enter", "Escape": "Esc",
                "Prior": "PageUp", "Next": "PageDown",
                "Print": "PrintScreen", "Scroll_Lock": "ScrollLock",
                "Caps_Lock": "CapsLock", "Num_Lock": "NumLock",
                "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
            }.get(key, key)
            parts.append(display)
        return "+".join(parts)

    def _stop_capture(self, indicator, btn, cancelled=False):
        """キャプチャモードを終了する"""
        self._capture_active = False
        if cancelled and hasattr(self, "_capture_entry") and self._capture_entry:
            self._capture_target_var.set(self.settings.get(
                "hotkey_next" if "next" in str(indicator) else "hotkey_used", "Alt+G"
            ))
        if indicator is not None:
            indicator.config(text="", foreground="#0066cc")
        if btn is not None:
            btn.config(state="normal", text="🎹 キャプチャ")
        if hasattr(self, "_capture_dlg") and self._capture_dlg:
            try:
                self._capture_dlg.unbind("<KeyPress>")
                self._capture_dlg.unbind("<KeyRelease>")
            except Exception:
                pass

    def _re_register_global_hotkeys(self):
        """現在の self.settings に基づいてグローバルホットキー (2 つ) を再登録"""
        if not _KEYBOARD_AVAILABLE:
            return
        try:
            next_spec = self.settings.get("hotkey_next", "Alt+G")
            next_parsed = parse_hotkey(next_spec)
            if not next_parsed:
                return
            next_mod, next_vk = next_parsed
            # 2 つ目: コピー+自動ペースト
            cp_mod, cp_vk = 0, 0
            if self.settings.get("auto_paste_enabled", False):
                cp_spec = self.settings.get("hotkey_copy_paste", "Alt+Shift+G")
                cp_parsed = parse_hotkey(cp_spec)
                if cp_parsed:
                    cp_mod, cp_vk = cp_parsed
            # used は使わなくなったが register_global_hotkeys のシグネチャ互換のためダミーを渡す
            ok1, ok2 = register_global_hotkeys(next_mod, next_vk, cp_mod, cp_vk, 0, 0, 0)
            if ok1:
                self._log(f"✅ グローバル再登録: {next_spec} (次のコードをコピー)")
                if cp_mod and cp_vk:
                    self._log(f"✅ グローバル再登録: {cp_spec} (コピー + 自動ペースト)")
        except Exception as exc:
            self._log(f"⚠ 再登録エラー: {exc}")

    def delete_selected(self):
        i = self._selected_index()
        if i < 0:
            return
        c = self.codes[i]
        if not messagebox.askyesno("削除確認", f"コード {c.get('code','')} を削除しますか？"):
            return
        del self.codes[i]
        save_codes(self.codes)
        self._refresh_tree()
        self._log(f"🗑 削除: {c.get('code','')}")

    def _show_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    # ---------- ポーリング ----------
    def _schedule_clipboard_poll(self):
        self.root.after(CLIPBOARD_POLL_MS, self._clipboard_poll)

    def _clipboard_poll(self):
        try:
            cur = get_clipboard_text() or ""
            if cur and cur != self.last_clipboard:
                # アプリがコピーしたコードは無視（ペーストではない）
                if cur != self.last_copied_code:
                    # アプリ外にコピーされた → ユーザーがゲームにペーストする準備？
                    self.last_clipboard = cur
                else:
                    self.last_clipboard = cur
        except Exception:
            pass
        self._schedule_clipboard_poll()

    def _schedule_json_reload(self):
        self.root.after(JSON_RELOAD_MS, self._json_reload_tick)

    def _json_reload_tick(self):
        self.reload_codes()
        self._schedule_json_reload()

    def _hotkey_next_action(self):
        """グローバルホットキー受信時 (keyboard ライブラリの別スレッドから呼ばれる)。
        必ず GUI スレッドに委譲する。"""
        try:
            self.root.after(0, self._on_hotkey_next_main)
        except Exception:
            # tkinter が破棄済み
            pass
    def _on_hotkey_next_main(self):
        """GUI スレッドで実行される『次のコードをコピー』ハンドラ。"""
        self._log(f"🔥 ホットキー受信 → 次のコードをコピー")
        self.copy_next()

    def _hotkey_copy_paste_action(self):
        """グローバルホットキー受信時 (コピー + 自動ペースト)。
        必ず GUI スレッドに委譲する。"""
        try:
            self.root.after(0, self._on_hotkey_copy_paste_main)
        except Exception:
            pass

    def _on_hotkey_copy_paste_main(self):
        """GUI スレッドで実行される『次のコードをコピー + 自動ペースト』ハンドラ。
        copy_next() でコードをクリップボードにコピー後、SendInput で Ctrl+V を
        擬似キー入力する。ゲーム側でテキスト入力欄にフォーカスがある前提。
        """
        self._log(f"🔥 ホットキー受信 → コピー + 自動ペースト")
        # まず次のコードをコピー
        self.copy_next()
        if not self.settings.get("auto_paste_enabled", False):
            return
        # 自動ペーストが無効ならコピーだけ
        delay_ms = int(self.settings.get("auto_paste_delay_ms", 100))
        # 遅延後に SendInput で Ctrl+V 実行
        try:
            self.root.after(delay_ms, self._send_paste_keys)
        except Exception:
            pass

    def _send_paste_keys(self):
        """pynput の keyboard.Controller で Ctrl+V を擬似キー入力する (1回だけ)。
        SendInput は WH_KEYBOARD_LL に到達しないが、ゲーム側のテキスト入力欄
        には届く。一度の押下/解放で完了し、リピートしないよう明示的に実装。
        """
        # 二重発火ガード: 既にペースト処理中なら無視
        if getattr(self, "_paste_in_flight", False):
            return
        self._paste_in_flight = True
        try:
            from pynput.keyboard import Controller as _Kbd, Key, Controller
            ctl = _Kbd()
            # press_and_release は press → release を内部で完結させる
            # 同時に押し・離しではなく、わずかに間を空ける
            ctl.press(Key.ctrl)
            time.sleep(0.02)
            ctl.tap("v")  # press + release を 1 ステップで実行
            time.sleep(0.02)
            ctl.release(Key.ctrl)
            self._log(f"⌨ 自動ペースト (Ctrl+V) 送信完了 (1回のみ)")
        except Exception as exc:
            self._log(f"⚠ 自動ペースト失敗: {exc}")
        finally:
            # 短時間 (300ms) ロックして連続発火を防ぐ
            # 連続押下による多重 Ctrl+V を抑制
            self.root.after(300, lambda: setattr(self, "_paste_in_flight", False))

    def _schedule_next_after_focus_poll(self):
        """Alt+Tab で戻ってきたら『次のコードをコピーする準備ができた』インジケータを表示。
        自動ではコピーしない（誤検知防止）。"""
        try:
            focused = self.root.focus_displayof() is not None
            if focused and not self._next_after_focus:
                # 戻ってきた
                self._next_after_focus = True
                if self.last_copied_code and self._is_known(self.last_copied_code):
                    self._log(f"⏎ アプリにフォーカスが戻りました。Ctrl+G で次へコピーできます (前回: {self.last_copied_code})")
            elif not focused and self._next_after_focus:
                self._next_after_focus = False
        except Exception:
            pass
        self.root.after(400, self._schedule_next_after_focus_poll)

    def _is_known(self, code: str) -> bool:
        return any(c.get("code", "").upper() == code.upper() for c in self.codes)

    # ---------- ログ・終了 ----------
    def _log(self, msg: str):
        ts = datetime.now(JST).strftime("%H:%M:%S")
        try:
            self.status_var.set(f"[{ts}] {msg}")
        except Exception:
            pass
        # テスト用: ファイルにも書く
        if hasattr(self, "_log_file_path") and self._log_file_path:
            try:
                with open(self._log_file_path, "a", encoding="utf-8") as f:
                    f.write(f"[{ts}] {msg}\n")
            except Exception as e:
                # ファイル書き込み失敗は stderr に
                import sys
                print(f"[_log file error] {e}", file=sys.stderr)

    def _start_test_socket_server(self):
        """テスト用ソケットサーバー (localhost:51234)
        外部テストプログラムが 'NEXT', 'USED', 'STATUS', 'EXIT' を送って制御できる。
        グローバルホットキーが物理的に届かない開発環境でも動作確認できる。

        ⚠ 環境変数 WWM_TEST_SOCKET=1 または引数 --test-socket が指定されている時のみ起動。
           通常利用では OFF。
        """
        import socket
        import threading
        self._test_socket = None
        self._test_thread = None
        _on = (os.environ.get("WWM_TEST_SOCKET", "0") == "1") or any(
            arg == "--test-socket" for arg in sys.argv[1:]
        )
        if not _on:
            self._log("🔧 テストソケット: 無効 (WWM_TEST_SOCKET=1 または --test-socket で有効化)")
            return

        # ファイルロガーを必ずセットアップ
        log_file_path = None
        for arg in sys.argv[1:]:
            if arg.startswith("--log-file="):
                log_file_path = arg.split("=", 1)[1]
                break
        if not log_file_path:
            log_file_path = os.environ.get("WWM_LOG_FILE", "")
        if not log_file_path:
            # デフォルト: APP_DIR/_exe_log.txt
            log_file_path = str(APP_DIR / "_exe_log.txt")
        self._log_file_path = log_file_path
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now(JST).isoformat()}] テストソケット起動\n")
                f.write(f"  APP_DIR={APP_DIR}\n")
                f.write(f"  CODES_JSON={CODES_JSON}\n")
                f.write(f"  exists={CODES_JSON.exists()}\n")
                f.write(f"  argv={sys.argv}\n")
        except Exception as e:
            print(f"log file error: {e}", file=sys.stderr)

        def server():
            port = int(os.environ.get("WWM_TEST_PORT", "51234"))
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("127.0.0.1", port))
                srv.listen(1)
                srv.settimeout(0.5)
                self._test_socket = srv
                self._log(f"🔧 テストソケット起動: localhost:{port}")
                while True:
                    try:
                        conn, addr = srv.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    conn.settimeout(5)
                    try:
                        # 複数行対応: 全データを受信するまでループ
                        buf = b""
                        conn.settimeout(0.5)
                        try:
                            while True:
                                chunk = conn.recv(4096)
                                if not chunk:
                                    break
                                buf += chunk
                        except socket.timeout:
                            pass
                        conn.settimeout(5)
                        data = buf.decode("utf-8", errors="replace").strip()
                        if not data:
                            conn.close()
                            continue
                        lines = data.split("\n")
                        response_lines = []
                        for line in lines:
                            cmd = line.strip().upper()
                            if cmd == "NEXT":
                                # 次のコードをコピー (GUIスレッドで)
                                self.root.after(0, self._on_hotkey_next_main)
                                response_lines.append("OK NEXT")
                            elif cmd == "COPY_PASTE":
                                # コピー + 自動ペースト (GUIスレッドで)
                                self.root.after(0, self._on_hotkey_copy_paste_main)
                                response_lines.append("OK COPY_PASTE")
                            elif cmd == "STATUS":
                                data = {
                                    "codes_total": len(self.codes),
                                    "codes_used": sum(1 for c in self.codes if c.get("used")),
                                    "last_copied": self.last_copied_code,
                                    "codes": [
                                        {"idx": i, "code": c.get("code"), "used": c.get("used", False)}
                                        for i, c in enumerate(self.codes)
                                    ],
                                    "settings": self.settings,
                                }
                                response_lines.append("STATUS " + json.dumps(data, ensure_ascii=False))
                            elif cmd.startswith("BULK_USED "):
                                # BULK_USED <code1>,<code2>,... → 一括使用済マーク
                                payload = cmd[len("BULK_USED "):].strip()
                                if not payload:
                                    response_lines.append("ERR BULK_USED_EMPTY")
                                else:
                                    import re as _re
                                    targets = [c.strip() for c in _re.split(r"[\s,;]+", payload) if c.strip()]
                                    existing = {c["code"].upper(): i for i, c in enumerate(self.codes)}
                                    marked, not_found = 0, 0
                                    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
                                    for code in targets:
                                        idx = existing.get(code.upper())
                                        if idx is None:
                                            not_found += 1
                                            continue
                                        c = self.codes[idx]
                                        if not c.get("used"):
                                            c["used"] = True
                                            c["used_at"] = now
                                            marked += 1
                                    if marked > 0:
                                        save_codes(self.codes)
                                    self._refresh_tree()
                                    self._log(f"✓ 一括使用済: {marked} 件マーク ({not_found} 件未発見)")
                                    response_lines.append(f"OK BULK_USED marked={marked} not_found={not_found}")
                            elif cmd.startswith("ADD "):
                                # ADD <code1>,<code2>,... → 一括追加
                                # カンマ・改行・スペース区切りに対応
                                payload = cmd[4:].strip()
                                if not payload:
                                    response_lines.append("ERR ADD_EMPTY")
                                else:
                                    import re as _re
                                    candidates = [c.strip() for c in _re.split(r"[\s,;]+", payload) if c.strip()]
                                    existing = {c["code"].upper(): i for i, c in enumerate(self.codes)}
                                    added, skipped, overwritten = 0, 0, 0
                                    today = datetime.now(JST).strftime("%Y-%m-%d")
                                    for code in candidates:
                                        cu = code.upper()
                                        if cu in existing:
                                            skipped += 1
                                        else:
                                            self.codes.append({
                                                "code": code,
                                                "used": False,
                                                "added_at": today,
                                                "used_at": None,
                                                "source": "test-socket",
                                            })
                                            existing[cu] = len(self.codes) - 1
                                            added += 1
                                    save_codes(self.codes)
                                    self._refresh_tree()
                                    response_lines.append(f"OK ADD added={added} skipped={skipped}")
                            elif cmd == "EXIT":
                                response_lines.append("OK EXIT")
                                self.root.after(0, self._on_close)
                            else:
                                response_lines.append(f"ERR UNKNOWN_CMD {cmd}")
                        conn.sendall(("\n".join(response_lines) + "\n").encode("utf-8"))
                    except Exception as e:
                        try:
                            conn.sendall(f"ERR {e}\n".encode("utf-8"))
                        except Exception:
                            pass
                    finally:
                        conn.close()
            except Exception as e:
                print(f"Test socket error: {e}", file=__import__('sys').stderr)
            finally:
                try:
                    if self._test_socket:
                        self._test_socket.close()
                except Exception:
                    pass

        self._test_thread = threading.Thread(target=server, daemon=True)
        self._test_thread.start()

    def _on_close(self):
        try:
            if _KEYBOARD_AVAILABLE:
                unregister_global_hotkeys()
            # テストソケットを閉じる
            sock = getattr(self, "_test_socket", None)
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        except Exception:
            pass
        self.root.destroy()


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
def _seed_initial_codes_json():
    """exe 起動時 (frozen) には 同梱の codes.json を exe と同じフォルダに展開する。
    ソース実行時は何もしない (codes.json が同階層に存在するため)。
    ⚠ テスト環境変数 WWM_TEST_SOCKET=1 または --test-socket 引数がある場合は
       何もしない (テスト側で事前配置した codes.json を尊重するため)。
    """
    if getattr(sys, "frozen", False):
        _test = (os.environ.get("WWM_TEST_SOCKET", "0") == "1") or any(
            arg == "--test-socket" for arg in sys.argv[1:]
        )
        if _test:
            return
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "codes.json"
            if bundled.exists() and not CODES_JSON.exists():
                CODES_JSON.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")


def main():
    _seed_initial_codes_json()
    # codes.json が無ければ空のテンプレートを作成
    if not CODES_JSON.exists():
        save_codes([])
    root = tk.Tk()
    app = CodeInputApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
