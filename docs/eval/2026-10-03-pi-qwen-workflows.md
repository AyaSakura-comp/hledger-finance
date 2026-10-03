# pi＋Qwen 完整財務 extension 評估（2026-10-03）

模型：`local-llama/qwen3.6-35b-q4`（pi SDK 直接 session，thinking off）。全部使用隔離的暫存 Git 帳本；每個案例前後比對正式帳本 checksum，**未變動**。原始數據：`2026-10-03-pi-qwen-workflows-evidence.json`。

## 結果

| 範圍 | 結果 | 備註 |
|---|---:|---|
| 17 個 guarded workflow 情境 × 4 輪 | **68/68** | 每輪皆 17/17 |
| 50 個台灣單筆支出（host fast path） | **47/50** | 前次報告為 2/50 |
| Backend 單元測試 | 453/453 | |
| Extension 單元測試 | 42/42 | 含已修復的 custom binding 回歸 |

Workflow 情境（中位數秒數，含模型與 backend，不含使用者確認等待）：

| 情境 | 秒 | 情境 | 秒 |
|---|---:|---|---:|
| 收據圖片 | 5.4 | 分期 | 2.8 |
| PDF 收據 | 4.7 | 貸款排程 | 3.9 |
| 一次三筆 | 6.5 | 歷史 CSV 匯入（含試算＋稽核） | 10.3 |
| 收入 | 2.8 | 查詢餘額 | 1.6 |
| 跨幣轉帳 | 3.9 | 統計 | 5.6 |
| 外幣支出 | 2.7 | 逐幣別圖表 | 6.8 |
| 退款＋期初 | 3.4 | 刪除候選（部分確認不執行） | 1.6 |
| undo | 1.6 | 新增分類規則 | 2.8 |
| 備份 | 2.8 | | |

Fast path 剩餘失敗：C01、C48 模型仍輸出文字而非工具呼叫（未寫入，host 回報）；C19「Gogoro 電池資費」分到 `expenses:transport:transit`，屬分類判斷差異。

## 本輪修正的根因

1. **llama.cpp 跨對話 KV 洩漏（qwen-mtp 伺服器）**：研究分支 `c529d79d4` 對 hybrid（recurrent）模型在 checkpoint 還原後略過 `llama_memory_seq_rm`，舊對話的 attention KV 與新 token 並存。直接重現 7/8 個新對話回答成第一個對話的路徑；upstream 與舊 build 皆 0/8。修正 `3b5f5e9ff` 已部署（舊 binary 備份為 `llama-server.pre-seqrm-fix-20261003`），修正後 0/8，cache 重用與 MTP 速度不變。此 bug 影響所有使用 :8001 的 session，不只記帳。
2. **Host 擁有可確定的事實**：來源標籤由 host 依實際來源設定；使用者明文的跨幣轉帳兩側與單一付款帳戶由 host 套用；expense/income/refund 借貸恰好顛倒時依 kind 正規化。
3. **傳輸容錯但不猜資料**：安全整數轉十進位字串（小數 JSON number 仍拒絕）；只移除提早關閉 root 的多餘 `}`／`]`，或補上**恰好一個**結尾缺少的 closer。
4. **工具呼叫強制**：FSM 擁有回合時對 OpenAI 相容 payload 設 `tool_choice: "required"`；另有一次性 nudge。
5. **錯誤訊息可修正**：帳戶家族、刪除過濾語法、`controls` 欄位、未知欄位皆說明預期格式（count 錯誤不洩漏預期值）。
6. 唯讀 inspection 失敗不再終止流程；同一張收據的完全重複列被拒絕；跨幣轉帳不可再另記一筆收入。

## 未驗證／限制

- （2026-10-04 已補測：gateway 確認往返、擬真收據照片、Google Drive 備份、語音，見下節。）仍未測：使用者本人的真實收據照片、piweb 網頁畫面上的實際顯示。
- 樣本為合成資料，情境固定；不等於任意說法都能正確解讀。
- 2026-10-04 起已全域安裝；正式帳本僅做唯讀驗證。

## 2026-10-04 integration tests and install

Real pi (`~/src/pi-agent`) + local Qwen, real gateway prompt formats, disposable journals unless noted.

| Item | Result |
|---|---|
| RPC (piweb text path): preview → `確認` | Works after fixes: preview shown via displayed custom message; confirm turn ends on new `input_handled` event (previously piweb would show an empty reply and the confirm turn would hang) |
| Print mode (attachment path): preview and `確認` in separate processes | Works via persisted authorization; replayed token refused; journal has one transaction |
| Realistic receipt photo (全聯, 小計480/折扣-20/合計460, rotated, noisy JPEG) | 5/6 previews correct (460, 2026-10-02, groceries, cash), all confirmed cross-process; re-sent same photo skipped as duplicate (1 transaction total). Fixed on the way: invented accounts, `Assets:Cash` casing, `NTD`, model-supplied import ids, total+line-item double counting, parallel inspections |
| Google Drive backup (`gdrive:` real remote, test folder then purged) | Bytes identical. **Warning: rclone's shared Google client_id is being retired during 2026; create your own client_id or backups will stop** |
| Voice (edge-tts → Breeze ASR :8025 → Discord envelope) | 3/3 recorded correctly (一百五十元→150, 四百六十塊→460, 五萬元→50000 with preview + confirm) |
| Non-finance message with finance words in history | Not intercepted |
| Regression | workflows 34/34 (2 runs), Taiwan fast path 49/50 (C38 category choice), backend 454/454, extension 50/50 |
| Production journal, read-only stats with the installed package | Correct data, checksum unchanged |
