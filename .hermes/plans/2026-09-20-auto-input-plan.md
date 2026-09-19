# WWM-CodeInput Computer Use + Jev 自動入力 — 実装計画書

作成: 2026-09-20 (Dv00 テックリード) / 要求: @user「Computer Use＋Jevによる自動入力機能を実装せよ」+ 手入力サンプル動画 (7秒, 3.5MB)

## 1. 要件 (確定)

- R1: `codes.json` の未使用コードを先頭から順にゲーム (Steam版デスクトップクライアント) の「報酬引換」モーダルへ自動投入する。1コードごとに **貼付 → Space確定 → 結果取込 → Jev判定 → マーク** の1サイクルを回す。
- R2: 結果メッセージは **キャプチャ → OCRテキスト化 → Jev 3問並列 (outcome/is_success/certainty)** で分類し、Confidence-Gated Routing (`next / retry_same / human / done`, conf floor 0.5) に従う。`conf < 0.5` は自動マークせず人間確認。
- R3: 安全装置を維持する。既定 dry-run (保存なし)、`--yes` でのみ `codes.json` 保存。件間隔 既定8秒、rate_limited時は倍増。未導入パッケージを増やさない (stdlib + pywin32 + PIL のみ、winocrは任意)。
- R4: 手入力と同等の操作を再現する。モーダルが閉じたら次コード用に再オープン前提で1件ずつ処理し、失敗時は Esc で復帰できること。
- R5: `app.py` の既存資産 (Alt+G コピー / Alt+Shift+G 自動ペースト / Jev判定パネル) と二重実装にしない。CLI (`auto_redeem.py`) を正本とし、GUIからは呼び出すだけ。

## 2. 現状ギャップ (実測根拠)

- 動画解析 (`.hermes/evidence/frame_01..07.png`, fps=1抽出, 全7フレーム目視):
  - 背景: 引換設定画面。右説明文「引換コードを入力して報酬を受け取れる。報酬はメールで配布される。」左に入力バー+`>>`ボタン+ON/OFFトグル5行。
  - モーダル: 中央「報酬引換」+ 横長入力欄 (空プレースホルダ「引換コードを入力」) + 下部「Esc 戻る / Space 確定」。
  - フロー実測: 空モーダル → `QR8JMF6367` 充填 → モーダル閉 (設定画面に戻る) → 空モーダル → `QCDQD4K8TE` 充填 → 確定待ち → 閉じる。**2件/7秒 ≒ 3〜4秒/件の手入力**。結果メッセージのポップアップは7フレーム中に映っていない (成功/失敗文言の出現位置・表示時間は未確定)。
- 実装済み (`git log` HEAD 05525bb, app.py 2556行 / auto_redeem.py 390行):
  - `auto_redeem.py --once`: ゲーム検出→フォーカス→クリップボード→Ctrl+V まで自動、結果は人間貼付/OCR (`--result-text` / `--shot`)。**連続ループなし、全自動なし**。
  - `app.py` Jev判定パネル (1025〜1554行): 結果文の手入力→判定→「判定通りにマーク」。OCR連携なし。
  - `decide_action` の2実装 (`app.py:239` / `auto_redeem.py:158`) は同等だが重複している。
- 不足: (a) 連続投入ループ (b) Space確定キー送出 (c) 確定後の結果キャプチャ領域・タイミング (d) rate_limited時の待機倍増の実装 (e) 単体テスト (`test_core.py` はJSON I/Oのみ、Jev分岐のテストなし)。

## 3. 設計方針

- Computer UseはDOMではなく **Win32実入力** で行う (`focus_window` → `set_clipboard` → `send_ctrl_v` → Space送出 → `capture_window` → `ocr_image`)。既存 `auto_redeem.py` の関数を拡張し、新規依存は入れない。
- Space確定は `send_ctrl_v` と同型の `send_key(vk)` 追加 (keybd_event)。クリック座標の画像認識はやらない (lean: キー確定で足りることが動画で判明)。
- 結果取込は2経路: (1) winocr導入済みなら自動OCR (空文字ならhumanへ)、(2) 未導入/空なら `--result-text` 手貼り (半自動維持)。OCR必須化しない。
- Jev判定は既存 `jev_classify` / `decide_action` を正とし、`app.py` 側は thin wrapper に寄せる (重複の片寄せは本計画の最終タスク、振る舞い変更なし)。
- 結果モーダルの出現位置・文言が動画で未確定のため、初回E2Eは **1件実投入 + 目視確認** を必須とし、キャプチャ領域 (全ウィンドウ vs モーダル中央) と待機秒を実測で決める。推測で固定しない。

## 4. 実装タスク (番号付き, @dv01-engineer 担当)

- T1: `auto_redeem.py` に `send_key(vk)` (Space=0x20, Esc=0x1B) を追加。`send_ctrl_v` と同型 (keybd_event)、単体でキー送出のみテスト可能に。
- T2: 連続ループ `run_loop(codes, limit, interval, conf_floor, yes)` を追加。1件フロー = next_unused → focus → set_clipboard → Ctrl+V → Space → 待機 (既定2秒, 可変 `--settle`) → capture → OCR → Jev → decide → (yes時のみ保存・マーク)。`--loop --limit N --interval 8 --settle 2 --yes/--dry-run` のCLI配線。rate_limited → 次件の待機を倍増 (上限60秒)。`human` → 停止してコード・OCR文・判定をログに残す (勝手に進めない)。
- T3: 結果キャプチャの実測対応。`--shot` で確定直後の全ウィンドウPNGを保存し、OCR文字数を表示 (既存)。モーダル中央クロップ (`--crop center`) を追加し、全画面OCRと中央OCRのどちらが結果文を拾えるかE2Eで比較できること。
- T4: `decide_action` の conf floor・rate倍増・human停止の単体テスト `test_auto_redeem.py` を新設 (Jev通信はモック、ゲーム不要)。既存 `test_core.py` T1〜は維持。
- T5: `app.py` 統合 (thin): Jev判定パネルに「自動入力 (CLI) の説明 + `--loop` コマンド雛形表示」程度に留め、GUIからの二重ループ実装はしない。`jev_classify_redeem` と `auto_redeem.jev_classify` の criteria 差分があれば `auto_redeem` 側に寄せる (振る舞い同一化、テストで担保)。
- T6: README に自動入力節を追記 (使い方3行: dry-run → 1件実投入+目視 → loop。`JEV_API_KEY` 必須、頻度制限注意)。`build_exe.py` でexe再ビルドし、`dist/WWMCodeInput.exe` を更新。

## 5. 見送り (明示)

- 座標クリック・画像マッチングによる `>>` ボタン押下 (Space確定で足りるため)。
- browser-use / DOM state space 方式 (対象はデスクトップクライアントのため対象外)。
- winocr の必須化・OCRエンジン追加導入 (任意のまま)。
- 結果文言のハードコード分岐 (Jev分類が正、6パターン実測済みの資産を活かす)。
- 未使用コード全件の一括無人投入の既定化 (既定は停止・確認あり。無人は運用確認後)。
- アンチチート/ToS回避策・権限昇格の自動化 (管理者権限の手動設定という現行手順を維持)。

## 6. テスト設計 (TDD: RED→GREEN→REFACTOR→実測)

- 単体 (ゲーム・通信不要): `test_auto_redeem.py` — next_unused 順序 / decide_action 5分岐 (success/already_used/expired→next, rate_limited→retry_same, other/低conf→human) / rate倍増の上限 / dry-runで保存しないこと。Jevはダミーgate辞書で注入。
- 結合 (dry-run): `python auto_redeem.py --dry-run --limit 3` + `--result-text 'Already used...'` のJev実投下1件 (API疎通確認、課金の浪費を避け1件のみ)。
- E2E (要ゲーム・要目視): `--shot` で確定直後キャプチャ→OCR文字数確認 → `--once --yes --interval 8` で1件実投入+目視 → 問題なければ `--loop --limit 3`。全件ループは初回やらない。
- 回帰: `test_core.py` 全PASS + 新テスト全PASS。exe起動 (`start.bat` 経路) の起動確認。

## 7. 完了ゲート (1件でも欠ければリリース差し戻し)

- G1: `test_core.py` + `test_auto_redeem.py` 全緑 (件数を報告)。
- G2: `--dry-run` で保存なし・次コード表示を確認 (証跡ログ)。
- G3: Jev実投下1件の gate JSON (outcome/conf/elapsed_ms) を証跡保存。
- G4: ゲーム実機1件実投入の目視確認 (投入コード・確定キー・結果表示の3点を報告)。
- G5: exe再ビルド + `start.bat` 起動確認。
- G6: Dv00独立検証 (正規venv/実測再実行) + @dv03-qa ゲートPASS → Dv00リリース判断 → @user報告。

## 8. 判断記録

- 手入力速度の実測 (約3〜4秒/件) に対し interval既定8秒は控えめだが、頻度制限 (`Operated too frequently`) 回避を優先し据え置く。短縮は実測でrateが出ないことを確認してから。
- 結果メッセージの出現位置が動画で特定できないため、キャプチャ範囲を推測固定せず `--crop` 比較で実測決定する。
- ユーザー沈黙時は本計画の推奨案で進行し、切替は一言で受け付ける。
