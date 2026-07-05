"""
WWM コード高速入力ツール — 統合動作テスト
exeを起動して、ソケット経由でNEXT/BULK_USED/STATUS/ADD/EXITコマンドを送り、
「クリップボードに正しく格納されるか」「使用済マークがcodes.jsonに書かれるか」を検証する。

グローバルホットキー (Alt+G) の実機での動作は別途手動確認が必要だが、
本テストで「次のコードコピー → クリップボード格納 → 一括使用済マーク」のフロー全体が
exe 内部で正しく動作することを確認する。

使い方:
    python test_socket.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import socket
import subprocess
import tempfile
import time
import shutil
import os
import sys
from pathlib import Path

# Windows の cp932 デフォルトを UTF-8 に変更
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# argtypes/restype 設定 (重要! これをしないと ctypes のデフォルト型変換で失敗)
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wt.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wt.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL


def get_clipboard() -> str:
    """クリップボードを安全に読む (CF_UNICODETEXT と CF_TEXT 両方)"""
    hwnd = user32.GetDesktopWindow()
    for i in range(20):
        try:
            user32.OpenClipboard(hwnd)
            try:
                # まず CF_UNICODETEXT
                if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    h = user32.GetClipboardData(CF_UNICODETEXT)
                    if h:
                        ptr = kernel32.GlobalLock(h)
                        if ptr:
                            try:
                                val = ctypes.wstring_at(ptr)
                            finally:
                                kernel32.GlobalUnlock(h)
                            if val:
                                return val
                # CF_TEXT (ANSI) も試す
                CF_TEXT = 1
                if user32.IsClipboardFormatAvailable(CF_TEXT):
                    h = user32.GetClipboardData(CF_TEXT)
                    if h:
                        ptr = kernel32.GlobalLock(h)
                        if ptr:
                            try:
                                val = ctypes.string_at(ptr).decode("utf-8", errors="replace")
                            finally:
                                kernel32.GlobalUnlock(h)
                            if val:
                                return val
                return ""
            finally:
                user32.CloseClipboard()
        except Exception as e:
            time.sleep(0.1)
    return ""


def set_clipboard(text: str) -> bool:
    user32.OpenClipboard(user32.GetDesktopWindow())
    try:
        user32.EmptyClipboard()
        data = (text + "\0").encode("utf-16-le")
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


def send_command(port: int, cmd: str, timeout: float = 3.0) -> str:
    """exe にコマンドを送り、応答を返す。改行を含む複数行コマンドも対応。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        # 改行で終わるように正規化
        payload = cmd if cmd.endswith("\n") else cmd + "\n"
        s.sendall(payload.encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        chunks = []
        try:
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
        except socket.timeout:
            pass
        return b"".join(chunks).decode("utf-8", errors="replace").strip()
    finally:
        s.close()


def main():
    print("=" * 70)
    print("WWM コード高速入力ツール — 統合動作テスト")
    print("=" * 70)
    print()

    # テスト用一時環境
    tmpdir = Path(tempfile.mkdtemp(prefix="wwm_int_"))
    exe = Path("dist/WWMCodeInput.exe").resolve()
    # 毎回違うポートを使って、古いexeゾンビとの衝突を避ける
    import random
    port = 51240 + random.randint(0, 1000)

    # テスト用 codes.json を作成
    test_codes = [
        {"code": "TEST_AAA_001", "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test"},
        {"code": "TEST_AAA_002", "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test"},
        {"code": "TEST_AAA_003", "used": False, "added_at": "2026-07-02", "used_at": None, "source": "test"},
    ]
    (tmpdir / "codes.json").write_text(
        json.dumps({"version": 1, "total": 3, "codes": test_codes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # テスト用 settings.json
    (tmpdir / "settings.json").write_text(
        json.dumps({"version": 1, "hotkey_next": "Alt+G", "hotkey_used": "Alt+Shift+G"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"テスト環境: {tmpdir}")
    print(f"exe: {exe}")
    print(f"テストソケット: localhost:{port}")
    print()

    # exe 起動
    print("[1] exe 起動中...")
    # 環境変数もコマンドライン引数も両方渡す
    env = os.environ.copy()
    env["WWM_TEST_SOCKET"] = "1"
    env["WWM_TEST_PORT"] = str(port)
    env["WWM_APP_DIR"] = str(tmpdir)

    # ログファイルも exe に渡す
    env["WWM_LOG_FILE"] = str(tmpdir / "_exe_log.txt")

    # テスト起動モード: env変数 WWM_TEST_USE_PYTHON=1 で app.py を直接実行 (exe の elevation 問題を回避)
    if os.environ.get("WWM_TEST_USE_PYTHON", "") == "1":
        py_path = Path(r"<redacted>")
        app_py = Path(__file__).parent / "app.py"
        env["WWM_USE_PYTHON"] = "1"
        proc = subprocess.Popen(
            [str(py_path), str(app_py), "--test-socket", f"--app-dir={tmpdir}", f"--log-file={tmpdir / '_exe_log.txt'}"],
            env=env,
            stdout=open(tmpdir / "_exe_stdout.txt", "w", encoding="utf-8"),
            stderr=open(tmpdir / "_exe_stderr.txt", "w", encoding="utf-8"),
        )
        print(f"    (app.py 直接起動) PID: {proc.pid}")
    else:
        # PowerShell 経由でexeを起動 (hermes-agent とのトークン互換性問題回避)
        ps_cmd = (
            f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            f"$env:WWM_TEST_SOCKET='1'; "
            f"$env:WWM_TEST_PORT='{port}'; "
            f"$env:WWM_APP_DIR='{tmpdir}'; "
            f"$env:WWM_LOG_FILE='{tmpdir}/_exe_log.txt'; "
            f"$p = Start-Process -FilePath '{str(exe)}' "
            f"-ArgumentList @('--test-socket', '--app-dir={tmpdir}', '--log-file={tmpdir}/_exe_log.txt') "
            f"-PassThru -RedirectStandardOutput '{tmpdir}/_exe_stdout.txt' "
            f"-RedirectStandardError '{tmpdir}/_exe_stderr.txt'; "
            f"[Console]::WriteLine($p.Id)"
        )
        proc_ps = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_cmd],
            capture_output=True, timeout=10
        )
        pid_str = proc_ps.stdout.decode('utf-8', errors='ignore').strip()
        print(f"    PowerShell 起動 PID: {pid_str}")

        # プロセスハンドルを取得できないため ps 経由で kill する設計
        import psutil
        try:
            proc_obj = psutil.Process(int(pid_str))
            class FakeProc:
                def __init__(self, ps_obj, pid):
                    self._ps = ps_obj
                    self.pid = pid
                    self.returncode = None
                def kill(self):
                    self._ps.kill()
                def communicate(self, timeout=None):
                    self._ps.wait(timeout=timeout)
                    self.returncode = self._ps.returncode
                    return (b"", b"")
                def poll(self):
                    if not self._ps.is_running():
                        self.returncode = self._ps.returncode
                        return self.returncode
                    return None
            proc = FakeProc(proc_obj, int(pid_str))
        except Exception as e:
            print(f"    [NG] psutil 取得失敗: {e}")
            return 1
    print(f"    PID: {proc.pid}")

    # ソケット接続待ち (最大 10秒)
    print("\n[2] テストソケット接続待ち...")
    connected = False
    for i in range(20):
        time.sleep(0.5)
        if proc.poll() is not None:
            print(f"    [NG] exe が起動直後に終了: exit code={proc.returncode}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            return 1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            s.close()
            connected = True
            print(f"    [OK] {i*0.5:.1f}秒で接続成功")
            break
        except (ConnectionRefusedError, socket.timeout, OSError):
            continue
    if not connected:
        print("    [NG] ソケット接続失敗")
        proc.kill()
        time.sleep(1)
        # デバッグログ出力
        for f_name in ("_dbg.txt", "_exe_log.txt", "_dbg_err.txt", "_exe_stdout.txt", "_exe_stderr.txt"):
            f = tmpdir / f_name
            if f.exists():
                print(f"\n=== {f_name} ===")
                print(f.read_text(encoding="utf-8"))
        print(f"\nテスト環境: {tmpdir}")
        return 1

    print()
    print("=" * 70)
    print("シナリオ1: グローバルホットキー Alt+G の代わりに 'NEXT' 送信")
    print("=" * 70)
    results = {"passed": 0, "failed": 0}

    # シナリオ1: NEXT → クリップボード確認
    set_clipboard("INITIAL_BEFORE_NEXT")
    print(f"\n[3] クリップボード (NEXT前): {get_clipboard()!r}")
    time.sleep(0.3)  # クリップボード書込完了待ち
    resp = send_command(port, "NEXT")
    print(f"    応答: {resp!r}")
    time.sleep(1.0)  # GUI thread 処理 + クリップボード書込完了待ち
    cb = get_clipboard()
    print(f"    クリップボード (NEXT後): {cb!r}")
    if cb == "TEST_AAA_001":
        print("    ✅ PASS: クリップボードに次のコード 'TEST_AAA_001' が格納された")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待='TEST_AAA_001', 実際={cb!r}")
        results["failed"] += 1

    # シナリオ2: BULK_USED → 指定コードを一括使用済マーク
    print()
    print("=" * 70)
    print("シナリオ2: 'BULK_USED' 送信 (一括使用済マーク)")
    print("=" * 70)
    # 2件のコードを一括使用済にする
    target_codes = ["TEST_AAA_001", "TEST_AAA_002"]
    payload = ",".join(target_codes)
    resp = send_command(port, f"BULK_USED {payload}")
    print(f"    応答: {resp!r}")
    time.sleep(0.5)

    # codes.json 確認
    codes_data = json.loads((tmpdir / "codes.json").read_text(encoding="utf-8"))
    used_codes = [c["code"] for c in codes_data["codes"] if c.get("used")]
    print(f"    codes.json の使用済コード: {used_codes}")
    if all(c in used_codes for c in target_codes):
        print(f"    ✅ PASS: {target_codes} が全て使用済として永続化された")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: {target_codes} の一部が未使用")
        results["failed"] += 1

    # クリップボードは変わっていないこと
    cb_after_used = get_clipboard()
    # 期待: シナリオ1のクリップボードのまま
    # シナリオ1の最後に NEXT してからクリップボードが TEST_AAA_001 だった
    print(f"    クリップボード (BULK_USED後): {cb_after_used!r}")
    # BULK_USED は「次をコピー」しないのでクリップボードは変化しない
    if cb_after_used == "TEST_AAA_001":
        print(f"    ✅ PASS: クリップボードは変化なし (BULK_USED はコピーしない)")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待='TEST_AAA_001', 実際={cb_after_used!r}")
        results["failed"] += 1

    # シナリオ3: 連続 NEXT で全コードをコピー (BULK_USED で TEST_AAA_001/002 使用済済)
    print()
    print("=" * 70)
    print("シナリオ3: 連続 NEXT で未使用コードだけコピー")
    print("=" * 70)
    # 現在のクリップボードを記憶 (シナリオ1でNEXTしたTEST_AAA_001)
    last_cb = get_clipboard()
    # 残りの未使用 = test_codes から used_codes を除いたもの
    all_remaining = [c["code"] for c in test_codes if c["code"] not in used_codes]
    print(f"    [DEBUG] used_codes={used_codes}, all_remaining={all_remaining}")
    # 1回目 NEXT: 次の未使用
    send_command(port, "NEXT")
    time.sleep(1.0)
    cb1 = get_clipboard()
    print(f"    NEXT 1回目: {cb1!r} (期待: {all_remaining[0] if all_remaining else '(全件使用済)'})")
    if all_remaining and cb1 == all_remaining[0]:
        print("    ✅ PASS: 次のコードが正しくコピーされた")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待={all_remaining[0] if all_remaining else '(全件)'}, 実際={cb1!r}")
        results["failed"] += 1
    # 残りの未使用 (1回目NEXTしたTEST_AAA_003を除外)
    all_remaining_after = [c for c in all_remaining if c != cb1]
    print(f"    [DEBUG] all_remaining_after={all_remaining_after}")
    # 2回目 NEXT
    send_command(port, "NEXT")
    time.sleep(1.0)
    cb2 = get_clipboard()
    if all_remaining_after:
        print(f"    NEXT 2回目: {cb2!r} (期待: {all_remaining_after[0]})")
        if cb2 == all_remaining_after[0]:
            print("    ✅ PASS: さらに次のコードがコピーされた")
            results["passed"] += 1
        else:
            print(f"    ❌ FAIL: 期待={all_remaining_after[0]}, 実際={cb2!r}")
            results["failed"] += 1
    else:
        print(f"    NEXT 2回目 (全件使用済想定): {cb2!r}")
        if cb2 == cb1:
            print("    ✅ PASS: 全件使用済のため何もコピーされなかった")
            results["passed"] += 1
        else:
            print(f"    ❌ FAIL: 期待=変化なし, 実際={cb2!r}")
            results["failed"] += 1

    # シナリオ4: STATUS
    print()
    print("=" * 70)
    print("シナリオ4: STATUS コマンド")
    print("=" * 70)
    resp = send_command(port, "STATUS")
    print(f"    応答: {resp!r}")
    if "STATUS" in resp and "codes_total" in resp:
        print("    ✅ PASS: STATUS が JSON 形式で返された")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 不正な応答")
        results["failed"] += 1

    # シナリオ5: ADD (一括追加)
    print()
    print("=" * 70)
    print("シナリオ5: ADD コマンド (一括追加)")
    print("=" * 70)
    initial_count = len(json.loads((tmpdir / "codes.json").read_text(encoding="utf-8"))["codes"])
    new_codes = ["BULK_TEST_001", "BULK_TEST_002", "BULK_TEST_003", "BULK_TEST_004", "BULK_TEST_005"]
    payload = ",".join(new_codes)  # カンマ区切り (スペース/改行も可)
    resp = send_command(port, f"ADD {payload}")
    print(f"    ADD 応答: {resp!r}")
    if "OK ADD" in resp and "added=5" in resp:
        print("    ✅ PASS: 5件一括追加成功")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待='OK ADD added=5', 実際={resp!r}")
        results["failed"] += 1

    # codes.json の件数チェック
    data = json.loads((tmpdir / "codes.json").read_text(encoding="utf-8"))
    final_count = len(data["codes"])
    expected_count = initial_count + 5
    if final_count == expected_count:
        print(f"    ✅ PASS: codes.json 件数 {initial_count} → {final_count}")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待={expected_count}, 実際={final_count}")
        results["failed"] += 1

    # 重複追加テスト (同じコードを再度追加 → スキップ)
    print()
    print("=" * 70)
    print("シナリオ6: ADD 重複コード (スキップされる)")
    print("=" * 70)
    payload2 = "BULK_TEST_001, BULK_TEST_006, BULK_TEST_001"  # 1つ目と3つ目が重複
    resp2 = send_command(port, f"ADD {payload2}")
    print(f"    ADD 応答: {resp2!r}")
    if "OK ADD" in resp2 and "added=1" in resp2 and "skipped=2" in resp2:
        print("    ✅ PASS: 重複2件スキップ + 新規1件追加")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待='OK ADD added=1 skipped=2', 実際={resp2!r}")
        results["failed"] += 1

    # 最終確認
    data = json.loads((tmpdir / "codes.json").read_text(encoding="utf-8"))
    final_count2 = len(data["codes"])
    expected_count2 = expected_count + 1
    if final_count2 == expected_count2:
        print(f"    ✅ PASS: codes.json 件数 {expected_count} → {final_count2}")
        results["passed"] += 1
    else:
        print(f"    ❌ FAIL: 期待={expected_count2}, 実際={final_count2}")
        results["failed"] += 1

    # exe 終了
    print()
    print("=" * 70)
    print("exe 終了...")
    print("=" * 70)
    send_command(port, "EXIT")
    time.sleep(2)
    if proc.poll() is None:
        proc.kill()
        print("    [WARN] 強制終了")
    else:
        print(f"    [OK] 正常終了 (exit code={proc.returncode})")

    # デバッグログを保持
    log_file = tmpdir / "_exe_log.txt"
    if log_file.exists():
        print()
        print("=" * 70)
        print("exe ログファイル (_exe_log.txt):")
        print("=" * 70)
        print(log_file.read_text(encoding="utf-8"))
    stderr_file = tmpdir / "_exe_stderr.txt"
    if stderr_file.exists():
        content = stderr_file.read_text(encoding="utf-8", errors="ignore").strip()
        if content:
            print()
            print("=" * 70)
            print("exe 標準エラー出力 (_exe_stderr.txt):")
            print("=" * 70)
            print(content)
    dbg_file = tmpdir / "_dbg.txt"
    if dbg_file.exists():
        print()
        print(f"=== _dbg.txt ===")
        print(dbg_file.read_text(encoding="utf-8"))
    # 結果サマリ
    print()
    print("テスト環境 (デバッグ用):", tmpdir)
    print("  ※ shutil.rmtree スキップ — ログ確認用")
    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    print("=" * 70)
    print(f"結果: {results['passed']} passed, {results['failed']} failed")
    print("=" * 70)
    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
