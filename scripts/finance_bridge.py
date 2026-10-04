#!/usr/bin/env python3
"""Fixed JSON bridge for the Pi finance FSM. Never accepts shell commands/argv."""
from contextlib import nullcontext, redirect_stdout, redirect_stderr
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

# Also supports importlib-based unit tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finance as f

READONLY = {'query', 'stats', 'visualize', 'check', 'audit', 'classify', 'native', 'settings'}
MUTATIONS = {'ingest', 'historical', 'installment', 'loan', 'delete', 'undo', 'rules', 'backup', 'amend'}
ALIASES = {'loan': 'loan-resume'}
FILTERS = {'period', 'begin', 'end', 'account', 'description', 'tag', 'where'}
NATIVE_REPORTS = {'accounts', 'activity', 'aregister', 'balance', 'balancesheet', 'balancesheetequity', 'cashflow', 'codes', 'commodities', 'descriptions', 'incomestatement', 'notes', 'payees', 'prices', 'print', 'register', 'roi', 'stats', 'tags'}
NATIVE_FLAGS = {'begin', 'end', 'period', 'forecast', 'market', 'value', 'monthly', 'daily', 'weekly', 'quarterly', 'yearly', 'depth', 'flat', 'tree', 'cost', 'investment', 'pnl', 'cashflow', 'today', 'output-format'}
RULE_FILES = {'classification': 'classification-rules.json', 'payment': 'payment-account-rules.json'}


def strict_object(value, keys, name='data'):
    if not isinstance(value, dict):
        raise ValueError(f'Unknown or invalid structured fields: {name} must be an object with fields {sorted(keys)}')
    unknown = set(value) - set(keys)
    if unknown:
        raise ValueError(f'Unknown or invalid structured fields in {name}: {sorted(unknown)}; allowed {sorted(keys)}')


def invoke(journal, args, payload=None):
    out, err = io.StringIO(), io.StringIO()
    previous = sys.stdin
    original_run = subprocess.run
    def captured_run(*args, **kwargs):
        implicit = not kwargs.get('capture_output') and 'stdout' not in kwargs and 'stderr' not in kwargs
        if implicit:
            kwargs.update(capture_output=True, text=True)
        result = original_run(*args, **kwargs)
        if implicit:
            print(result.stdout or '', end='')
            print(result.stderr or '', end='', file=sys.stderr)
        return result
    try:
        subprocess.run = captured_run
        sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else '')
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = f.main(['--journal', str(journal), *args])
            except SystemExit as exc:
                # argparse rejects invalid option values by exiting; report it as
                # an ordinary validation error instead of losing the receipt.
                code = exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.stdin = previous
        subprocess.run = original_run
    return {'code': code, 'stdout': out.getvalue(), 'stderr': err.getvalue()}


def fingerprint(journal):
    digest = hashlib.sha256()
    for name, snapshot in sorted(f._snapshot_active_journal_sources(journal).items()):
        digest.update(name.encode())
        digest.update(snapshot.text.encode())
    for name in RULE_FILES.values():
        p = journal.parent / name
        if p.is_symlink():
            raise ValueError('Symlink rules refused')
        digest.update(name.encode())
        digest.update(p.read_bytes() if p.exists() else b'absent')
    head = subprocess.run(['git', '-C', str(journal.parent), 'rev-parse', 'HEAD'], capture_output=True, check=True).stdout
    digest.update(head)
    return digest.hexdigest()


def command_args(operation, data):
    command = ALIASES.get(operation, operation)
    # Use canonical parser names, not argparse's permissive prefix matching.
    parser = f.build_parser()
    sub = next(a for a in parser._actions if hasattr(a, 'choices') and isinstance(a.choices, dict)).choices[command]
    options = {a.dest: a for a in sub._actions if a.option_strings}
    options.pop('help', None)
    options.pop('output', None)
    options.pop('confirm', None)
    if operation == 'backup':
        options.pop('remote', None)
    strict_object(data, options)
    args = [command]
    for key, value in data.items():
        action = options[key]
        option = next(o for o in action.option_strings if o.startswith('--'))
        if action.nargs == 0:
            if type(value) is not bool:
                raise ValueError('Flag must be boolean')
            if value:
                args.append(option)
        else:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str) or len(item) > 20000 or any(ord(c) < 32 for c in item):
                    raise ValueError('Option must be a bounded string')
                if key == 'where' and item.startswith('-'):
                    raise ValueError('Option injection in query refused')
                args.append(option + '=' + item)
    return args


def validated_rules(data):
    strict_object(data, {'kind', 'value'})
    kind, value = data.get('kind'), data.get('value')
    if kind not in RULE_FILES:
        raise ValueError('Unknown rules kind')
    if kind == 'payment':
        f._validate_payment_account_rules(value, 'Payment rules')
    else:
        strict_object(value, {'rules'})
        rules = value.get('rules')
        if not isinstance(rules, list) or len(rules) > 1000:
            raise ValueError('Rules must be a bounded list')
        for rule in rules:
            strict_object(rule, {'pattern', 'account'})
            pattern = rule.get('pattern')
            if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 200 or any(ord(c) < 32 for c in pattern):
                raise ValueError('Invalid literal pattern')
            account = f._validate_account(rule.get('account'))
            if not account.startswith('expenses:'):
                raise ValueError('Classification account must be expenses')
    return kind, value


def historical_trial(journal, data):
    strict_object(data, {'payload', 'controls'})
    payload, controls = data.get('payload'), data.get('controls')
    strict_object(controls, {'count', 'totals'}, 'controls (required: independently verified source count and kind:currency totals)')
    strict_object(payload, {'defaults', 'transactions'}, 'payload')
    records = payload.get('transactions')
    if type(controls.get('count')) is not int:
        raise ValueError('controls.count must be a JSON integer (the independently verified source row count)')
    if not isinstance(records, list) or not records or controls['count'] != len(records):
        raise ValueError('Historical source count mismatch')
    totals = {}
    defaults = payload.get('defaults', {})
    if not isinstance(defaults, dict):
        raise ValueError('Invalid defaults')
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid historical record')
        row = {**defaults, **record}
        tags = [*defaults.get('tags', []), *record.get('tags', [])]
        if 'source:historical-import' not in tags and 'source:structured-csv' not in tags:
            raise ValueError('Historical import source required')
        if row.get('installments', 1) != 1 or Decimal(str(row.get('fee', '0'))) != 0:
            raise ValueError('Historical controls require unsplit rows without generated fees')
        key = str(row.get('kind')) + ':' + str(row.get('currency'))
        totals[key] = totals.get(key, Decimal(0)) + Decimal(str(row.get('amount')))
    supplied = controls.get('totals')
    if not isinstance(supplied, dict) or set(supplied) != set(totals) or any(Decimal(str(supplied[k])) != v for k, v in totals.items()):
        raise ValueError('Historical source control-total mismatch')
    # Flatten source snapshots into a new private journal; no includes may point
    # back at the live book during trial ingestion/audit.
    text = '\n'.join(s.text for s in f._snapshot_active_journal_sources(journal).values())
    text = re.sub(r'^\s*include\s+.*$', '', text, flags=re.M)
    with tempfile.TemporaryDirectory(prefix='hfin-migration-trial-') as folder:
        trial = Path(folder) / 'main.journal'
        result = invoke(trial, ['init'])
        if result['code']:
            raise ValueError('Trial initialization failed')
        trial.write_text(text)
        f._git_commit(trial.parent, 'Migration trial baseline', paths=[trial])
        result = invoke(trial, ['ingest-json', '-'], payload)
        if result['code']:
            raise ValueError('Trial ingestion failed: ' + result['stderr'])
        result = invoke(trial, ['audit', '--strict'])
        if result['code']:
            raise ValueError('Trial strict audit failed: ' + result['stdout'] + result['stderr'])
        expected_text, _, _, _ = f.build_ingest_transactions(payload, existing_text='')
        expected_path = trial.parent / 'expected.journal'
        expected_path.write_text(expected_text)
        expected = f._load_journal_transactions(expected_path)
        actual = f._load_journal_transactions(trial)
        def identity(transaction):
            ids = [v for k, v in transaction.get('ttags', []) if k == 'import-id']
            if len(ids) != 1:
                raise ValueError('Historical row requires exactly one stable ID')
            return ids[0]
        def semantics(transaction):
            postings = []
            for posting in transaction.get('tpostings', []):
                values = [(a.get('acommodity'), f._amount_decimal(a), json.dumps(a.get('aprice'), sort_keys=True)) for a in posting.get('pamount', [])]
                postings.append((posting.get('paccount'), values))
            return transaction.get('tdate'), transaction.get('tdescription'), postings, sorted(tuple(t) for t in transaction.get('ttags', []))
        actual_by_id = {}
        for transaction in actual:
            for key, value in transaction.get('ttags', []):
                if key == 'import-id':
                    actual_by_id.setdefault(value, []).append(transaction)
        for row in expected:
            matches = actual_by_id.get(identity(row), [])
            if len(matches) != 1 or semantics(matches[0]) != semantics(row):
                raise ValueError('Historical stored row mismatch for stable source ID ' + identity(row))
    return payload


def rule_action(journal, data, mode):
    kind, value = validated_rules(data)
    path = journal.parent / RULE_FILES[kind]
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    result = {'code': 0, 'stdout': text, 'stderr': 'Rules replacement preview; nothing written.'}
    if mode == 'execute':
        f._reject_staged_changes(journal.parent)
        dirty = subprocess.run(['git', '-C', str(journal.parent), 'diff', '--quiet', '--', path.name], check=False)
        if dirty.returncode:
            raise ValueError('Uncommitted rule changes refused')
        existed = path.exists()
        previous = path.read_text() if existed else ''
        try:
            f._atomic_replace_text(path, text)
            f._git_commit(journal.parent, 'Update finance rules: ' + kind, paths=[path])
        except Exception:
            if existed:
                f._atomic_replace_text(path, previous)
            else:
                path.unlink(missing_ok=True)
            subprocess.run(['git', '-C', str(journal.parent), 'reset', '--quiet', 'HEAD', '--', path.name], capture_output=True)
            raise
        result['stderr'] = 'Rules saved and committed; historical entries unchanged.'
    return result


def undo_action(journal, mode):
    latest = subprocess.run(['git', '-C', str(journal.parent), 'log', '-1', '--format=%H%x00%s', '--', journal.name, *RULE_FILES.values()], capture_output=True, text=True, check=True).stdout.strip()
    commit, subject = latest.split('\x00', 1)
    if subject == 'Initialize hledger journal':
        raise ValueError('Refusing to undo initialization')
    if mode == 'preview':
        return {'code': 0, 'stdout': latest.replace('\x00', ' '), 'stderr': 'Undo preview; nothing written.'}
    if not subject.startswith('Update finance rules:'):
        return invoke(journal, ['undo'])
    f._reject_staged_changes(journal.parent)
    f._reject_dirty_journal(journal)
    if subprocess.run(['git', '-C', str(journal.parent), 'diff', '--quiet', '--', *RULE_FILES.values()]).returncode:
        raise ValueError('Uncommitted rule changes refused')
    reverted = subprocess.run(['git', '-C', str(journal.parent), 'revert', '--no-commit', commit], capture_output=True, text=True)
    if reverted.returncode:
        subprocess.run(['git', '-C', str(journal.parent), 'revert', '--abort'], capture_output=True)
        raise ValueError('Could not undo rule action')
    subprocess.run(['git', '-C', str(journal.parent), 'commit', '-m', 'Undo finance action: ' + subject], capture_output=True, check=True)
    return {'code': 0, 'stdout': 'Undid: ' + subject, 'stderr': ''}


def visualize(journal, data):
    args = command_args('visualize', data)
    q = dict(data)
    q.pop('currency', None)
    q.pop('title', None)
    q.update(report='transactions', format='json')
    result = invoke(journal, command_args('query', q))
    if result['code']:
        return result
    transactions = json.loads(result['stdout'])
    currencies = [data['currency']] if data.get('currency') else sorted(f.compute_stats(transactions)['currencies'])
    if not currencies:
        raise ValueError('No income/expense data to visualize')
    directory = journal.parent / 'reports'
    if directory.is_symlink():
        raise ValueError('Symlink report directory refused')
    directory.mkdir(exist_ok=True)
    images = []
    for currency in currencies:
        # Commodity never becomes a filename; each report gets an unpredictable ID.
        path = directory / ('finance-' + uuid.uuid4().hex + '.png')
        chart = f.build_visualization_data(transactions, currency=currency)
        f.render_dashboard_png(chart, path, title=data.get('title', '財務分析'), period_label=data.get('period', '篩選期間'))
        if path.stat().st_size == 0 or path.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Invalid rendered PNG')
        images.append(str(path))
    return {'code': 0, 'stdout': '\n'.join('[[image: ' + p + ']]' for p in images), 'stderr': '', 'images': images}


def amend_action(journal, data, mode, delete_token):
    """Replace exactly one transaction: delete it and ingest the correction
    under a single authorization; a failed ingest restores the deletion."""
    strict_object(data, {'filter', 'defaults', 'transactions'})
    selector = data.get('filter')
    strict_object(selector, FILTERS, 'filter')
    if not selector:
        raise ValueError('amend needs a filter matching exactly one existing transaction')
    payload = {k: v for k, v in data.items() if k in {'defaults', 'transactions'}}
    if not isinstance(payload.get('transactions'), list) or not payload['transactions']:
        raise ValueError('amend needs the complete corrected replacement transaction(s)')
    delete_args = command_args('delete', selector)
    if mode == 'preview':
        found = invoke(journal, delete_args)
        if found['code'] != 3:
            return found
        count = re.search(r'Delete candidates: (\d+)', found['stdout'])
        if not count or count.group(1) != '1':
            raise ValueError(f"amend filter must match exactly one transaction (matched {count.group(1) if count else '?'}); narrow it")
        token = re.search(r'Confirmation token: ([a-f0-9]{16})', found['stdout'])
        if not token:
            raise ValueError('Missing backend deletion token')
        added = invoke(journal, ['ingest-json', '-', '--preview'], payload)
        if added['code']:
            return added
        if re.search(r'Records ready: 0\b', added['stdout'] + added['stderr']):
            raise ValueError('amend replacement would not be written (duplicate of an existing entry)')
        return {'code': 0, 'stdout': 'REMOVE:\n' + found['stdout'] + '\nADD:\n' + added['stdout'],
                'stderr': added['stderr'], 'delete_token': token.group(1)}
    if not delete_token or not re.fullmatch(r'[a-f0-9]{16}', delete_token):
        raise ValueError('Host deletion token required')
    removed = invoke(journal, delete_args + ['--confirm', delete_token])
    if removed['code']:
        return removed
    added = invoke(journal, ['ingest-json', '-'], payload)
    if added['code'] == 0 and re.search(r'Records ready: 0\b', added['stdout'] + added['stderr']):
        added = {**added, 'code': 1, 'stderr': added['stderr'] + '\nReplacement was skipped as a duplicate.'}
    if added['code']:
        restored = undo_action(journal, 'execute')
        return {'code': added['code'] or 1, 'stdout': added['stdout'],
                'stderr': added['stderr'] + '\nReplacement failed; deletion undone: ' + restored.get('stdout', '') + restored.get('stderr', '')}
    return {'code': 0, 'stdout': 'REMOVED:\n' + removed['stdout'] + '\nADDED:\n' + added['stdout'], 'stderr': added['stderr']}


def run(journal, request, *, mode='preview', expected_snapshot=None, delete_token=None):
    strict_object(request, {'operation', 'data'})
    operation, data = request.get('operation'), request.get('data')
    if operation not in READONLY | MUTATIONS or mode not in {'preview', 'execute'} or not isinstance(data, dict):
        raise ValueError('Unknown finance operation/mode/data')
    journal = Path(journal).absolute()
    f._ensure_journal(journal)
    f._require_regular_journal(journal)
    acquire = f._acquire_journal_lock
    with acquire(journal):
        before = fingerprint(journal)
        if operation in MUTATIONS and mode == 'execute' and (not expected_snapshot or expected_snapshot != before):
            raise ValueError('Stale or absent authorization snapshot; prepare again')
        # This private process owns the existing per-journal lock. finance.py's
        # command functions can reuse it without a nested flock deadlock.
        f._acquire_journal_lock = lambda _: nullcontext()
        try:
            if operation in {'ingest', 'historical'}:
                payload = historical_trial(journal, data) if operation == 'historical' else data
                args = ['ingest-json', '-'] + (['--preview'] if mode == 'preview' else [])
                result = invoke(journal, args, payload)
                if operation == 'historical':
                    result['stderr'] += '\nIsolated trial and strict audit passed; source count/totals reconciled to proposed records.'
            elif operation == 'amend':
                result = amend_action(journal, data, mode, delete_token)
            elif operation == 'settings':
                strict_object(data, set())
                values = {}
                for kind, name in RULE_FILES.items():
                    path = journal.parent / name
                    value = json.loads(path.read_text()) if path.exists() else ({'rules': []} if kind == 'classification' else {'currency_accounts': {}, 'require_match': False})
                    if kind == 'classification' and isinstance(value, list):
                        value = {'rules': value}
                    validated_rules({'kind': kind, 'value': value})
                    values[kind] = value
                result = {'code': 0, 'stdout': json.dumps(values, ensure_ascii=False), 'stderr': ''}
            elif operation == 'rules':
                result = rule_action(journal, data, mode)
            elif operation == 'undo':
                strict_object(data, set())
                result = undo_action(journal, mode)
            elif operation == 'visualize':
                result = visualize(journal, data)
            elif operation == 'backup':
                strict_object(data, {'remote'})
                remote = data.get('remote')
                if not isinstance(remote, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*:[^\x00-\x1f]*', remote):
                    raise ValueError('Explicit valid backup remote required')
                if mode == 'preview':
                    result = {'code':0, 'stdout':'Copy entire book (including Git history/rules) to: ' + remote, 'stderr':'No network request in preview.'}
                else:
                    copied = subprocess.run(['rclone', 'copy', str(journal.parent), remote, '--create-empty-src-dirs'], capture_output=True, text=True)
                    result = {'code':copied.returncode, 'stdout':copied.stdout, 'stderr':copied.stderr}
            elif operation == 'native':
                strict_object(data, {'report', 'options', 'terms'})
                report = data.get('report')
                options = data.get('options', {})
                terms = data.get('terms', [])
                if report not in NATIVE_REPORTS:
                    raise ValueError('Native mutation refused')
                strict_object(options, NATIVE_FLAGS)
                args = ['hledger', '-f', str(journal), report]
                for key, value in options.items():
                    if type(value) is bool:
                        if value:
                            args.append('--' + key)
                    elif isinstance(value, str) and not any(ord(c) < 32 for c in value):
                        args.append('--' + key + '=' + value)
                    else:
                        raise ValueError('Invalid native option')
                if not isinstance(terms, list) or any(not isinstance(t, str) or t.startswith('-') or any(ord(c)<32 for c in t) for t in terms):
                    raise ValueError('Invalid native query terms')
                result = subprocess.run(args + terms, capture_output=True, text=True)
                result = {'code':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr}
            else:
                args = command_args(operation, data)
                if operation in {'installment', 'loan'}:
                    if data.get('preview') is not None:
                        raise ValueError('Preview is host-owned')
                    if mode == 'preview':
                        args.append('--preview')
                if operation == 'delete' and mode == 'execute':
                    if not delete_token or not re.fullmatch(r'[a-f0-9]{16}', delete_token):
                        raise ValueError('Host deletion token required')
                    args += ['--confirm', delete_token]
                result = invoke(journal, args)
                if operation == 'delete' and result['code'] == 3:
                    match = re.search(r'Confirmation token: ([a-f0-9]{16})', result['stdout'])
                    if not match:
                        raise ValueError('Missing backend deletion token')
                    result['delete_token'] = match.group(1)
            result['snapshot'] = before
            return result
        finally:
            f._acquire_journal_lock = acquire


def main():
    if len(sys.argv) != 2:
        raise ValueError('Fixed journal argument required')
    envelope = f.parse_ingest_json(sys.stdin.read())
    strict_object(envelope, {'request', 'mode', 'expected_snapshot', 'delete_token'})
    result = run(Path(sys.argv[1]), **envelope)
    print('HF_RESPONSE:' + json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('HF_RESPONSE:' + json.dumps({'code': 1, 'stdout':'', 'stderr': str(error)}, ensure_ascii=False))
        sys.exit(1)
