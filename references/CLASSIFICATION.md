# 費用分類標準

## 範圍與原則

這是 **ordinary expense 的用途分類**，不是交易意圖／方向辨識器。先判定 `kind`，再選類別。薪資是收入；提款、自有帳戶轉帳、信用卡還款及貸款本金不是消費。退款退回原費用類別；本金與利息分開。圖片或對話中的範例不得當成新交易。

分類以「買了什麼／支出用途」為主，店名、國家、付款渠道和旅行背景為輔。日本旅行晚餐仍是餐飲；有明確旅行背景時可另外加 `project:travel`。單寫「晚餐」不得憑歷史旅遊帳目補上旅行標籤。

## 誰做決定

`hfin classify` 回傳 `categories`（全部可選帳戶：穩定類別表＋帳本已用帳戶與使用次數）、`suggestion`（下方規則引擎的判斷，只作為證據）與 `binding`（命中自訂規則時為 true，必須照用）。**由模型從 `categories` 挑一個帳戶**，以明確 `debit` 寫入。下方決策順序是 `suggestion` 的產生方式，也是 `--debit auto`（分期、無分類 CSV 等非逐筆由模型決定的路徑）的後備邏輯；`--rules-only` 只輸出這個判斷。

## 決策順序（規則引擎）

1. **使用者明確指定帳戶**：原樣使用，不被自動分類替換。
2. **已明訂的字面規則**：`classification-rules.json`，不分大小寫、正規化全形與空白。多條匹配時取最長 pattern；最長規則若指向不同帳戶則棄權，不能任取最後一條。
3. **商品／用途證據**：優先於純店名。多個獨立用途（例如雞蛋和洗髮精）且沒有拆分金額，回傳 `mixed-purposes`，不任選其中之一。名稱內部的原料字串不算另一個用途（雞蛋糕≠雞蛋）。
4. **可信歷史共識**：只使用最高匹配等級的一組紀錄。至少 3 筆支持同一帳戶，且占匹配組至少 80%。先完整相同，再長描述包含，再高相似度；短於 4 字只允許完整相同。拒絕最後一筆勝出、平手、多用途歷史、未分類歷史。分期僅第 1 期算一次觀察。
   - 歷史不得覆蓋已知用途：晚餐不能因歷史變旅遊或點心。
   - 相同帳戶或用途帳戶的子帳戶可沿用；保留少數明確相容別名：`food:coffee`／`food:tea` 對應 drinks，`food:breakfast`／`food:lunch`／`food:dinner` 對應 dining。
   - 粗略歷史 `expenses:food` 不取代精確的 dining/snacks。
5. **純商家預設**：沒有明確商品或可信歷史才使用。只寫「全聯」預設食材，但「全聯買洗髮精」必須是個人照護。商家預設是較弱證據，不是商店所有商品都屬於同類。
6. **Agent 語意補充／未分類**：`insufficient-evidence` 且意思明確時，Agent 可依本表指定穩定帳戶。不要覆蓋 `mixed-purposes`、`conflicting-custom-rules` 或 `conflicting-merchants`，除非使用者提供了可消除衝突的資訊。資訊不足時可先記 `expenses:uncategorized` 並告知，不為普通類別反覆詢問。

程式回傳的 `confidence` 是啟發式規則強度，**不是校準後的正確率**。歷史另外提供 `support`、`observations`、`agreement`，避免把相同文字當成相同用途的證明。

## 穩定類別表

以下全部省略 `expenses:` 前綴：

| 類別 | 判準與例子 |
|---|---|
| food:dining | 早餐、午餐、晚餐、便當、餐廳 |
| food:snacks | 紅豆餅、雞蛋糕、餅乾、甜點、冰淇淋 |
| food:drinks | 咖啡、奶茶、礦泉水、拿鐵 |
| food:groceries | 生鮮食材、蔬果、雞蛋、肉品 |
| food:delivery | 明確餐點外送服務／平台；含餐點與飲料時仍可用此類 |
| food:convenience | 只知便利商店、尚無商品明細時的弱預設 |
| transport:taxi | Uber 搭車、計程車 |
| transport:transit | 悠遊卡、高鐵、台鐵、捷運、公車 |
| transport:driving | 停車、加油、汽車保養維修；不含保險與稅 |
| housing | 房租、管理費、房屋修繕；不含房貸本金 |
| utilities | 水電瓦斯、電話、網路；代收商店不影響類別 |
| health | 人的醫療、掛號、藥品、牙醫、健檢 |
| education | 學費、書籍、課程、補習 |
| subscriptions | 影音、音樂、軟體與雲端訂閱 |
| entertainment | 電影、遊戲、演唱會、KTV、桌遊 |
| entertainment:lottery | 刮刮樂、彩券、樂透 |
| shopping | 一般購物或只有電商名稱且用途未知 |
| travel | 機票、住宿、旅行團；不是所有旅行期間的消費 |
| fees | 銀行手續費、信用卡年費；不靠任意英文子字串 `fee` |
| interest:loan | 明確貸款利息，不含本金 |
| pets | 飼料、貓砂、獸醫、寵物用品及美容 |
| donations | 公益、慈善、宗教奉獻 |
| gifts | 紅包、禮金、孝親費、禮物、奠儀 |
| insurance | 汽機車、人壽、醫療、旅平、住宅保險 |
| taxes | 所得稅、房屋稅、地價稅、牌照稅、燃料稅費 |
| sports | 健身房、運動場館、瑜珈、教練 |
| personal-care | 理髮、洗髮精、牙膏、化妝保養品 |
| uncategorized | 無法可靠決定、矛盾規則或尚未拆分的混合用途 |

## 使用者修正與永久規則

更正一筆交易不等於自動建立永久規則。只有明確要求未來也沿用的偏好才寫入 `classification-rules.json`；修改設定須循正式財務 repo 的驗證與 Git 流程。程式不會從單筆更正自動學習，也不會自動重寫歷史帳目。

例如永久將公司午餐列為工作餐費：

```json
{"rules": [{"pattern": "公司午餐", "account": "expenses:work:meals"}]}
```

新增分類規則與回溯更正是兩件事。既有帳目的刪除仍須先展示候選、取得確認，不能為了測試而改正式帳本。

## 可重跑的 200 情境

```bash
python3 tests/test_classification_scenarios.py --report /tmp/hfin-classification-200
python3 -m unittest discover -s tests -v
```

固定 200 案例：25 組常見類別×6=150、10 衝突與帳務邊界、20 歷史污染與共識、10 自訂規則、10 正規化／誤命中。預期結果先於實作制定。每例包含 ID、輸入、預期帳戶、理由及必要的歷史或規則；報告輸出 `results.json` 和 `report.md`。另外有 ingestion／複式平衡／明確覆寫／非支出不套用分類的整合測試。

這是有限的決定性回歸矩陣，不是所有自然語言的正確率，也不是 Qwen、語音、OCR 或多輪對話的端到端測試。規則詞庫仍可能有未知商品、否定詞、多用途或新店名；不能宣稱 200 案例通過代表不再誤判。

## 端到端模型測試

200 情境只測規則引擎。模型從 `categories` 挑選的端到端結果另見 [pi + Qwen 50 種台灣特有消費](../docs/eval/2026-09-26-pi-qwen-taiwan-50.md)：分類 49/50，但直接輸入的記帳觸發率僅 39/50，且有 15 筆日期幻覺。
