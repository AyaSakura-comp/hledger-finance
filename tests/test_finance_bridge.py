import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('finance_bridge', Path(__file__).resolve().parents[1] / 'scripts/finance_bridge.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / 'main.journal'
        bridge.f.main(['--journal', str(self.journal), 'init'])

    def invoke(self, operation, data, mode='preview', **kwargs):
        return bridge.run(self.journal, {'operation': operation, 'data': data}, mode=mode, **kwargs)

    def ingest(self, records):
        request = {'transactions': records}
        p = self.invoke('ingest', request)
        self.assertEqual(p['code'], 0, p)
        return self.invoke('ingest', request, 'execute', expected_snapshot=p['snapshot'])

    def test_all_kinds_batch_explicit_accounts_and_cross_currency(self):
        records = [
            dict(kind='expense', description='Lunch', amount='150', debit='expenses:food:dining', credit='assets:cash'),
            dict(kind='income', description='Salary', amount='50000', debit='assets:bank', credit='income:salary'),
            dict(kind='transfer', description='Exchange', amount='3200', debit='assets:usd', credit='assets:cash', debit_amount='100', debit_currency='USD'),
            dict(kind='refund', description='Lunch refund', amount='20', debit='assets:cash', credit='expenses:food:dining'),
            dict(kind='opening-balance', description='Initial cash', amount='1000', debit='assets:cash', credit='equity:opening-balances'),
        ]
        for n, r in enumerate(records):
            r.update(date='2026-10-03', currency='TWD', tags=['source:fuzzy-text'], import_id=f'row-{n}')
        result = self.ingest(records)
        self.assertEqual(result['code'], 0, result)
        text = self.journal.read_text()
        self.assertIn('100 USD @@ 3200 TWD', text)
        self.assertEqual(text.count('; kind:'), 5)

    def test_stale_snapshot_and_unknown_fields_never_write(self):
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            self.invoke('undo', {}, 'execute', expected_snapshot='bad')
        with self.assertRaises(ValueError):
            self.invoke('stats', {'output': '/etc/file'})
        with self.assertRaises(ValueError):
            self.invoke('query', {'journal': '/etc/passwd'})

    def test_amend_replaces_exactly_one_transaction_atomically(self):
        row = lambda d, a, i: dict(kind='expense', description=d, amount=a, date='2026-10-04', currency='TWD', debit='expenses:food:dining', credit='assets:cash', tags=['source:fuzzy-text'], import_id=i)
        self.ingest([row('早餐', '65', 'b1'), row('午餐', '120', 'l1'), row('午餐', '90', 'l2')])
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.invoke('amend', {'filter': {'description': ['午餐']}, 'transactions': [row('午餐', '100', 'l3')]})
        fix = {'filter': {'description': ['早餐']}, 'transactions': [row('早餐', '80', 'b2')]}
        before = self.journal.read_text()
        p = self.invoke('amend', fix)
        self.assertEqual(p['code'], 0, p)
        self.assertIn('REMOVE', p['stdout'])
        self.assertIn('ADD', p['stdout'])
        self.assertEqual(self.journal.read_text(), before)  # preview writes nothing
        with self.assertRaisesRegex(ValueError, 'token'):
            self.invoke('amend', fix, 'execute', expected_snapshot=p['snapshot'])
        d = self.invoke('amend', fix, 'execute', expected_snapshot=p['snapshot'], delete_token=p['delete_token'])
        self.assertEqual(d['code'], 0, d)
        text = self.journal.read_text()
        self.assertNotIn('65 TWD', text)
        self.assertIn('80 TWD', text)

    def test_amend_failed_replacement_restores_the_original(self):
        r = dict(kind='expense', description='早餐', amount='65', date='2026-10-04', currency='TWD', debit='expenses:food:dining', credit='assets:cash', tags=['source:fuzzy-text'], import_id='b1')
        self.ingest([r])
        good = {'filter': {'description': ['早餐']}, 'transactions': [{**r, 'amount': '80', 'import_id': 'b2'}]}
        p = self.invoke('amend', good)
        bad = {'filter': {'description': ['早餐']}, 'transactions': [{**r, 'amount': '-5', 'import_id': 'b3'}]}
        d = self.invoke('amend', bad, 'execute', expected_snapshot=p['snapshot'], delete_token=p['delete_token'])
        self.assertNotEqual(d['code'], 0)
        self.assertIn('deletion undone', d['stderr'])
        self.assertIn('65 TWD', self.journal.read_text())

    def test_amend_never_deletes_without_adding(self):
        r = dict(kind='expense', description='早餐', amount='65', date='2026-10-04', currency='TWD', debit='expenses:food:dining', credit='assets:cash', tags=['source:fuzzy-text'], import_id='b1')
        other = {**r, 'description': '午餐', 'amount': '120', 'import_id': 'l1'}
        self.ingest([r, other])
        reused = {'filter': {'description': ['早餐']}, 'transactions': [{**r, 'amount': '80', 'import_id': 'l1'}]}
        with self.assertRaisesRegex(ValueError, 'would not be written'):
            self.invoke('amend', reused)
        p = self.invoke('amend', {'filter': {'description': ['早餐']}, 'transactions': [{**r, 'amount': '80', 'import_id': 'b2'}]})
        d = self.invoke('amend', reused, 'execute', expected_snapshot=p['snapshot'], delete_token=p['delete_token'])
        self.assertNotEqual(d['code'], 0)
        self.assertIn('deletion undone', d['stderr'])
        self.assertIn('65 TWD', self.journal.read_text())

    def test_invalid_option_values_return_an_error_receipt(self):
        r = self.invoke('query', {'report': 'transactions', 'format': 'column:date'})
        self.assertNotEqual(r['code'], 0)
        self.assertIn('invalid choice', r['stderr'])

    def test_delete_requires_host_token_and_identical_candidates(self):
        r = dict(kind='expense', description='Lunch', amount='800', date='2026-10-03', currency='TWD', debit='expenses:food:dining', credit='assets:cash', tags=['source:fuzzy-text'], import_id='lunch')
        self.ingest([r])
        filters = {'tag': ['import-id:lunch']}
        p = self.invoke('delete', filters)
        self.assertEqual(p['code'], 3, p)
        with self.assertRaises(ValueError):
            self.invoke('delete', {**filters, 'confirm': p['delete_token']})
        d = self.invoke('delete', filters, 'execute', expected_snapshot=p['snapshot'], delete_token=p['delete_token'])
        self.assertEqual(d['code'], 0, d)
        self.assertNotIn('Lunch', self.journal.read_text())

    def test_rules_are_strict_atomic_and_binding_is_available(self):
        rule = {'kind': 'classification', 'value': {'rules': [{'pattern': '毛孩', 'account': 'expenses:pets:supplies'}]}}
        p = self.invoke('rules', rule)
        w = self.invoke('rules', rule, 'execute', expected_snapshot=p['snapshot'])
        self.assertEqual(w['code'], 0)
        c = self.invoke('classify', {'description': '毛孩'})
        evidence = json.loads(c['stdout'])
        self.assertTrue(evidence['binding'])
        self.assertEqual(evidence['suggestion']['account'], 'expenses:pets:supplies')
        with self.assertRaises(ValueError):
            self.invoke('rules', {'kind':'payment', 'value': {'currency_accounts': {'TWD':'income:bad'}, 'require_match':True}})

    def test_historical_control_totals_are_mandatory_and_reconciled(self):
        r = dict(kind='expense', description='Lunch', amount='150', date='2026-10-03', currency='TWD', debit='expenses:food:dining', credit='assets:cash', tags=['source:historical-import'], import_id='source-row')
        with self.assertRaises(ValueError):
            self.invoke('historical', {'payload': {'transactions':[r]}, 'controls': {'count':1, 'totals': {'expense:TWD':'151'}}})
        result = self.invoke('historical', {'payload': {'transactions':[r]}, 'controls': {'count':1, 'totals': {'expense:TWD':'150'}}})
        self.assertEqual(result['code'], 0, result)
        self.assertIn('Isolated trial and strict audit passed', result['stderr'])
        self.assertNotIn('Lunch', self.journal.read_text())

    def test_installments_loan_queries_and_undo_are_real_backend_operations(self):
        i = {'start':'2026-01-31','description':'Phone','total':'900','count':'3','currency':'TWD','debit':'expenses:shopping','credit':'liabilities:credit-card'}
        p = self.invoke('installment', i)
        self.assertEqual(p['code'], 0, p)
        result = self.invoke('installment', i, 'execute', expected_snapshot=p['snapshot'])
        self.assertEqual(result['code'], 0, result)
        self.assertIn('2026-02-28', self.journal.read_text())
        q = self.invoke('query', {'report':'transactions','format':'json'})
        self.assertEqual(len(json.loads(q['stdout'])), 3)
        p = self.invoke('undo', {})
        self.assertEqual(self.invoke('undo', {}, 'execute', expected_snapshot=p['snapshot'])['code'], 0)
        self.assertNotIn('Phone', self.journal.read_text())
        loan = {'loan_id':'qa-loan','start':'2026-10-03','description':'Loan payment','principal':'500','annual_rate':'0','payment':'250','paid_periods':'0','remaining_periods':'2','currency':'TWD','liability':'liabilities:loans:personal','interest_account':'expenses:interest:loans','payment_account':'assets:bank','rounding':'1'}
        p = self.invoke('loan', loan)
        self.assertEqual(p['code'], 0, p)
        r = self.invoke('loan', loan, 'execute', expected_snapshot=p['snapshot'])
        self.assertEqual(r['code'], 0, r)
        self.assertIn('source:scheduled-loan', self.journal.read_text())
        self.assertEqual(self.invoke('audit', {'strict':True})['code'], 0)

    def test_rule_undo_reverts_rule_action_not_an_earlier_journal_write(self):
        rule = {'kind':'payment','value': {'currency_accounts': {'TWD':'assets:cash:wallet'}, 'require_match': True}}
        p = self.invoke('rules', rule)
        self.invoke('rules', rule, 'execute', expected_snapshot=p['snapshot'])
        p = self.invoke('undo', {})
        self.assertIn('Update finance rules: payment', p['stdout'])
        result = self.invoke('undo', {}, 'execute', expected_snapshot=p['snapshot'])
        self.assertEqual(result['code'], 0, result)
        self.assertFalse((self.journal.parent/'payment-account-rules.json').exists())

    def test_historical_reused_id_must_match_stored_amount(self):
        r = dict(kind='expense', description='Lunch', amount='150', date='2026-10-03', currency='TWD', debit='expenses:food:dining', credit='assets:cash', tags=['source:historical-import'], import_id='stable-row')
        data = {'payload': {'transactions':[r]}, 'controls': {'count':1, 'totals': {'expense:TWD':'150'}}}
        p = self.invoke('historical', data)
        self.invoke('historical', data, 'execute', expected_snapshot=p['snapshot'])
        r['amount']='151';data['controls']['totals']['expense:TWD']='151'
        with self.assertRaisesRegex(ValueError, 'stored|mismatch'):
            self.invoke('historical', data)

if __name__ == '__main__':
    unittest.main()
