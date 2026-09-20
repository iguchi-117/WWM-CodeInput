# Changelog — WWM-CodeInput

## 2026-09-20 — Jev機能を撤去 (ユーザー指示「Jevは意味がない→削除」「無駄な機能はいらねえ」)

- 撤去: app.py の Jev判定パネル・jev_classify_redeem・jev_decide_action・JEV_* 定数一式、
  auto_redeem.py の jev_classify・decide_action・OUTCOME_CRITERIA・JEV_* 定数一式。
- 連鎖撤去: 判定がJev一択だった連続投入ループ (--loop/--interval/--settle/--conf-floor/--yes/
  --result-text) と未使用になった save_codes を削除。支援CLIは下見(--dry-run)・1件貼付テスト
  (--once)・キャプチャ(--shot)のみで、codes.json への保存経路を持たない。
- テスト: test_core T13を「Jev参照なし」ガードに置換。test_auto_redeem は撤去後APIのみ検証
  (B1〜B12・25項目)。回帰は両方全緑。
- 正規フロー=手動 (Alt+G → 人間 Ctrl+V) は不変。自動投入の再開はgit履歴から。

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

## 2026-09-20 — 前提表示追加・UIPI方向逆転確定 (f6a4c38)

- UIPI方向逆転で一本化: 自プロセス・explorer・wwm.exeともSession 1同一でSession分離説は棄却。
  非昇格→非昇格exeはAlt+G到達PASS、非昇格→昇格exeは0/3、管理者wwm.exeへの貼付は吸収。
  terminalセッションからの合成送出は証拠に使わない（実デスクトップ人間押下が正）。
- UI: ツールバー下に前提表示1行（実デスクトップ操作で有効・排他全画面はAlt+Tab運用）。
  文言追加のみ・既存UI影響なし。py_compile OK・test_core 13/13。
- 既知制約: ゲーム排他全画面ではSetForegroundWindow効果なし・Alt+Tabで戻る運用が前提。
  自動G4は見送り除外のまま。正規フロー＝手動（Alt+G→人間Ctrl+V）。

## 2026-09-20 — 貼付手段ラダー完遂・ゲーム側吸収確定

- 3手段を順に実測（同一条件 `--limit 1 --click-xy 1290,741`・used不変・証拠3枚維持）:
  ctrl_v → shift_insert → type_text直接送出。いずれも `focus:OK・field:OK・paste:OK`・
  白枠ハイライトまで到達も入力欄はプレースホルダのまま・`marked=0`。
- 対照実験: 同一SendInput層でメモ帳（medium）には到達（round-trip一致）。
  送出層は正常＝吸収はゲーム側（DirectX排他／アンチチート）と確定。
- 4手段目の追加なし。正規フロー＝手動のまま。G4証跡は `.hermes/evidence/g4_fail/` に14枚。
- ゲート: test_core 13/13 / test_auto_redeem 37/37 / dry-run保存なし。
