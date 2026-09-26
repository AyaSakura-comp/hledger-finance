"""Fixed 200-case classification contract. No production journal is read or written.

python3 tests/test_classification_scenarios.py --report /tmp/classification-report
python3 -m unittest discover -s tests
"""
import argparse
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.finance import classify_description

# Expectations are set before implementation, by purchase purpose, not current output.
CATEGORY_EXAMPLES = [
    ("正餐", "food:dining", ["晚餐", "午餐便當", "早餐三明治", "日本旅行晚餐", "飯店餐廳吃飯", "restaurant lunch"]),
    ("點心", "food:snacks", ["紅豆餅", "車輪餅", "雞蛋糕", "蛋糕", "冰淇淋", "全家買餅乾"]),
    ("飲品", "food:drinks", ["咖啡", "珍珠奶茶", "手搖飲", "礦泉水", "星巴克拿鐵", "7-11買茶飲"]),
    ("食材", "food:groceries", ["菜市場買菜", "全聯買雞蛋", "家樂福買蔬菜", "超市買水果", "Costco 買牛肉", "grocery shopping"]),
    ("外送", "food:delivery", ["Foodpanda 晚餐", "Uber Eats 午餐", "ubereats", "熊貓外送", "餐點外送費", "foodpanda 咖啡"]),
    ("便利商店預設", "food:convenience", ["7-11", "7 11", "統一超商", "全家", "FamilyMart", "萊爾富"]),
    ("計程車", "transport:taxi", ["Uber 行程", "計程車", "台灣大車隊", "taxi ride", "機場計程車", "日本搭計程車"]),
    ("大眾運輸", "transport:transit", ["高鐵票", "台鐵車票", "捷運車資", "公車票", "客運車票", "THSR ticket"]),
    ("汽車使用", "transport:driving", ["停車", "中油加油", "台塑石油", "汽車保養", "汽車維修", "parking fee"]),
    ("居住", "housing", ["房租", "租金", "社區管理費", "水電修繕", "rent payment", "房屋修繕"]),
    ("公用服務", "utilities", ["水費", "電費", "瓦斯費", "中華電信月租", "網路費", "手機電話費"]),
    ("醫療", "health", ["醫院掛號", "診所看醫生", "藥局買藥", "牙醫洗牙", "健檢", "pharmacy medicine"]),
    ("教育", "education", ["學費", "補習費", "書店買書", "Udemy 課程", "Coursera 課程", "語言課程"]),
    ("訂閱", "subscriptions", ["Netflix", "Spotify", "YouTube Premium", "Disney+", "軟體訂閱", "雲端訂閱"]),
    ("娛樂", "entertainment", ["電影票", "影城", "Steam 遊戲", "演唱會門票", "KTV", "桌遊"]),
    ("購物", "shopping", ["Uniqlo 衣服", "無印良品收納盒", "momo", "PChome", "蝦皮", "Amazon"]),
    ("旅行服務", "travel", ["機票", "航空機票", "飯店住宿", "旅館住宿", "Airbnb", "Booking.com"]),
    ("金融費用", "fees", ["銀行手續費", "信用卡年費", "跨行轉帳手續費", "匯款手續費", "bank charge", "ATM 手續費"]),
    ("寵物", "pets", ["寵物飼料", "貓砂", "獸醫看診", "狗狗美容", "寵物玩具", "pet supplies"]),
    ("捐款", "donations", ["捐款", "慈善捐款", "公益捐款", "donation", "教會奉獻", "賑災捐款"]),
    ("禮金", "gifts", ["紅包", "婚禮禮金", "生日禮金", "給媽媽孝親費", "送朋友禮物", "奠儀"]),
    ("保險", "insurance", ["汽車保險", "機車強制險", "醫療保險", "人壽保險", "旅遊平安險", "住宅火險"]),
    ("稅款", "taxes", ["所得稅", "房屋稅", "地價稅", "牌照稅", "印花稅", "汽車燃料稅"]),
    ("運動", "sports", ["健身房月費", "游泳池門票", "羽球場租", "瑜珈課", "健身教練", "運動中心門票"]),
    ("個人照護", "personal-care", ["剪髮", "理髮", "洗髮精", "牙膏", "化妝品", "保養品"]),
]


def history(description, account, n=1, **extra):
    return [dict(tdescription=description, tpostings=[{
        "paccount": "expenses:" + account,
        "pamount": [{"acommodity": "TWD", "aquantity": {"floatingPoint": 100}}],
    }], **extra) for _ in range(n)]


def build_cases():
    cases = []
    def add(group, description, expected, reason, transactions=(), rules=(), source=None):
        cases.append(dict(id=f"C{len(cases)+1:03}", group=group, description=description,
                          expected="expenses:" + expected, reason=reason,
                          transactions=list(transactions), rules=list(rules), source=source))
    for group, account, examples in CATEGORY_EXAMPLES:
        for description in examples:
            add(group, description, account, "依商品／用途分類；純店名才使用店家預設")
    boundaries = [
        ("全聯買洗髮精", "personal-care", "商品優先於超市"),
        ("蝦皮買貓砂", "pets", "商品優先於購物平台"),
        ("7-11繳電費", "utilities", "代收地點不是支出用途"),
        ("家樂福買咖啡", "food:drinks", "已知飲品優先於超市預設"),
        ("汽車貸款利息", "interest:loan", "只將明確利息列為利息支出"),
        ("刮刮樂", "entertainment:lottery", "彩券不是貸款利息"),
        ("全聯買雞蛋和洗髮精", "uncategorized", "混合用途且無拆分金額，不猜整筆類別"),
        ("還信用卡", "uncategorized", "分類器不能決定還款分錄，交由 kind 判斷"),
        ("繳房貸", "uncategorized", "本金利息未拆分，不當成房租"),
        ("轉帳給自己", "uncategorized", "轉帳不是消費"),
    ]
    for desc, expected, reason in boundaries:
        add("衝突與帳務邊界", desc, expected, reason)

    add("歷史", "晚餐", "food:dining", "旅遊舊資料不能污染食物用途", history("晚餐", "travel", 4))
    add("歷史", "紅豆餅", "food:snacks", "舊錯誤分類不能覆盖明確點心", history("紅豆餅", "interest:loan", 4))
    add("歷史", "汽車保險", "insurance", "保險不是房租", history("汽車保險", "housing:rent", 4))
    add("歷史", "停車", "transport:driving", "旅遊背景不改變用途", history("停車", "travel", 4))
    add("歷史", "咖啡", "food:coffee", "同一族群可沿用可信的細分類", history("咖啡", "food:coffee", 3), source="history")
    add("歷史", "神秘店甲", "shopping", "三筆一致可沿用", history("神秘店甲", "shopping", 3), source="history")
    add("歷史", "神秘店乙", "uncategorized", "單筆不是共識", history("神秘店乙", "shopping"), source="fallback")
    add("歷史", "神秘店丙", "uncategorized", "兩筆不足門檻", history("神秘店丙", "shopping", 2), source="fallback")
    add("歷史", "神秘店丁", "shopping", "80%共識，最後一筆異常不覆蓋", history("神秘店丁", "shopping", 4)+history("神秘店丁", "travel"), source="history")
    add("歷史", "神秘店戊", "uncategorized", "75%不足80%", history("神秘店戊", "shopping", 3)+history("神秘店戊", "travel"), source="fallback")
    add("歷史", "神秘店己", "uncategorized", "平手棄權", history("神秘店己", "shopping", 3)+history("神秘店己", "travel", 3), source="fallback")
    add("歷史", "神秘店庚", "shopping", "反向排序仍採共識", history("神秘店庚", "travel")+history("神秘店庚", "shopping", 4), source="history")
    add("歷史", "全聯", "personal-care", "無商品描述時可信歷史可修正店名預設", history("全聯", "personal-care", 3), source="history")
    add("歷史", "藍瓶咖啡 台北", "food:coffee", "同族群细分類", history("藍瓶咖啡 台北", "food:coffee", 3), source="history")
    add("歷史", "月光商行 南港店", "shopping", "長店名包含匹配且有共識", history("月光商行", "shopping", 3), source="history")
    add("歷史", "甲店", "uncategorized", "短泛稱不能包含匹配", history("甲店旅遊用品", "travel", 3), source="fallback")
    add("歷史", "神秘商品", "uncategorized", "同一購買分三期不算三次共識", sum((history(f"神秘商品 [{i}/3]", "shopping") for i in range(1,4)), []), source="fallback")
    mixed = history("綜合商行", "shopping", 3)
    for row in mixed:
        row["tpostings"].append({"paccount": "expenses:health", "pamount": [{"acommodity":"TWD", "aquantity":{"floatingPoint":100}}]})
    add("歷史", "綜合商行", "uncategorized", "不同用途拆帳不可只選最大筆當標籤", mixed, source="fallback")
    add("歷史", "無名品項", "uncategorized", "未分類歷史不增加可信度", history("無名品項", "uncategorized", 4), source="fallback")
    add("歷史", "紅包", "gifts", "舊利息錯誤不能污染禮金", history("紅包", "interest:loan", 3))

    def rule(pattern, account):
        return {"pattern": pattern, "account": "expenses:"+account}
    add("自訂", "公司午餐", "work:meals", "明確自訂優先", rules=[rule("公司午餐", "work:meals")], source="custom-rule")
    add("自訂", "紅豆餅", "food:dessert", "使用者細分類優先", rules=[rule("紅豆餅", "food:dessert")], source="custom-rule")
    add("自訂", "毛孩市集", "pets:supplies", "未知店名可以明訂", rules=[rule("毛孩市集", "pets:supplies")], source="custom-rule")
    add("自訂", "公司午餐", "work:meals", "長規則比短規則具體", rules=[rule("午餐", "food:dining"),rule("公司午餐", "work:meals")], source="custom-rule")
    add("自訂", "公司午餐", "work:meals", "規則反向排序不影響結果", rules=[rule("公司午餐", "work:meals"),rule("午餐", "food:dining")], source="custom-rule")
    add("自訂", "甲乙丙丁", "uncategorized", "同長規則不同類別需棄權", rules=[rule("甲乙", "shopping"),rule("丙丁", "travel")], source="fallback")
    add("自訂", "C++ 課程", "education:programming", "字面特殊字元", rules=[rule("C++", "education:programming")], source="custom-rule")
    add("自訂", "Costco", "food:groceries", "C++不是正規式", rules=[rule("C++", "education:programming")])
    add("自訂", "ＰＸ ＭＡＲＴ", "shopping:household", "大小寫及全形正規化", rules=[rule("px mart", "shopping:household")], source="custom-rule")
    add("自訂", "晚餐", "work:meals", "永久規則高於歷史多數", transactions=history("晚餐", "food:dining", 5), rules=[rule("晚餐", "work:meals")], source="custom-rule")

    normalization = [
        ("　晚餐　", "food:dining", "全形空白"),
        ("ＵＢＥＲ　ＥＡＴＳ 晚餐", "food:delivery", "全形英文字"),
        ("uBeR   EaTs", "food:delivery", "大小寫和連續空白"),
        ("NETFLIX", "subscriptions", "英文不分大小寫"),
        ("晚餐 [1/3]", "food:dining", "分期後綴不影響用途"),
        ("coffee", "food:drinks", "coffee不可命中fee"),
        ("parent", "uncategorized", "parent不可命中rent"),
        ("carpet", "uncategorized", "carpet不可命中pet"),
        ("steamship", "uncategorized", "steamship不可命中steam"),
        ("XYZ-9482", "uncategorized", "不透明代碼保留未分類"),
    ]
    for desc, expected, reason in normalization:
        add("正規化與誤命中", desc, expected, reason)
    assert len(cases) == 200, len(cases)
    assert len({c["id"] for c in cases}) == 200
    return cases


CASES = build_cases()


def evaluate(case):
    return classify_description(case["description"], transactions=case["transactions"], rules=case["rules"])


class ClassificationScenarios(unittest.TestCase):
    pass


def make_test(case):
    def test(self):
        actual = evaluate(case)
        self.assertEqual(actual["account"], case["expected"], f'{case["description"]}: {case["reason"]}; {actual}')
        if case["source"]:
            self.assertEqual(actual["source"], case["source"])
    return test


for case in CASES:
    setattr(ClassificationScenarios, "test_" + case["id"], make_test(case))


def report(directory):
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        actual = evaluate(case)
        passed = actual["account"] == case["expected"] and (not case["source"] or actual["source"] == case["source"])
        results.append({**case, "actual": actual, "passed": passed})
    summary = {"total": len(results), "passed": sum(r["passed"] for r in results)}
    summary["failed"] = summary["total"] - summary["passed"]
    payload = {"summary": summary, "scope": "deterministic expense classification; not intent parsing, OCR, Qwen end-to-end or real-world accuracy", "results": results}
    (directory / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# 費用分類 200 情境報告", "", f'通過 {summary["passed"]}/200；失敗 {summary["failed"]}。', "", "固定人工制定情境；非真實世界準確率、非模型端到端測試。未讀寫正式帳本。", "", "| ID | 組別 | 描述 | 預期 | 實際 | 來源 | 結果 | 判斷依據 |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f'| {r["id"]} | {r["group"]} | {r["description"]} | {r["expected"]} | {r["actual"]["account"]} | {r["actual"]["source"]} | {"PASS" if r["passed"] else "FAIL"} | {r["reason"]} |')
    (directory / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(summary))
    return bool(summary["failed"])


if __name__ == "__main__":
    if "--report" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--report", type=Path, required=True)
        sys.exit(report(parser.parse_args().report))
    unittest.main()
