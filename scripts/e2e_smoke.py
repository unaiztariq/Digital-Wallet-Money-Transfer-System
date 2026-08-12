#!/usr/bin/env python
"""End-to-end smoke test for the wallet app.

Plain standalone script (NOT a management command). Walks the full user story
through the real HTTP stack — ``django.test.Client`` runs the complete
middleware chain (sessions, CSRF, the idempotency middleware, DRF auth + the
custom exception handler) exactly as a browser would.

It uses the configured database (.env -> MySQL by default). Each run create
throwaway ``smoke_*`` users so repeated runs do not collide.

Usage (from the project root):
    .\\.venv\\Scripts\\python.exe scripts\\e2e_smoke.py

Exit code 0 = every check passed, 1 = at least one check failed.
"""

import json
import os
import sys
import uuid
from decimal import Decimal

# Make the project root importable when run as `python scripts/e2e_smoke.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402
django.setup()

from django.test import Client  # noqa: E402

from wallet.models import Transaction, User, Wallet, WalletTransaction  # noqa: E402

RESULTS = []


def check(name, ok, extra=''):
    """Record and print a single assertion."""
    RESULTS.append((name, bool(ok)))
    print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({extra})' if extra else ''))
    return bool(ok)


def csrf_token(client):
    """Return the csrftoken cookie value, priming it with a GET if needed.

    The cookie is only emitted when a template actually renders {% csrf_token %},
    so a logged-in client must hit a page that renders one (/dashboard/), while
    an anonymous client uses the login page.
    """
    if 'csrftoken' not in client.cookies:
        if client.cookies.get('sessionid'):
            client.get('/dashboard/')
        else:
            client.get('/accounts/login/')
    return client.cookies['csrftoken'].value


def api_post(client, url, payload, key=None):
    headers = {'HTTP_X_CSRFTOKEN': csrf_token(client)}
    if key:
        headers['HTTP_IDEMPOTENCY_KEY'] = key
    return client.post(url, data=json.dumps(payload), content_type='application/json', **headers)


def api_get(client, url):
    return client.get(url)


def get_body(response):
    try:
        return response.json()
    except Exception:
        return {}


def fmt(amount):
    """Stable 4-dp string form of a Decimal for API payloads and compares."""
    return f'{Decimal(amount):.4f}'


S = {}


def ikey(name):
    """Per-run idempotency key. IdempotencyRecord rows persist in the real
    DB across runs, so fixed keys would replay a previous run's stored
    response (same payload) or 400 on a different payload."""
    return f'{S["key_prefix"]}-{name}'


def step1_register_users_and_wallets():
    """Register customer A and B, log A in, create USD wallets for both."""
    suffix = uuid.uuid4().hex[:8]
    S['key_prefix'] = uuid.uuid4().hex[:8]
    password = 'SmokePass-12345'
    S['username_a'] = f'smoke_a_{suffix}'
    S['username_b'] = f'smoke_b_{suffix}'
    S['password'] = password

    r = api_post(S['anon'], '/api/auth/register/',
                 {'username': S['username_a'], 'email': f'{S["username_a"]}@example.com', 'password': password})
    ok = r.status_code == 201 and get_body(r).get('role') == 'CUSTOMER'
    check('register customer A via /api/auth/register/ -> 201, role CUSTOMER', ok,
          f'status={r.status_code} body={get_body(r)}')
    if not ok:
        return

    r = api_post(S['anon'], '/api/auth/register/',
                 {'username': S['username_b'], 'email': f'{S["username_b"]}@example.com', 'password': password})
    check('register customer B via /api/auth/register/ -> 201', r.status_code == 201,
          f'status={r.status_code}')

    check('login A via session (Client.login)', S['client_a'].login(username=S['username_a'], password=password))
    check('login B via session (Client.login)', S['client_b'].login(username=S['username_b'], password=password))

    # Registration must auto-create the default (PKR) wallet for both users.
    r = api_get(S['client_a'], '/api/wallets/')
    auto_pkr = [w for w in get_body(r) if w.get('currency') == 'PKR']
    check('register auto-creates default PKR wallet', r.status_code == 200 and auto_pkr,
          f'status={r.status_code} wallets={get_body(r)}')

    r = api_get(S['client_b'], '/api/wallets/')
    auto_pkr_b = [w for w in get_body(r) if w.get('currency') == 'PKR']
    check('register auto-creates default PKR wallet for B', r.status_code == 200 and auto_pkr_b,
          f'status={r.status_code} wallets={get_body(r)}')

    r = api_post(S['client_a'], '/api/wallets/', {'currency': 'USD'})
    S['wallet_a'] = get_body(r).get('id')
    check('create USD wallet for A -> 201', r.status_code == 201 and S['wallet_a'], f'status={r.status_code}')

    r = api_post(S['client_b'], '/api/wallets/', {'currency': 'USD'})
    S['wallet_b'] = get_body(r).get('id')
    check('create USD wallet for B -> 201', r.status_code == 201 and S['wallet_b'], f'status={r.status_code}')

    # Duplicate-currency wallet for A must be a 400 (BusinessRuleError), not a 500.
    r = api_post(S['client_a'], '/api/wallets/', {'currency': 'USD'})
    check('duplicate USD wallet for A -> 400 (not 500)', r.status_code == 400,
          f'status={r.status_code} body={get_body(r)}')

    S['bal_a'] = Decimal('0.0000')
    S['bal_b'] = Decimal('0.0000')
    S['ref_3000'] = None
    S['txn_3000_id'] = None


def step2_deposit_idempotency():
    """Deposit 5000 with Idempotency-Key k1: 201, replay (no double credit), different payload -> 400."""
    url = f'/api/wallets/{S["wallet_a"]}/deposit/'
    payload = json.dumps({'amount': '5000.0000', 'description': 'smoke deposit'})
    headers = {'HTTP_X_CSRFTOKEN': csrf_token(S['client_a']), 'HTTP_IDEMPOTENCY_KEY': ikey('k1')}

    r1 = S['client_a'].post(url, data=payload, content_type='application/json', **headers)
    r2 = S['client_a'].post(url, data=payload, content_type='application/json', **headers)
    check('deposit 5000 (key k1) -> 201', r1.status_code == 201, f'status={r1.status_code} body={get_body(r1)}')
    check('replay same key+payload -> 201 with identical stored body',
          r2.status_code == 201 and get_body(r1) == get_body(r2),
          f'status={r2.status_code}')
    check('deposit memo stored', get_body(r1).get('description') == 'smoke deposit',
          f'description={get_body(r1).get("description")!r}')
    S['bal_a'] += Decimal('5000.0000')

    r = api_get(S['client_a'], f'/api/wallets/{S["wallet_a"]}/')
    live_balance = Decimal(get_body(r).get('balance', '0'))
    check('balance NOT doubled by replay (wallet shows 5000.0000)', r.status_code == 200 and live_balance == S['bal_a'],
          f'balance={live_balance}')

    r = S['client_a'].post(url, data=json.dumps({'amount': '999.0000'}), content_type='application/json', **headers)
    body = get_body(r)
    check('different payload with same key -> 400', r.status_code == 400 and body.get('code') == 400,
          f'status={r.status_code} body={body}')


def step3_withdraw():
    """Withdraw 1000 (balance 4000); withdraw above balance -> 400 InsufficientBalance."""
    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/withdraw/',
                 {'amount': '1000.0000', 'description': 'smoke withdraw'}, key=ikey('k2'))
    ok = r.status_code == 201
    if ok:
        S['bal_a'] -= Decimal('1000.0000')
    check('withdraw 1000 -> 201', ok, f'status={r.status_code} body={get_body(r)}')
    check('withdraw memo stored', get_body(r).get('description') == 'smoke withdraw',
          f'description={get_body(r).get("description")!r}')

    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/withdraw/', {'amount': '5000.0000'}, key=ikey('k3'))
    body = get_body(r)
    check('withdraw above balance -> 400 InsufficientBalance', r.status_code == 400 and body.get('code') == 400,
          f'status={r.status_code} body={body}')


def step4_transfer():
    """Transfer 3000 A->B; check balances + immutable ledger; self-transfer and zero-amount -> 400."""
    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                 {'recipient_wallet_id': S['wallet_b'], 'amount': '3000.0000', 'description': 'smoke transfer'}, key=ikey('t1'))
    ok = r.status_code == 201
    if ok:
        S['bal_a'] -= Decimal('3000.0000')
        S['bal_b'] += Decimal('3000.0000')
        S['txn_3000_id'] = get_body(r).get('id')
        S['ref_3000'] = get_body(r).get('reference')
    check('transfer 3000 A->B -> 201', ok, f'status={r.status_code} body={get_body(r)}')
    if not ok:
        return
    check('transfer memo stored', get_body(r).get('description') == 'smoke transfer',
          f'description={get_body(r).get("description")!r}')

    wa = Wallet.objects.get(pk=S['wallet_a'])
    wb = Wallet.objects.get(pk=S['wallet_b'])
    check('balances A=1000, B=3000', wa.balance == S['bal_a'] and wb.balance == S['bal_b'],
          f'A={wa.balance} B={wb.balance}')

    entries = list(WalletTransaction.objects.filter(transaction_id=S['txn_3000_id']).order_by('entry_type'))
    debit = [e for e in entries if e.entry_type == WalletTransaction.EntryType.DEBIT]
    credit = [e for e in entries if e.entry_type == WalletTransaction.EntryType.CREDIT]
    check('ledger has DEBIT + CREDIT rows with balance_after',
          len(entries) == 2 and debit and credit and debit[0].balance_after == S['bal_a']
          and credit[0].balance_after == S['bal_b'] and str(debit[0].wallet_id) == S['wallet_a']
          and str(credit[0].wallet_id) == S['wallet_b'],
          f'entries={[(e.entry_type, str(e.amount), str(e.balance_after)) for e in entries]}')

    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                 {'recipient_wallet_id': S['wallet_a'], 'amount': '10.0000'}, key=ikey('t2'))
    body = get_body(r)
    check('transfer to self -> 400 SelfTransfer', r.status_code == 400 and body.get('code') == 400,
          f'status={r.status_code} body={body}')

    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                 {'recipient_wallet_id': S['wallet_b'], 'amount': '0.0000'}, key=ikey('t3'))
    body = get_body(r)
    check('transfer amount 0 -> 400 InvalidAmount', r.status_code == 400 and body.get('code') == 400,
          f'status={r.status_code} body={body}')


def step5_daily_limits():
    """max_single_transfer 100000 rejected at 100001; cumulative daily limit rejects once > 500000."""
    # Fund A to 601000 (six max deposits of 100000 each).
    deposits_ok = True
    for i in range(6):
        r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/deposit/',
                     {'amount': '100000.0000'}, key=ikey(f'lim-dep-{i}'))
        if r.status_code != 201:
            deposits_ok = False
            check(f'deposit 100000 (funding #{i + 1}) -> 201', False, f'status={r.status_code} body={get_body(r)}')
        else:
            S['bal_a'] += Decimal('100000.0000')
    check('fund A with 6 x 100000 deposits (max_single_deposit=100000)', deposits_ok,
          f'balance={S["bal_a"]}')

    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                 {'recipient_wallet_id': S['wallet_b'], 'amount': '100001.0000'}, key=ikey('lim-single'))
    body = get_body(r)
    check('transfer 100001 -> 400 LimitExceeded (max_single_transfer=100000)',
          r.status_code == 400 and body.get('code') == 400, f'status={r.status_code} body={body}')

    # Cumulative: prior TRANSFER debits today = 3000. Five 100000 transfers push
    # cumulative to 403000 (<=500000 ok) and the 5th would hit 503000 -> rejected.
    successes = 0
    rejected = False
    for i in range(5):
        r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                     {'recipient_wallet_id': S['wallet_b'], 'amount': '100000.0000'}, key=ikey(f'lim-{i}'))
        if r.status_code == 201:
            successes += 1
            S['bal_a'] -= Decimal('100000.0000')
            S['bal_b'] += Decimal('100000.0000')
        else:
            body = get_body(r)
            rejected = (r.status_code == 400 and body.get('code') == 400)
    check('cumulative daily transfer limit: 4 x 100000 succeed, 5th -> 400 LimitExceeded',
          successes == 4 and rejected, f'successes={successes} rejected={rejected} bal_a={S["bal_a"]}')


def step6_api_filters():
    """GET /api/transactions/ with filters + detail endpoint."""
    r = api_get(S['client_a'], '/api/transactions/?type=TRANSFER')
    body = get_body(r)
    check('GET /api/transactions/?type=TRANSFER -> count 5', r.status_code == 200 and body.get('count') == 5,
          f'status={r.status_code} count={body.get("count")}')

    r = api_get(S['client_a'], f'/api/transactions/?type=TRANSFER&reference={S["ref_3000"]}')
    body = get_body(r)
    check('reference search -> count 1', r.status_code == 200 and body.get('count') == 1,
          f'status={r.status_code} count={body.get("count")}')

    r = api_get(S['client_a'], '/api/transactions/?type=TRANSFER&amount_min=5000')
    body = get_body(r)
    check('amount_min=5000 & type=TRANSFER -> count 4', r.status_code == 200 and body.get('count') == 4,
          f'status={r.status_code} count={body.get("count")}')

    r = api_get(S['client_a'], '/api/transactions/?direction=outgoing')
    body = get_body(r)
    check('direction=outgoing -> 6 (5 transfers + 1 withdrawal)', r.status_code == 200 and body.get('count') == 6,
          f'status={r.status_code} count={body.get("count")}')

    r = api_get(S['client_a'], '/api/transactions/?direction=incoming')
    body = get_body(r)
    check('direction=incoming -> 7 (7 deposits)', r.status_code == 200 and body.get('count') == 7,
          f'status={r.status_code} count={body.get("count")}')

    r = api_get(S['client_a'], f'/api/transactions/{S["txn_3000_id"]}/')
    body = get_body(r)
    check('GET /api/transactions/{id}/ -> 200 detail', r.status_code == 200 and body.get('type') == 'TRANSFER',
          f'status={r.status_code} body={body}')


def step7_admin():
    """Freeze/unfreeze B's wallet, adjust balance, role guard on admin endpoints."""
    S['admin_username'] = f'smoke_admin_{uuid.uuid4().hex[:8]}'
    User.objects.create_user(username=S['admin_username'], email=f'{S["admin_username"]}@example.com',
                             password=S['password'], role=User.Role.ADMIN)
    S['client_admin'] = Client()
    check('admin login', S['client_admin'].login(username=S['admin_username'], password=S['password']))

    r = api_post(S['client_admin'], f'/api/admin/wallets/{S["wallet_b"]}/freeze/', {})
    check('admin freeze B wallet -> 200', r.status_code == 200, f'status={r.status_code} body={get_body(r)}')

    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                 {'recipient_wallet_id': S['wallet_b'], 'amount': '500.0000'}, key=ikey('ad-1'))
    body = get_body(r)
    check('transfer to frozen wallet -> 403 WalletFrozen', r.status_code == 403 and body.get('code') == 403,
          f'status={r.status_code} body={body}')

    r = api_post(S['client_admin'], f'/api/admin/wallets/{S["wallet_b"]}/unfreeze/', {})
    check('admin unfreeze B wallet -> 200', r.status_code == 200, f'status={r.status_code} body={get_body(r)}')

    r = api_post(S['client_a'], f'/api/wallets/{S["wallet_a"]}/transfer/',
                 {'recipient_wallet_id': S['wallet_b'], 'amount': '500.0000'}, key=ikey('ad-2'))
    ok = r.status_code == 201
    if ok:
        S['bal_a'] -= Decimal('500.0000')
        S['bal_b'] += Decimal('500.0000')
    check('transfer after unfreeze -> 201', ok, f'status={r.status_code} body={get_body(r)}')

    r = api_post(S['client_admin'], f'/api/admin/wallets/{S["wallet_b"]}/adjust-balance/',
                 {'amount': '500.0000', 'reason': 'smoke test adjustment'})
    body = get_body(r)
    ok = r.status_code == 201 and body.get('type') == 'ADJUSTMENT'
    if ok:
        S['bal_b'] += Decimal('500.0000')
    check('adjust-balance +500 with reason -> 201 ADJUSTMENT txn', ok,
          f'status={r.status_code} body={body}')

    entries = list(WalletTransaction.objects.filter(transaction_id=body.get('id')))
    check('adjustment wrote a ledger row', len(entries) == 1 and entries[0].balance_after == S['bal_b'],
          f'entries={[(e.entry_type, str(e.balance_after)) for e in entries]}')

    r = api_get(S['client_a'], '/api/admin/wallets/')
    check('non-admin hits admin endpoint -> 403', r.status_code == 403, f'status={r.status_code}')


def step8_pages():
    """Server-rendered pages: status codes, admin guard, anonymous redirects."""
    r = S['client_a'].get('/dashboard/')
    check('GET /dashboard/ -> 200 for logged-in A', r.status_code == 200, f'status={r.status_code}')

    # The stat card defaults to A's first wallet currency (PKR, balance 0.00);
    # request the USD currency explicitly to assert the USD total renders.
    r = S['client_a'].get('/dashboard/?currency=USD')
    check('GET /dashboard/?currency=USD contains USD total balance', f'{S["bal_a"]:.2f}' in r.content.decode(),
          f'expected balance {S["bal_a"]}')

    r = S['client_a'].get('/transfer/')
    check('GET /transfer/ -> 200', r.status_code == 200, f'status={r.status_code}')

    r = S['client_a'].get('/transactions/?type=TRANSFER&amount_min=1000')
    check('GET /transactions/ with filter query params -> 200', r.status_code == 200, f'status={r.status_code}')

    r = S['client_admin'].get('/admin-console/')
    check('GET /admin-console/ -> 200 for admin', r.status_code == 200, f'status={r.status_code}')

    r = S['client_a'].get('/admin-console/')
    check('GET /admin-console/ -> 403 for customer', r.status_code == 403, f'status={r.status_code}')

    anon = Client()
    r = anon.get('/accounts/login/')
    check('GET /accounts/login/ -> 200 anonymous', r.status_code == 200, f'status={r.status_code}')

    r = anon.get('/')
    check('GET / -> 302 redirect to login for anonymous', r.status_code == 302 and '/accounts/login/' in r['Location'],
          f'status={r.status_code} location={r.get("Location")}')


STEPS = [
    ('Step 1: register users + wallets', step1_register_users_and_wallets),
    ('Step 2: deposit idempotency', step2_deposit_idempotency),
    ('Step 3: withdraw', step3_withdraw),
    ('Step 4: transfer', step4_transfer),
    ('Step 5: daily limits', step5_daily_limits),
    ('Step 6: API filters', step6_api_filters),
    ('Step 7: admin', step7_admin),
    ('Step 8: pages', step8_pages),
]


def main():
    print('=' * 70)
    print('Wallet E2E smoke test — running against the configured database')
    print('=' * 70)
    S['anon'] = Client()
    S['client_a'] = Client()
    S['client_b'] = Client()

    for label, fn in STEPS:
        print(f'\n--- {label} ---')
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - keep going, collect failures
            check(f'{label}: unhandled exception', False, repr(exc))

    failed = [name for name, ok in RESULTS if not ok]
    print('\n' + '=' * 70)
    if failed:
        print(f'RESULT: FAILED — {len(failed)}/{len(RESULTS)} checks failed:')
        for name in failed:
            print(f'  - {name}')
        print('=' * 70)
        sys.exit(1)
    print(f'RESULT: ALL PASSED ({len(RESULTS)} checks)')
    print('=' * 70)
    sys.exit(0)


if __name__ == '__main__':
    main()
