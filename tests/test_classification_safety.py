"""Integration and adversarial checks beyond the fixed 200-case matrix."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.finance import build_ingest_transactions, classify_description
from test_classification_scenarios import history


class ClassificationSafetyTests(unittest.TestCase):
    def test_easycard_is_classified_as_transit(self):
        self.assertEqual(classify_description('悠遊卡')['account'], 'expenses:transport:transit')

    def test_longer_food_name_beats_embedded_ingredient(self):
        self.assertEqual(classify_description('雞蛋糕')['account'], 'expenses:food:snacks')

    def test_compound_food_plus_separate_ingredient_remains_mixed(self):
        self.assertEqual(classify_description('雞蛋糕和雞蛋')['account'], 'expenses:uncategorized')

    def test_purpose_cannot_be_overridden_by_wrong_food_subcategory(self):
        self.assertEqual(classify_description('晚餐', transactions=history('晚餐', 'food:snacks', 4))['account'], 'expenses:food:dining')

    def test_known_mixed_purposes_cannot_be_overridden_by_history(self):
        self.assertEqual(classify_description('雞蛋和洗髮精', transactions=history('雞蛋和洗髮精', 'food:groceries', 8))['account'], 'expenses:uncategorized')

    def test_large_fee_is_not_silently_discarded_from_mixed_history(self):
        rows = history('神秘消費', 'shopping', 3)
        for row in rows:
            row['tpostings'].append({'paccount':'expenses:fees', 'pamount':[
                {'acommodity':'TWD', 'aquantity':{'floatingPoint':2000}}]})
        self.assertEqual(classify_description('神秘消費', transactions=rows)['account'], 'expenses:uncategorized')

    def test_extreme_installment_label_cannot_crash_classification(self):
        rows = history('神秘消費 [' + '9'*5000 + '/6000]', 'shopping', 3)
        self.assertEqual(classify_description('神秘消費', transactions=rows)['account'], 'expenses:uncategorized')

    def test_fee_and_purchase_in_different_currencies_are_not_compared(self):
        rows = history('神秘消費', 'shopping', 3)
        for row in rows:
            row['tpostings'].append({'paccount':'expenses:fees', 'pamount':[
                {'acommodity':'USD', 'aquantity':{'floatingPoint':1}}]})
        self.assertEqual(classify_description('神秘消費', transactions=rows)['account'], 'expenses:uncategorized')

    def test_delivery_platform_cannot_hide_nonfood_purchase(self):
        self.assertEqual(classify_description('foodpanda 洗髮精')['account'], 'expenses:uncategorized')

    def test_corpus_ingestion_balances_and_honors_explicit_override(self):
        payload = {'defaults': {'date': '2026-01-01', 'kind': 'expense', 'currency': 'TWD', 'tags': ['source:fuzzy-text']},
                   'transactions': [{'description':'晚餐', 'amount':'260'},
                                    {'description':'紅豆餅', 'amount':'30'},
                                    {'description':'汽車保險', 'amount':'1800'},
                                    {'description':'公司午餐', 'amount':'120', 'debit':'expenses:work:meals'}]}
        text, count, skipped, decisions = build_ingest_transactions(
            payload, existing_text='', classification_transactions=history('晚餐','travel',4),
            payment_account_rules={'currency_accounts':{'TWD':'assets:cash:wallet'},'require_match':True})
        self.assertEqual((count, skipped), (4,0))
        for account in ['food:dining','food:snacks','insurance','work:meals']:
            self.assertIn('expenses:'+account, text)
        self.assertNotIn('expenses:travel', text)
        self.assertIn('assets:cash:wallet', text)
        self.assertIn('inferred:currency-routing', text)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.journal'
            path.write_text(text, encoding='utf-8')
            result=subprocess.run(['hledger','-f',str(path),'check'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_income_transfer_refund_bypass_expense_inference(self):
        payload = {'defaults': {'date':'2026-01-01','currency':'TWD','tags':['source:fuzzy-text']},
                   'transactions':[
                       {'kind':'income','description':'餐廳薪水','amount':'50000','debit':'assets:bank','credit':'income:salary'},
                       {'kind':'transfer','description':'繳房貸本金','amount':'10000','debit':'liabilities:loans:home','credit':'assets:bank'},
                       {'kind':'refund','description':'咖啡退款','amount':'50','debit':'assets:cash','credit':'expenses:food:coffee'}]}
        text, count, skipped, _ = build_ingest_transactions(payload, existing_text='')
        self.assertEqual((count, skipped),(3,0))
        self.assertIn('income:salary',text)
        self.assertIn('liabilities:loans:home',text)
        self.assertIn('expenses:food:coffee',text)
        self.assertNotIn('expenses:food:dining',text)
        self.assertNotIn('expenses:housing',text)


if __name__ == '__main__':
    unittest.main()
