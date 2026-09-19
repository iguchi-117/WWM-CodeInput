# Changelog — WWM-CodeInput

## 2026-09-20 — 手動正規でリリース、自動投入は見送り (a093304)

- 手動導線 PASS: 再ビルドexeで Alt+G → QATEST001/002 をクリップボード独立読みで確認、
  codes.json は used=0 のまま。`copy_selected` の HWND統一＋tkinterフォールバックが有効。
- 自動投入は G4 で3連FAIL（通常・管理者昇格・`--click-xy 1290,741`＋SendInput統一）のため
  今回スコープ外。正規フロー＝手動（Alt+Gコピー→人間Ctrl+V）。
- 到達点: `focus:OK・field:OK・ctrl_v:OK`、`focused_shot`で入力欄の白枠ハイライト確認。
  最後のCtrl+Vだけがゲームに吸収される状態。UIPI単独説は棄却（昇格でも不変、対象は FULL(admin)）。
- 修正: keybd_event→SendInput統一（`_vk_seq`・送出件数で到達診断）、focus突き合わせログ、
  入力欄フォーカス（`--click-xy`/`--tabs`・未設定ならnoop）、貼付直前後の3枚証拠チェーン、
  マウスINPUT構造体48バイト問題の修正（SendInput err=87）。
- レガシー3テスト修正: restype宣言・現行仕様化（used自動マークなし）・冒頭残骸掃除。
- ゲート: `py_compile` 7 exit 0 / test_core 13/13 / test_auto_redeem 33/33 /
  test_socket 10/10 / dry-run保存なし / Jevゲート証跡あり。
- 次の1手（見送り）: 貼付手段の切替（Shift+Insert等）。lean・運用負荷最小化で今回対象外。
- G4証跡: `.hermes/evidence/g4_fail/`（focused/pasted/full 5枚）。
