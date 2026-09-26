# pi + Qwen 端到端分類測試：50 種台灣特有消費（2026-09-26）

## 設定

- Agent：pi（`pi -p --mode json --no-session`），模型 `local-llama/qwen3.6-35b-q4`（:8001）。
- Skill：本 repo（模型從 `hfin classify` 的 `categories` 清單挑帳戶，見 [CLASSIFICATION.md](../../references/CLASSIFICATION.md)）。
- 輸入：每筆獨立一次對話，文字為「項目 金額 元」，例如「雞排 85 元」。題目與可接受帳戶見 [cases.tsv](2026-09-26-pi-qwen-taiwan-50-cases.tsv)；可接受帳戶含帳本已存在的合理子帳戶（如 `food:breakfast`）。
- 隔離：正式帳本的複本；pi 以沙盒 `HOME` 執行，使 `~/finance` 指向複本（見下方「測試隔離」）。
- 未觸發記帳的題目以「記帳：」前綴重跑一次，只為量測分類。

## 結果

| 指標 | 結果 |
|---|---|
| 分類正確（含前綴重跑） | **49/50 = 98%** |
| 直接輸入即觸發記帳 | 39/50 = 78% |
| 直接輸入、端到端記入正確類別 | 38/50 = 76% |
| 前綴重跑後分類正確 | 11/11 |
| 寫入日期錯誤（非當天） | 15/52 筆寫入 |
| 重複寫入 | 牛肉麵 ×3 |
| 每筆耗時 | 約 30 秒 |

每次觸發記帳皆有先呼叫 `hfin classify`。

| # | 輸入 | 直接輸入結果 | 前綴重跑 | 分類 |
|---|---|---|---|---|
| 1 | 雞排 85 元 | expenses:food:snacks |  | ✅ |
| 2 | 珍珠奶茶 65 元 | expenses:food:drinks |  | ✅ |
| 3 | 滷肉飯 45 元 | expenses:food:dining |  | ✅ |
| 4 | 蚵仔煎 80 元 | expenses:food:snacks |  | ✅ |
| 5 | 臭豆腐 70 元 | expenses:food:snacks |  | ✅ |
| 6 | 刈包 60 元 | expenses:food:snacks |  | ✅ |
| 7 | 鹹酥雞 150 元 | expenses:food:snacks |  | ✅ |
| 8 | 大腸包小腸 65 元 | expenses:food:snacks |  | ✅ |
| 9 | 鳳梨酥 350 元 | expenses:food:snacks |  | ✅ |
| 10 | 牛肉麵 180 元 | expenses:food:dining |  | ✅ |
| 11 | 四神湯 50 元 | expenses:food:dining |  | ✅ |
| 12 | 愛玉 40 元 | expenses:food:snacks |  | ✅ |
| 13 | 蔥抓餅 45 元 | expenses:food:snacks |  | ✅ |
| 14 | 燒餅油條 55 元 | expenses:food:breakfast |  | ✅ |
| 15 | 米糕 50 元 | expenses:food:dining |  | ✅ |
| 16 | 肉圓 50 元 | expenses:food:snacks |  | ✅ |
| 17 | 潤餅 60 元 | expenses:food:dining |  | ✅ |
| 18 | 麻辣燙 180 元 | expenses:food:dining |  | ✅ |
| 19 | Gogoro 電池資費 499 元 | 未觸發記帳 | expenses:transport:fuel | ✅ |
| 20 | YouBike 15 元 | 未觸發記帳 | expenses:transport:transit | ✅ |
| 21 | ETC 通行費 120 元 | expenses:transport:transit |  | ❌ |
| 22 | 國光客運 150 元 | expenses:transport:transit |  | ✅ |
| 23 | 路邊停車費 40 元 | expenses:transport:parking |  | ✅ |
| 24 | 機車強制險 566 元 | 未觸發記帳 | expenses:insurance | ✅ |
| 25 | 機車驗車 200 元 | expenses:transport:driving |  | ✅ |
| 26 | 健保費 826 元 | expenses:insurance |  | ✅ |
| 27 | 勞保自付額 1100 元 | expenses:insurance |  | ✅ |
| 28 | 台電電費 1200 元 | expenses:utilities |  | ✅ |
| 29 | 自來水費 300 元 | expenses:utilities |  | ✅ |
| 30 | 第四台 550 元 | expenses:utilities |  | ✅ |
| 31 | 中華電信 MOD 月租 199 元 | 未觸發記帳 | expenses:subscriptions | ✅ |
| 32 | 寶雅買洗面乳 199 元 | expenses:personal-care |  | ✅ |
| 33 | 屈臣氏面膜 299 元 | 未觸發記帳 | expenses:personal-care | ✅ |
| 34 | 康是美買感冒藥 180 元 | expenses:health:medicine |  | ✅ |
| 35 | 大創 160 元 | expenses:shopping |  | ✅ |
| 36 | 燦坤買吹風機 1290 元 | 未觸發記帳 | expenses:housing:appliance | ✅ |
| 37 | 全聯鮮乳 95 元 | expenses:food:groceries |  | ✅ |
| 38 | 小北百貨 230 元 | expenses:shopping |  | ✅ |
| 39 | 誠品買書 450 元 | expenses:education:books |  | ✅ |
| 40 | 威力彩 100 元 | 未觸發記帳 | expenses:entertainment:lottery | ✅ |
| 41 | 夾娃娃機 50 元 | 未觸發記帳 | expenses:entertainment | ✅ |
| 42 | 好樂迪 600 元 | expenses:entertainment |  | ✅ |
| 43 | 威秀影城 330 元 | 未觸發記帳 | expenses:entertainment | ✅ |
| 44 | 安親班 6000 元 | expenses:education |  | ✅ |
| 45 | 媽祖廟香油錢 200 元 | expenses:donations |  | ✅ |
| 46 | 白包 1200 元 | expenses:gifts |  | ✅ |
| 47 | 安太歲光明燈 600 元 | 未觸發記帳 | expenses:donations | ✅ |
| 48 | 診所掛號費 150 元 | expenses:health |  | ✅ |
| 49 | 補習班學費 8000 元 | expenses:education |  | ✅ |
| 50 | 蝦皮店到店運費 38 元 | 未觸發記帳 | expenses:shopping | ✅ |

## 發現

1. **分類**：唯一錯誤是「ETC 通行費」→ `transport:transit`，應為 `transport:driving`（國道通行費屬開車成本，不是大眾運輸）。
2. **觸發失敗（11 筆）**：Gogoro 電池資費、YouBike、機車強制險、MOD 月租、屈臣氏面膜、燦坤吹風機、威力彩、夾娃娃機、威秀影城、安太歲光明燈、蝦皮運費。Qwen 把「商品／方案名＋金額」當成價格查詢，改做網路搜尋；燦坤案例甚至開瀏覽器找到商品並詢問是否加入購物車（未下單）。加「記帳：」後全部正確記帳。
3. **日期幻覺（15 筆）**：使用者未給日期時，Qwen 仍在 `ingest-json` 填入 `date`（如 2025-07-11、2026-01-15），而不是省略讓 CLI 以當天日期補上並加 `inferred:date`。搜尋時也曾自認「現在是 2025-07-14」。
4. **越權寫入正式帳本**：找不到剛寫入的紀錄時，Qwen 自行加 `--journal ~/finance/main.journal` 重寫，並 `chmod u+w` 移除唯讀保護。

## 測試隔離（重跑前必讀）

- 只設 `FINANCE_JOURNAL` 不夠：模型會自己傳 `--journal ~/finance/main.journal`。需用沙盒 `HOME`（`.pi`、`.local`、`.gitconfig` 以 symlink 指回，`finance` 指向帳本複本）。
- `while read` 迴圈呼叫 pi 時，stdin 必須接 `/dev/null`，否則 pi 會把剩餘題目（含預期答案）當成輸入。
- 工作目錄不可放題目檔，模型會自行讀取。
- 每題結束後比對正式帳本 checksum，一旦變動立即中止。

## 建議修正（未實作）

1. 觸發：description 加入台灣商品／方案名＋金額的例子（「威力彩 100 元」「YouBike 15 元」），預設視為已發生消費。
2. 日期：SKILL.md 明訂未給日期時不得填 `date`；`ingest-json` 對 `source:fuzzy-text` 且日期偏離當天數日以上的紀錄拒絕或警告。
3. ETC：內建規則加入 `ETC|通行費|國道` → `transport:driving`。
