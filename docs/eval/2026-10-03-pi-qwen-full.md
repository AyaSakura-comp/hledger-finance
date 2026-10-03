# pi＋Qwen 記帳全功能測試報告

測試日期：2026-10-03（Asia/Taipei）；整理時間 17:59。

## 結論：未通過上線驗收

已執行原 skill 的所有主要功能族群，不只是短句記帳。**backend 能做，並不等於 pi＋Qwen 能可靠地完成。** 本次不修改正式帳本，不刪除原 skill，不部署 extension，不修正生產邏輯。

| 層次 | 結果 |
|---|---|
| 原有 Python backend 回歸 | 445/445 PASS |
| Extension 核心＋測試隔離支援 | 最終 15/15 PASS |
| 新發現的 binding 分類缺陷 | 1 個單獨的確定性回歸測試仍 FAIL |
| 圖表 backend 的完整模板 | 50/50 PASS，實際渲染 PNG／錯誤證據卡 |
| pi＋Qwen 主矩陣 | 106 情境已跑完；原始嚴格評分 20 PASS／86 FAIL |
| 修正測試框架後的四種 agent 畫圖重測 | 1 PASS／3 FAIL（不是替換或洗掉主矩陣結果） |
| Qwen 縮圖視覺檢視 | 五張 contact sheet、50 個情境均回覆 PASS |
| Qwen 全解析度複核 | 六個情境：4 PASS／2 REVIEW；不可宣稱視覺驗收全部完成 |
| 本機 rclone 備份 backend 實際傳輸 | PASS，含一筆交易與 Git 歷史，副本 bytes 相同 |

**20/106 是本次固定帳本工具適配器下的嚴格結果，不是 Qwen 一般準確率，也不是說有 86 個 backend 功能損壞。** FAIL 包含：未真正執行、錯誤帳務事實、錯誤工具／參數、被隔離層擋下的路徑、超時，以及產圖／交付問題。主矩陣另有已確認的產圖框架缺陷，詳見下方校準；保留原始評分，不將其當作精確的正式環境成功率。

## 測試方式與隔離

- **直接 pi SDK＋既有 `local-llama/qwen3.6-35b-q4`，thinking off**；不是用別的模型代答，也不是子代理。未重啟模型服務。
- 主矩陣載入完整原 SKILL.md，references 可讀；支援短句的輸入由 FSM 接管，其餘仍走原 skill。
- 每情境新建隔離 Git 帳本、in-memory session、合成 fixture；刪除確認與重送使用同一 case 的多輪對話。
- 使用實際 finance.py／hledger／matplotlib／rclone。模型能呼叫固定帳本 CLI、唯讀 native report、受限 fixture 讀寫與圖像讀取；**没有 unrestricted bash**。
- `hfin` tool 使用參數陣列而非 shell 字串；不允許模型指定別的 journal、讀取正式檔案、修改原始 journal 或使用外部雲端 remote。這不等於原有 unrestricted pi UI 的端到端測試。
- 正式 `/home/chihmin/finance/main.journal` 只由 host 讀取 checksum，每一情境後均核對不變。
- 每輪 90 秒上限，單情境 32 次工具預算；超時／超預算都列 FAIL，沒有偷偷重跑直至成功。
- 保留所有模型訊息、CLI 參數／payload、exit code、交易、Git 歷史與實際圖檔。完整文字證據：[JSON](2026-10-03-pi-qwen-full-evidence.json)；圖像 base64 在此 JSON 省略以控制大小，原圖仍在 /tmp artifacts。

## 重要的實際問題

1. **刪除範圍與授權不一致（D1，最高優先）**：使用者要求 2026-10-02 Lunch 800；agent 把範圍擴成 10/01..10/03 的 Lunch，列出了 Lunch 與 Lunch refund。下一輪使用者只確認 Lunch 800 一筆，agent 卻沿用雙候選 token 刪了兩筆。undo 確實還原，但不能消除越權刪除問題。純候選 token 綁定不足以保證授權範圍。
2. **只輸出工具呼叫文字，沒有真的記帳**：多數 Cxx 在 READY 後輸出 `finance_finish(category="...")` 文字而非 toolCall；host 正確告知未寫入。50 個台灣消費本次僅 C10、C11 通過完整寫入。這與前次精簡 prompt 三題測試不同，不能把先前小樣本推廣成完整 skill 可用。
3. **忽略明確使用者事實**：F06 將使用者指定 assets:cash 改成 assets:cash:wallet；F17 漏掉指定 --debit，實際寫入 uncategorized，回覆卻宣稱 shopping 且列出與 journal 不同的分期金額。
4. **執行與交付不穩定**：F03 分類後只說「我來記帳」，沒有 ingest；部分報表嘗試被禁止的 -f／journal 路徑；多個匯入與 adjustment 不完成或陷入錯誤迴圈。
5. **確定性 extension bug**：binding custom rule 指向尚未在 journal 使用的子帳戶時，分類 candidates 未包含該帳戶；FinanceFlow.commit 先做 candidates 檢查而拒絕合法 binding 選擇。`known-defects.test.ts` 可不靠模型穩定重現 `Invalid category`。尚未修正。
6. **視覺 reviewer 也會誤判**：Qwen 縮圖判全部 PASS；放大後對 V33/V35 改成 REVIEW。V33 所稱「正淨額不能下降」不是正確累計邏輯（收入0.02後支出0.01會從0.02降到0.01）；V35 的三位小數輸入與 TWD 顯示兩位小數不等價。這些疑慮需要核對資料與顯示規格，不直接宣稱 backend bug 或強行改判通過。

## 分析與畫圖的實測

50 種 backend 情境包含：純收入／純支出／混合、退款、收入沖回、純轉帳錯誤、跨年／閏日、12/24個月、密集資料、中文長標籤、各種 filters、USD/JPY/BTC、小數／大小金額、Unicode 路徑、覆寫、預設輸出、空帳本與顛倒日期。每種都有保留圖檔或預期錯誤證據卡。

主矩陣畫圖：混合、純支出、純收入、雙幣別四種 agent 情境；**前三種中的混合／純支出與雙幣別實際產生了四張圖，純收入沒有產圖**。因此不能把主矩陣「Missing chart files」字樣誤讀成 matplotlib 完全不能畫圖。

已找到並修正兩個**測試框架**問題：
- 隔離 HOME 導致找不到 user-site 的 matplotlib：前導 V1 失敗，正式矩陣已加既有 PYTHONPATH；未安裝新套件或開放正式帳本。
- 主矩陣框架原先拒絕合法 book/reports/*.png 且只搜尋 case 頂層 PNG。補上有 traversal/symlink 保護的 report PNG scope 與遞迴 artifact 檢查，TDD 驗證後**只重測四種產圖**，保留主矩陣原始結果。

修正框架後：
| Agent 圖表 | 結果 | 原因 |
|---|---|---|
| V1 混合收支 | FAIL | 有實際 dashboard.png，但仍有禁止路徑／參數嘗試 |
| V2 純支出 | FAIL | 有實際 expenses.png，但交付 marker 路徑不對，另有禁止嘗試 |
| V3 純收入 | FAIL | 沒有真正呼叫 visualize／產圖 |
| V4 雙幣別 | PASS | TWD、USD 各產圖、1800×1200、檢視並交付兩個正確 marker |

範例圖：`/tmp/hfin-qwen-full-20261003-final/agent/V1/reports/oct-dashboard.png`。資料為合成測試，收入10000、支出含退款為-600、淨額9400；圖上3筆收支交易，不包含另外一筆資產轉帳。

## 全部主矩陣結果

以下為原始嚴格結果，產圖校準重測另列，不混入此表。

| ID | 功能 | 原始結果 | 備註 |
|---|---|---|---|
| F01 | init/check | PASS | 交易／命令／只讀性驗證通過 |
| F02 | expense/defaults | PASS | 交易／命令／只讀性驗證通過 |
| F03 | relative date/payment | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-02","amount":"95","account":"expenses:food:drinks","currency":"TWD","credit":"assets:cash:wallet"} |
| F04 | income | PASS | 交易／命令／只讀性驗證通過 |
| F05 | transfer | PASS | 交易／命令／只讀性驗證通過 |
| F06 | refund | FAIL | Missing expected {"date":"2026-10-03","amount":"120","account":"assets:cash","currency":"TWD","credit":"expenses:food:dining"} |
| F07 | opening balance | PASS | 交易／命令／只讀性驗證通過 |
| F08 | batch fuzzy | PASS | 交易／命令／只讀性驗證通過 |
| F09 | preview | FAIL | 2 forbidden command/path attempts；Missing successful command ingest-json |
| F10 | hypothetical | PASS | 交易／命令／只讀性驗證通過 |
| F11 | ambiguous total | PASS | 交易／命令／只讀性驗證通過 |
| F12 | explicit category | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"150","account":"expenses:work:meals","currency":"TWD","credit":"assets:bank"} |
| F13 | custom binding | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"230","account":"expenses:pets:supplies","currency":"TWD"} |
| F14 | currency routing | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"12.5","account":"expenses:food:drinks","currency":"USD","credit":"assets:bank:usd"} |
| F15 | persist preference | PASS | 交易／命令／只讀性驗證通過 |
| F16 | mixed purposes | FAIL | Missing expected {"date":"2026-10-03","amount":"680","account":"expenses:uncategorized","currency":"TWD"} |
| F17 | installments/write | FAIL | Installment sum/end-month incorrect |
| F18 | installments/preview | PASS | 交易／命令／只讀性驗證通過 |
| F19 | loan schedule | PASS | 交易／命令／只讀性驗證通過 |
| F20 | loan incomplete | PASS | 交易／命令／只讀性驗證通過 |
| F21 | known CSV/import | FAIL | Transaction count 0, expected 3；Missing expected {"date":"2026-09-01","amount":"50","account":"expenses:shopping","currency":"TWD"}；Missing expected {"date":"2026-10-01","amount":"50","account":"expenses:shopping","currency":"TWD"}；Missing …（詳見JSON） |
| F22 | messy CSV | FAIL | Transaction count 0, expected 2；Missing expected {"date":"2026-09-10","amount":"120","account":"expenses:food:dining","currency":"TWD"}；Missing expected {"date":"2026-09-10","amount":"65","account":"expenses:food:drinks","currency":"TWD"}；M…（詳見JSON） |
| F23 | historical signed | FAIL | 3 forbidden command/path attempts；Historical metadata/IDs missing |
| F24 | MOZE principal | PASS | 交易／命令／只讀性驗證通過 |
| F25 | cross currency | PASS | 交易／命令／只讀性驗證通過 |
| F26 | migration adjustment | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-09-08","account":"assets:cash:wallet","credit":"equity:migration:rounding","amount":"0.01","currency":"TWD"}；Agent timeout；13 forbidden command/path attempts；Missing successful …（詳見JSON） |
| F27 | duplicate receipt | PASS | 交易／命令／只讀性驗證通過 |
| F28 | untrusted source | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-09-10","amount":"120","account":"expenses:food:dining","currency":"TWD"} |
| F29 | negative CSV rejection | FAIL | Agent timeout |
| Q1 | query/transactions | FAIL | 2 forbidden command/path attempts |
| Q2 | query/register | FAIL | 1 forbidden command/path attempts；Missing successful command query |
| Q3 | query/balance | FAIL | 2 forbidden command/path attempts；Missing successful command query |
| Q4 | query/income-statement | FAIL | 1 forbidden command/path attempts；Missing successful command query |
| Q5 | query/balance-sheet | FAIL | 2 forbidden command/path attempts |
| Q6 | query/cashflow | FAIL | 3 forbidden command/path attempts |
| Q7 | query/budget | FAIL | 1 forbidden command/path attempts；Missing successful command query |
| Q8 | query/stats | FAIL | 1 forbidden command/path attempts；Missing successful command query |
| Q9 | query filters/export | FAIL | 1 forbidden command/path attempts；Missing successful command query |
| Q10 | query statement HTML | FAIL | Missing successful command query |
| Q11 | stats semantics | FAIL | 1 forbidden command/path attempts |
| Q12 | multi-currency stats | FAIL | 1 forbidden command/path attempts；Missing successful command stats |
| Q13 | native reports | FAIL | 1 forbidden command/path attempts |
| Q14 | native metadata | FAIL | Agent exceeded 32-call budget；8 forbidden command/path attempts；Missing successful command native:print |
| Q15 | native ROI | FAIL | 3 forbidden command/path attempts |
| V1 | visualize mixed | FAIL | 1 forbidden command/path attempts；Missing chart files |
| V2 | visualize expense-only | FAIL | 1 forbidden command/path attempts |
| V3 | visualize income-only | FAIL | Missing successful command visualize；Missing chart files；No rendered visualization |
| V4 | visualize multi-currency | FAIL | 4 forbidden command/path attempts |
| D1 | delete confirmation+undo | FAIL | Confirmed delete did not remove exactly one candidate |
| D2 | stale delete token | PASS | 交易／命令／只讀性驗證通過 |
| D3 | undo batch | FAIL | Transaction count 1, expected 0；Missing successful command undo |
| B1 | backup local transport | FAIL | Missing successful command backup；Backup content mismatch |
| A1 | check+strict audit | PASS | 交易／命令／只讀性驗證通過 |
| I1 | receipt image | FAIL | Missing expected {"date":"2026-09-10","amount":"189","account":"expenses:food:dining","currency":"TWD"} |
| I2 | PDF extracted text | FAIL | Missing expected {"date":"2026-09-10","amount":"105","account":"expenses:food:drinks","currency":"TWD"}；1 forbidden command/path attempts |
| I3 | voice transcript | PASS | 交易／命令／只讀性驗證通過 |
| C01 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"85","account":["expenses:food:snacks","expenses:food:dining"],"currency":"TWD"} |
| C02 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"65","account":["expenses:food:drinks"],"currency":"TWD"} |
| C03 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"45","account":["expenses:food:dining"],"currency":"TWD"} |
| C04 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"80","account":["expenses:food:dining","expenses:food:snacks"],"currency":"TWD"} |
| C05 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"70","account":["expenses:food:snacks","expenses:food:dining"],"currency":"TWD"} |
| C06 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"60","account":["expenses:food:snacks","expenses:food:dining"],"currency":"TWD"} |
| C07 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"150","account":["expenses:food:snacks","expenses:food:dining"],"currency":"TWD"} |
| C08 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"65","account":["expenses:food:snacks","expenses:food:dining"],"currency":"TWD"} |
| C09 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"350","account":["expenses:food:snacks","expenses:gifts"],"currency":"TWD"} |
| C10 | Taiwan classification | PASS | 交易／命令／只讀性驗證通過 |
| C11 | Taiwan classification | PASS | 交易／命令／只讀性驗證通過 |
| C12 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"40","account":["expenses:food:snacks","expenses:food:drinks"],"currency":"TWD"} |
| C13 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"45","account":["expenses:food:snacks","expenses:food:dining","expenses:food:breakfast"],"currency":"TWD"} |
| C14 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"55","account":["expenses:food:dining","expenses:food:breakfast"],"currency":"TWD"} |
| C15 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"50","account":["expenses:food:dining","expenses:food:snacks"],"currency":"TWD"} |
| C16 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"50","account":["expenses:food:dining","expenses:food:snacks"],"currency":"TWD"} |
| C17 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"60","account":["expenses:food:dining","expenses:food:snacks"],"currency":"TWD"} |
| C18 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"180","account":["expenses:food:dining"],"currency":"TWD"} |
| C19 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"499","account":["expenses:transport:driving","expenses:transport:vehicle","expenses:transport:fuel","expenses:subscriptions"],"currency":"TWD"} |
| C20 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"15","account":["expenses:transport:transit"],"currency":"TWD"} |
| C21 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"120","account":["expenses:transport:driving","expenses:transport:vehicle"],"currency":"TWD"} |
| C22 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"150","account":["expenses:transport:transit"],"currency":"TWD"} |
| C23 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"40","account":["expenses:transport:driving","expenses:transport:parking"],"currency":"TWD"} |
| C24 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"566","account":["expenses:insurance"],"currency":"TWD"} |
| C25 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"200","account":["expenses:transport:driving","expenses:transport:vehicle"],"currency":"TWD"} |
| C26 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"826","account":["expenses:insurance","expenses:health"],"currency":"TWD"} |
| C27 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"1100","account":["expenses:insurance"],"currency":"TWD"} |
| C28 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"1200","account":["expenses:utilities"],"currency":"TWD"} |
| C29 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"300","account":["expenses:utilities"],"currency":"TWD"} |
| C30 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"550","account":["expenses:utilities","expenses:subscriptions","expenses:entertainment","expenses:entertainment:streaming"],"currency":"TWD"} |
| C31 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"199","account":["expenses:subscriptions","expenses:utilities","expenses:utilities:telecom","expenses:entertainment:streaming"],"currency":"TWD"} |
| C32 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"199","account":["expenses:personal-care","expenses:personal:grooming","expenses:shopping:beauty"],"currency":"TWD"} |
| C33 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"299","account":["expenses:personal-care","expenses:personal:grooming","expenses:shopping:beauty"],"currency":"TWD"} |
| C34 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"180","account":["expenses:health","expenses:health:medicine"],"currency":"TWD"} |
| C35 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"160","account":["expenses:shopping"],"currency":"TWD"} |
| C36 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"1290","account":["expenses:shopping","expenses:shopping:electronics","expenses:housing:appliance"],"currency":"TWD"} |
| C37 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"95","account":["expenses:food:groceries","expenses:shopping:groceries"],"currency":"TWD"} |
| C38 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"230","account":["expenses:shopping","expenses:housing:supplies"],"currency":"TWD"} |
| C39 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"450","account":["expenses:education","expenses:education:books"],"currency":"TWD"} |
| C40 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"100","account":["expenses:entertainment:lottery"],"currency":"TWD"} |
| C41 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"50","account":["expenses:entertainment"],"currency":"TWD"} |
| C42 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"600","account":["expenses:entertainment"],"currency":"TWD"} |
| C43 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"330","account":["expenses:entertainment"],"currency":"TWD"} |
| C44 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"6000","account":["expenses:education"],"currency":"TWD"} |
| C45 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"200","account":["expenses:donations"],"currency":"TWD"} |
| C46 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"1200","account":["expenses:gifts"],"currency":"TWD"} |
| C47 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"600","account":["expenses:donations"],"currency":"TWD"} |
| C48 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"150","account":["expenses:health","expenses:health:clinic"],"currency":"TWD"} |
| C49 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"8000","account":["expenses:education","expenses:education:course"],"currency":"TWD"} |
| C50 | Taiwan classification | FAIL | Transaction count 0, expected 1；Missing expected {"date":"2026-10-03","amount":"38","account":["expenses:shopping","expenses:fees"],"currency":"TWD"} |

## 未宣稱已驗證的外部／介面功能

- Google Drive OAuth／真正的 gdrive 遠端上傳：**未測**，使用本機 qa: alias；backend copy 通過，但 B1 的 Qwen agent 沒有完成備份操作。
- 原始 PDF 檔解碼／OCR、實際語音轉錄：**未測**。I2 測已擷取 PDF 文字，I3 測已提供的語音逐字稿；I1 是實際附圖的合成收據。
- 台灣電子發票下載、即時銀行連線、推播：skill 本就未部署，不冒稱通過。
- PiWeb／Discord 的實際圖檔送達、互動 UI 端到端：**未測**；本次檢驗正確 media marker 與檔案存在。
- 原生 hledger 的所有進階 flags、ROI 有真實投資 holdings 的數值對帳、close/rewrite 等 native mutation：不在此次安全適配器的保證範圍；唯讀 fallback 代表命令已測，但不是覆蓋整個 hledger CLI。
- 不做模型服務／KV cache 根因結論。訊息中出現前一案例路徑與 facts，可能有多種原因，需要額外可控重現；沒有據此更改服務配置。

## 檔案與重現

- Runner：`extension/full-eval.ts`；案例矩陣：`extension/full-eval-cases.ts`。
- 測試隔離／精確 amount checker：`extension/full-eval-support.ts`／`.test.ts`。
- Qwen 圖像檢視：`extension/visual-review.ts`。
- 確定性未修缺陷：`extension/known-defects.test.ts`（目前執行應回 exit 1，不能算進綠燈測試）。
- 原始 artifacts：`/tmp/hfin-qwen-full-20261003-final`。
- 校準後圖表重測：`/tmp/hfin-qwen-full-20261003-chart-retest`。
- 50種圖表 artifacts：`/tmp/hfin-qwen-full-20261003-1708/visualization`。
- 正式帳本不變；本次 production finance backend / extension core / SKILL.md 都未改動。新增評估工具與報告，沒有 commit／push／全域載入。

```bash
cd /home/chihmin/src/hledger-finance
python3 -m unittest discover -s tests
cd extension
./node_modules/.bin/tsx --test core.test.ts index.test.ts full-eval-support.test.ts
./node_modules/.bin/tsx --test known-defects.test.ts  # known FAIL
EVAL_OUTPUT=/tmp/a-new-unique-qa-dir ./node_modules/.bin/tsx full-eval.ts
```

**下一步應先處理刪除授權範圍、實際工具執行與 facts ownership，再修分類 binding 與報表／產圖路徑，之後重跑整份矩陣。現在不建議啟用到正式帳本。**
