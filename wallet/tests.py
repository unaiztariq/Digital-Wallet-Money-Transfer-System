"""
Test suite for the wallet backend (Django 6.0.3 + DRF + simplejwt, MySQL 8.4).

Covers, per the integration spec:
  * Unit/service tests (django.test.TestCase) for transfer, deposit, withdraw,
    admin adjustments, state transitions and service-level idempotency.
  * Concurrency + idempotency tests (django.test.TransactionTestCase +
    threading) asserting only invariants that hold regardless of thread
    interleaving (conservation, never-negative balances, reference
    uniqueness, single-credit idempotency).
  * API smoke tests via rest_framework.test.APIClient + simplejwt tokens.

Money is always Decimal. Wallet/transaction ids are UUIDs.

KNOWN SERVICE ISSUES discovered while writing this suite (documented, not
tested as failures):
  1. (FIXED) admin_wallet_service.adjust_balance() used a raw
     Wallet.objects.select_for_update().get(pk=...) so a missing wallet
     surfaced as Wallet.DoesNotExist instead of the WalletNotFound domain
     error used everywhere else; it now converts to WalletNotFound and
     EdgeCaseTests.test_wallet_not_found asserts that behaviour.
  2. transfer_service.transfer() accepts `description` but never stores it
     (the Transaction model has no description field); caller-supplied
     descriptions are silently dropped.
  3. Service-level _replay() (wallet_service.py) scopes an idempotency key
     only by wallet, not by operation type. Reusing one key for a deposit
     and then a withdraw on the same wallet replays the DEPOSIT transaction
     for the withdraw call, silently skipping the withdrawal. The API
     middleware additionally hashes the payload, so this only bites callers
     who reuse key+payload across different operation types.
"""

import re
import threading
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from wallet.models import Transaction, Wallet, WalletTransaction
from wallet.repositories.errors import (InsufficientBalance, InvalidAmount,
    InvalidStateTransition, LimitExceeded, PermissionDenied, SelfTransfer,
    WalletFrozen, WalletNotFound)
from wallet.services.admin_wallet_service import AdminWalletService
from wallet.services.transaction_service import TransactionService
from wallet.services.wallet_service import WalletService

User = get_user_model()

REFERENCE_RE = re.compile(r'^TXN-\d{8}-\d{6}$')


# ---------------------------------------------------------------------------
# Concurrency helper
# ---------------------------------------------------------------------------

class ThreadRunner:
    """Run one callable per thread, released together on a barrier.

    Thread exceptions are collected and re-raised in the main thread so a
    deadlock/race bug fails the test instead of being silently swallowed.
    """

    def __init__(self, targets):
        self.targets = list(targets)
        self.exceptions = []
        self.results = [None] * len(self.targets)
        self.barrier = threading.Barrier(len(self.targets))

    def _run(self, index):
        try:
            # Each thread needs its own DB connection (autocommit) — the
            # main test connection must not be shared.
            close_old_connections()
            self.barrier.wait(timeout=30)
            self.results[index] = self.targets[index]()
        except Exception as exc:  # noqa: BLE001 - collected and re-raised below
            self.exceptions.append(exc)
        finally:
            close_old_connections()

    def run(self):
        threads = [threading.Thread(target=self._run, args=(i,))
                   for i in range(len(self.targets))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if self.exceptions:
            raise self.exceptions[0]
        return self.results


# ---------------------------------------------------------------------------
# Unit tests — service level (django.test.TestCase)
# ---------------------------------------------------------------------------

class WalletServiceUnitTests(TestCase):
    """Service-level rules for transfers, deposits, withdrawals, admin
    adjustments and idempotency. setUp already creates two ADJUSTMENT
    transactions (and two ledger rows), so counts are always asserted as
    deltas against a baseline."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='customer', email='customer@example.com',
            password='password123', role='CUSTOMER')
        self.other_user = User.objects.create_user(
            username='other', email='other@example.com',
            password='password123', role='CUSTOMER')
        self.admin_user = User.objects.create_user(
            username='admin', email='admin@example.com',
            password='password123', role='ADMIN')
        self.service = WalletService()
        self.admin_service = AdminWalletService()

        self.sender_wallet = self.service.create_wallet(self.user, 'USD')
        self.receiver_wallet = self.service.create_wallet(self.other_user, 'USD')
        # Fund via admin adjustments to stay under deposit single limits.
        self.admin_service.adjust_balance(self.admin_user, self.sender_wallet.id,
                                          Decimal('1000000'), 'Initial funding')
        self.admin_service.adjust_balance(self.admin_user, self.receiver_wallet.id,
                                          Decimal('1000000'), 'Initial funding')

    def _balance(self, wallet_id):
        return Wallet.objects.get(id=wallet_id).balance

    def test_transfer_success_updates_balances_and_creates_ledger(self):
        sender_before = self._balance(self.sender_wallet.id)
        receiver_before = self._balance(self.receiver_wallet.id)
        ledger_before = WalletTransaction.objects.count()
        amount = Decimal('100.50')

        txn = self.service.transfer(self.user, self.sender_wallet.id,
                                    self.receiver_wallet.id, amount, 'Test transfer')

        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(txn.type, Transaction.Type.TRANSFER)
        self.assertEqual(txn.amount, amount)
        self.assertEqual(txn.currency, 'USD')
        # Exactly two ledger rows: DEBIT on sender, CREDIT on receiver.
        self.assertEqual(WalletTransaction.objects.count(), ledger_before + 2)

        debit = WalletTransaction.objects.get(transaction=txn, wallet=self.sender_wallet,
                                              entry_type=WalletTransaction.EntryType.DEBIT)
        credit = WalletTransaction.objects.get(transaction=txn, wallet=self.receiver_wallet,
                                               entry_type=WalletTransaction.EntryType.CREDIT)
        self.assertEqual(debit.amount, amount)
        self.assertEqual(credit.amount, amount)
        self.assertEqual(debit.balance_after, sender_before - amount)
        self.assertEqual(credit.balance_after, receiver_before + amount)
        # Wallet balances updated.
        self.assertEqual(self._balance(self.sender_wallet.id), sender_before - amount)
        self.assertEqual(self._balance(self.receiver_wallet.id), receiver_before + amount)

    def test_transfer_zero_or_negative_amount_rejected(self):
        txn_before = Transaction.objects.count()
        ledger_before = WalletTransaction.objects.count()

        with self.assertRaises(InvalidAmount):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('0'), 'Zero')
        with self.assertRaises(InvalidAmount):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('-100'), 'Negative')

        # No side effects at all.
        self.assertEqual(Transaction.objects.count(), txn_before)
        self.assertEqual(WalletTransaction.objects.count(), ledger_before)

    def test_transfer_insufficient_funds_rejected(self):
        # Dedicated low-balance wallet so the amount (1000) stays under the
        # 100000 single-transfer limit and the balance check is what fires.
        wallet = self.service.create_wallet(self.user, 'EUR')
        self.admin_service.adjust_balance(self.admin_user, wallet.id,
                                          Decimal('100'), 'Small balance')
        txn_before = Transaction.objects.count()
        ledger_before = WalletTransaction.objects.count()

        with self.assertRaises(InsufficientBalance) as cm:
            self.service.transfer(self.user, wallet.id,
                                  self.receiver_wallet.id, Decimal('1000'), 'Overdraw')
        self.assertEqual(cm.exception.code, 400)

        # NO transaction row, NO ledger rows, balances unchanged.
        self.assertEqual(Transaction.objects.count(), txn_before)
        self.assertEqual(WalletTransaction.objects.count(), ledger_before)
        self.assertEqual(self._balance(wallet.id), Decimal('100'))

    def test_transfer_sender_wallet_frozen_rejected(self):
        self.admin_service.freeze_wallet(self.admin_user, self.sender_wallet.id)
        with self.assertRaises(WalletFrozen):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('100'), 'Frozen sender')

    def test_transfer_receiver_wallet_frozen_rejected(self):
        self.admin_service.freeze_wallet(self.admin_user, self.receiver_wallet.id)
        with self.assertRaises(WalletFrozen):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('100'), 'Frozen receiver')

    def test_transfer_self_wallet_rejected(self):
        with self.assertRaises(SelfTransfer):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.sender_wallet.id, Decimal('100'), 'Self')

    def test_transfer_exceeds_max_single_limit_rejected(self):
        # max_single_transfer seed = 100000.00
        with self.assertRaises(LimitExceeded) as cm:
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('100001'), 'Too big')
        self.assertEqual(cm.exception.code, 400)
        self.assertIn('max_single_transfer', str(cm.exception))

    def test_transfer_exceeds_max_daily_limit_rejected(self):
        # max_daily_transfer seed = 500000.00 — pre-transfer 495000, then
        # another 10000 must be rejected (495000 + 10000 > 500000).
        self.admin_service.adjust_balance(self.admin_user, self.sender_wallet.id,
                                          Decimal('600000'), 'Extra funding')
        for _ in range(99):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('5000'), 'Pre-transfer')

        with self.assertRaises(LimitExceeded) as cm:
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('10000'), 'Over daily')
        self.assertEqual(cm.exception.code, 400)
        self.assertIn('max_daily_transfer', str(cm.exception))

    def test_transfer_unowned_sender_wallet_rejected(self):
        # sender_wallet belongs to self.user; passing another user's id must
        # be refused before any locks/rows are touched.
        with self.assertRaises(PermissionDenied):
            self.service.transfer(self.other_user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('100'), 'Not mine')

    def test_deposit_withdraw_happy_path(self):
        wallet = self.service.create_wallet(self.user, 'EUR')

        txn = self.service.deposit(self.user, wallet.id, Decimal('5000'))
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self._balance(wallet.id), Decimal('5000'))

        txn = self.service.withdraw(self.user, wallet.id, Decimal('1000'))
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self._balance(wallet.id), Decimal('4000'))

        # Withdraw above balance.
        with self.assertRaises(InsufficientBalance):
            self.service.withdraw(self.user, wallet.id, Decimal('5000'))

        # max_single_deposit seed = 100000.00
        with self.assertRaises(LimitExceeded):
            self.service.deposit(self.user, wallet.id, Decimal('100001'))

        # max_daily_withdrawal seed = 100000.00: two 50000 withdrawals in one
        # day must exceed it (the first 1000 withdrawal also counts as a
        # DEBIT for today). Each single withdrawal stays within the 50000
        # max_single_withdrawal seed.
        self.service.deposit(self.user, wallet.id, Decimal('60000'))
        self.service.deposit(self.user, wallet.id, Decimal('60000'))
        self.assertEqual(self._balance(wallet.id), Decimal('124000'))

        self.service.withdraw(self.user, wallet.id, Decimal('50000'))
        self.assertEqual(self._balance(wallet.id), Decimal('74000'))

        with self.assertRaises(LimitExceeded) as cm:
            self.service.withdraw(self.user, wallet.id, Decimal('50000'))
        self.assertIn('max_daily_withdrawal', str(cm.exception))
        self.assertEqual(self._balance(wallet.id), Decimal('74000'))

    def test_admin_adjust_balance_creates_auditable_transaction(self):
        wallet = self.service.create_wallet(self.user, 'GBP')

        txn = self.admin_service.adjust_balance(self.admin_user, wallet.id,
                                                Decimal('1000.50'), 'Bonus reward')
        self.assertEqual(txn.type, Transaction.Type.ADJUSTMENT)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(txn.amount, Decimal('1000.50'))
        # Reason is persisted in failure_reason (audit trail).
        self.assertEqual(txn.failure_reason, 'Bonus reward')

        entry = WalletTransaction.objects.get(transaction=txn, wallet=wallet,
                                              entry_type=WalletTransaction.EntryType.CREDIT)
        self.assertEqual(entry.amount, Decimal('1000.50'))
        self.assertEqual(entry.balance_after, Decimal('1000.50'))
        self.assertEqual(self._balance(wallet.id), Decimal('1000.50'))

        # Negative adjustment: the ledger DEBIT carries the sign; the
        # transaction amount is stored as abs(raw) (CHECK amount > 0).
        txn = self.admin_service.adjust_balance(self.admin_user, wallet.id,
                                                Decimal('-500.25'), 'Fee deduction')
        self.assertEqual(txn.amount, Decimal('500.25'))
        entry = WalletTransaction.objects.get(transaction=txn, wallet=wallet,
                                              entry_type=WalletTransaction.EntryType.DEBIT)
        self.assertEqual(entry.amount, Decimal('500.25'))
        self.assertEqual(entry.balance_after, Decimal('500.25'))
        self.assertEqual(self._balance(wallet.id), Decimal('500.25'))

        # Negative adjustment pushing below zero.
        with self.assertRaises(InsufficientBalance):
            self.admin_service.adjust_balance(self.admin_user, wallet.id,
                                              Decimal('-1000'), 'Overdraw')

        # Non-admin actor.
        with self.assertRaises(PermissionDenied):
            self.admin_service.adjust_balance(self.user, wallet.id,
                                              Decimal('100'), 'Not allowed')

    def test_invalid_state_transition_rejected(self):
        wallet = self.service.create_wallet(self.user, 'CHF')
        txn = self.service.deposit(self.user, wallet.id, Decimal('100'))
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

        # confirm/cancel of an already-COMPLETED transaction must be refused.
        with self.assertRaises(InvalidStateTransition):
            TransactionService().confirm(txn)
        with self.assertRaises(InvalidStateTransition):
            TransactionService().cancel(txn)

        # Sanity: a PENDING transaction can be confirmed once, then no more.
        pending = Transaction.objects.create(
            reference='PENDING-TEST-1', type=Transaction.Type.TRANSFER,
            amount=Decimal('100'), currency='USD', status=Transaction.Status.PENDING)
        TransactionService().confirm(pending)
        pending.refresh_from_db()
        self.assertEqual(pending.status, Transaction.Status.COMPLETED)
        with self.assertRaises(InvalidStateTransition):
            TransactionService().cancel(pending)

    def test_service_level_idempotency(self):
        wallet = self.service.create_wallet(self.user, 'JPY')
        key = 'unique-key-123'

        txn1 = self.service.deposit(self.user, wallet.id, Decimal('1000'),
                                    idempotency_key=key)
        self.assertEqual(self._balance(wallet.id), Decimal('1000'))

        # Same key, same wallet → replay of the same transaction, no re-credit.
        txn2 = self.service.deposit(self.user, wallet.id, Decimal('1000'),
                                    idempotency_key=key)
        self.assertEqual(txn1.id, txn2.id)
        self.assertEqual(self._balance(wallet.id), Decimal('1000'))
        self.assertEqual(
            Transaction.objects.filter(idempotency_key=key).count(), 1)


# ---------------------------------------------------------------------------
# Integration tests — concurrency + idempotency (TransactionTestCase + threads)
# ---------------------------------------------------------------------------

class WalletServiceIntegrationTests(TransactionTestCase):
    """Real-concurrency tests. TransactionTestCase (no wrapping transaction)
    so thread writes are committed and visible; assertions are invariants
    (conservation, never-negative, uniqueness) that hold for any
    interleaving."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='customer', email='customer@example.com',
            password='password123', role='CUSTOMER')
        self.other_user = User.objects.create_user(
            username='other', email='other@example.com',
            password='password123', role='CUSTOMER')
        self.admin_user = User.objects.create_user(
            username='admin', email='admin@example.com',
            password='password123', role='ADMIN')
        self.service = WalletService()
        self.admin_service = AdminWalletService()

        self.sender_wallet = self.service.create_wallet(self.user, 'USD')
        self.receiver_wallet = self.service.create_wallet(self.other_user, 'USD')
        self.admin_service.adjust_balance(self.admin_user, self.sender_wallet.id,
                                          Decimal('5000000'), 'Initial funding')
        self.admin_service.adjust_balance(self.admin_user, self.receiver_wallet.id,
                                          Decimal('5000000'), 'Initial funding')

    def _balance(self, wallet_id):
        return Wallet.objects.get(id=wallet_id).balance

    def test_concurrent_bidirectional_transfers_no_deadlock(self):
        """A->B and B->A run at the same time; both must complete cleanly.
        lock_wallets_for_transfer() orders by id, so the two transactions
        take row locks in the same order — no deadlock is expected."""
        sender_before = self._balance(self.sender_wallet.id)
        receiver_before = self._balance(self.receiver_wallet.id)
        transfers_before = Transaction.objects.filter(type=Transaction.Type.TRANSFER).count()

        def a_to_b():
            return self.service.transfer(self.user, self.sender_wallet.id,
                                         self.receiver_wallet.id, Decimal('1000'), 'A->B')

        def b_to_a():
            return self.service.transfer(self.other_user, self.receiver_wallet.id,
                                         self.sender_wallet.id, Decimal('2500'), 'B->A')

        results = ThreadRunner([a_to_b, b_to_a]).run()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(t.status == Transaction.Status.COMPLETED for t in results))

        # Final balances consistent with the total moved: +1500 on A, -1500 on B.
        self.assertEqual(self._balance(self.sender_wallet.id), sender_before + Decimal('1500'))
        self.assertEqual(self._balance(self.receiver_wallet.id), receiver_before - Decimal('1500'))
        # Total conserved.
        self.assertEqual(self._balance(self.sender_wallet.id) + self._balance(self.receiver_wallet.id),
                         sender_before + receiver_before)
        self.assertEqual(
            Transaction.objects.filter(type=Transaction.Type.TRANSFER).count(),
            transfers_before + 2)

    def test_concurrent_transfers_preserve_balance_integrity(self):
        """8 threads x 1000 from a 5000-balance sender. Exactly 5 transfers
        can succeed; the rest must fail with InsufficientBalance inside the
        row lock. Invariants: balance never negative, total conserved,
        debited amount <= initial balance. (Deterministic: every attempt that
        finds balance >= 1000 succeeds and removes exactly 1000, so the 5th
        success always happens before the remaining attempts see balance 0.)"""
        sender_before = Decimal('5000')
        self.admin_service.adjust_balance(self.admin_user, self.sender_wallet.id,
                                          sender_before - self._balance(self.sender_wallet.id),
                                          'Reset balance')
        receiver_before = self._balance(self.receiver_wallet.id)
        amount = Decimal('1000')

        def transfer_1000():
            try:
                self.service.transfer(self.user, self.sender_wallet.id,
                                      self.receiver_wallet.id, amount, 'Concurrent')
            except (InsufficientBalance, LimitExceeded):
                pass  # expected for the attempts beyond the available balance

        ThreadRunner([transfer_1000] * 8).run()

        sender_final = self._balance(self.sender_wallet.id)
        receiver_final = self._balance(self.receiver_wallet.id)
        debited = sender_before - sender_final

        self.assertGreaterEqual(sender_final, Decimal('0'))           # never negative
        self.assertLessEqual(debited, sender_before)                  # debited <= balance
        self.assertEqual(sender_final + receiver_final,
                         sender_before + receiver_before)             # conservation
        # Exactly 5 x 1000 succeeded (see docstring for why this is deterministic).
        self.assertEqual(sender_final, Decimal('0'))
        self.assertEqual(receiver_final, receiver_before + Decimal('5000'))

    def test_idempotent_request_replays_without_double_credit(self):
        """Real deposit endpoint, same Idempotency-Key twice → same
        transaction id and a single credit. The IdempotencyMiddleware stores
        the first 201 response and replays it; the service-level _replay()
        is the second line of defence."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')
        key = 'replay-test-key'
        balance_before = self._balance(self.sender_wallet.id)

        first = client.post(f'/api/wallets/{self.sender_wallet.id}/deposit/',
                            {'amount': '1000.00'}, HTTP_IDEMPOTENCY_KEY=key)
        second = client.post(f'/api/wallets/{self.sender_wallet.id}/deposit/',
                             {'amount': '1000.00'}, HTTP_IDEMPOTENCY_KEY=key)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        # The replayed response is a plain JsonResponse (no .data), so compare
        # via .json() which both DRF Response and JsonResponse expose.
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(first.json()['status'], Transaction.Status.COMPLETED)
        # Credited exactly once.
        self.assertEqual(self._balance(self.sender_wallet.id), balance_before + Decimal('1000'))
        self.assertEqual(Transaction.objects.filter(idempotency_key=key).count(), 1)

    def test_concurrent_identical_idempotency_keys(self):
        """N threads hammer the deposit endpoint with the SAME key: exactly
        one transaction is created and the balance is credited exactly once.
        The middleware's unique (key, payload_hash) record is the gate —
        losers get 409 'in progress' or a 201 replay of the winner."""
        key = 'concurrent-key'
        url = f'/api/wallets/{self.sender_wallet.id}/deposit/'
        balance_before = self._balance(self.sender_wallet.id)
        txns_before = Transaction.objects.count()

        def make_deposit():
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')
            response = client.post(url, {'amount': '500.00'}, HTTP_IDEMPOTENCY_KEY=key)
            # 409/201-replay responses are plain JsonResponses (no .data);
            # .json() works for both DRF Response and JsonResponse.
            return response.status_code, response.json().get('id')

        results = ThreadRunner([make_deposit] * 10).run()

        # Exactly one transaction with this key; credited exactly once.
        self.assertEqual(Transaction.objects.filter(idempotency_key=key).count(), 1)
        self.assertEqual(Transaction.objects.count(), txns_before + 1)
        self.assertEqual(self._balance(self.sender_wallet.id), balance_before + Decimal('500'))

        # Every 201 response (winner or replay) must carry the SAME txn id.
        ids = {txn_id for status, txn_id in results if status == 201}
        self.assertEqual(len(ids), 1)
        # Every response is an acceptable outcome: 201 (win/replay) or 409 (in progress).
        self.assertTrue(all(status in (201, 409) for status, _ in results))
        self.assertTrue(any(status == 201 for status, _ in results))

    def test_reference_sequential_uniqueness_under_concurrency(self):
        """N parallel transfers → N distinct references matching the
        TXN-YYYYMMDD-NNNNNN format. DailyCounter rows are locked via
        select_for_update, so no two threads may read the same counter
        value."""
        num_transfers = 10
        references = []

        def do_transfer():
            txn = self.service.transfer(self.user, self.sender_wallet.id,
                                        self.receiver_wallet.id, Decimal('10'), 'Ref test')
            references.append(txn.reference)

        ThreadRunner([do_transfer] * num_transfers).run()

        self.assertEqual(len(references), num_transfers)
        self.assertEqual(len(set(references)), num_transfers)  # all distinct
        for ref in references:
            self.assertRegex(ref, REFERENCE_RE)

    def test_failed_transfer_leaves_no_orphaned_entries(self):
        """Failures that occur before transaction creation (insufficient
        funds, frozen wallet) must leave zero new Transaction rows, zero new
        WalletTransaction rows and untouched balances."""
        # Fresh wallet in a currency the user does not own yet.
        small_wallet = self.service.create_wallet(self.user, 'EUR')
        self.admin_service.adjust_balance(self.admin_user, small_wallet.id,
                                          Decimal('100'), 'Small balance')

        txns_before = Transaction.objects.count()
        ledger_before = WalletTransaction.objects.count()
        balance_before = self._balance(small_wallet.id)

        # Insufficient funds.
        with self.assertRaises(InsufficientBalance):
            self.service.transfer(self.user, small_wallet.id,
                                  self.receiver_wallet.id, Decimal('1000'), 'Overdraw')
        self.assertEqual(Transaction.objects.count(), txns_before)
        self.assertEqual(WalletTransaction.objects.count(), ledger_before)
        self.assertEqual(self._balance(small_wallet.id), balance_before)

        # Frozen receiver mid-flight attempt.
        self.admin_service.freeze_wallet(self.admin_user, self.receiver_wallet.id)
        with self.assertRaises(WalletFrozen):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('100'), 'To frozen')
        self.assertEqual(Transaction.objects.count(), txns_before)
        self.assertEqual(WalletTransaction.objects.count(), ledger_before)
        self.assertEqual(self._balance(self.sender_wallet.id), Decimal('5000000'))
        self.assertEqual(self._balance(self.receiver_wallet.id), Decimal('5000000'))

    def test_freeze_blocks_transfer_attempts(self):
        self.admin_service.freeze_wallet(self.admin_user, self.receiver_wallet.id)
        sender_before = self._balance(self.sender_wallet.id)
        receiver_before = self._balance(self.receiver_wallet.id)
        transfers_before = Transaction.objects.filter(type=Transaction.Type.TRANSFER).count()

        with self.assertRaises(WalletFrozen):
            self.service.transfer(self.user, self.sender_wallet.id,
                                  self.receiver_wallet.id, Decimal('100'), 'To frozen')

        # Balances consistent; no transfer recorded.
        self.assertEqual(self._balance(self.sender_wallet.id), sender_before)
        self.assertEqual(self._balance(self.receiver_wallet.id), receiver_before)
        self.assertEqual(
            Transaction.objects.filter(type=Transaction.Type.TRANSFER).count(),
            transfers_before)


# ---------------------------------------------------------------------------
# API smoke tests (APIClient + JWT)
# ---------------------------------------------------------------------------

class APIIntegrationTests(TransactionTestCase):
    """API-level tests. TransactionTestCase so the concurrency test (threads)
    sees committed data; the middleware (wallet.api.idempotency) is active
    because it lives in MIDDLEWARE."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='customer', email='customer@example.com',
            password='password123', role='CUSTOMER')
        self.admin_user = User.objects.create_user(
            username='admin', email='admin@example.com',
            password='password123', role='ADMIN')
        self.user_access = str(RefreshToken.for_user(self.user).access_token)
        self.admin_access = str(RefreshToken.for_user(self.admin_user).access_token)

        self.wallet = WalletService().create_wallet(self.user, 'USD')
        AdminWalletService().adjust_balance(self.admin_user, self.wallet.id,
                                            Decimal('10000'), 'Initial funding')

    def _balance(self, wallet_id):
        return Wallet.objects.get(id=wallet_id).balance

    def test_register_wallet_deposit_api_flow(self):
        # 1. Register.
        response = self.client.post('/api/auth/register/', {
            'username': 'newuser', 'email': 'newuser@example.com',
            'password': 'securepass123'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['role'], 'CUSTOMER')

        # 2. Obtain JWT pair via the simplejwt endpoint.
        response = self.client.post('/api/auth/token/', {
            'username': 'newuser', 'password': 'securepass123'})
        self.assertEqual(response.status_code, 200)
        access = response.data['access']

        # 3. Create a wallet.
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        response = self.client.post('/api/wallets/', {'currency': 'USD'})
        self.assertEqual(response.status_code, 201)
        wallet_id = response.data['id']

        # 4. Deposit through the API.
        response = self.client.post(
            f'/api/wallets/{wallet_id}/deposit/', {'amount': '500.00'},
            HTTP_IDEMPOTENCY_KEY='register-flow-key')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], Transaction.Status.COMPLETED)
        self.assertEqual(Decimal(response.data['amount']), Decimal('500.00'))

        wallet = Wallet.objects.get(id=wallet_id)
        self.assertEqual(wallet.balance, Decimal('500.00'))

    def test_admin_endpoint_requires_admin_role(self):
        # Admin is allowed to freeze.
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.admin_access}')
        response = self.client.post(f'/api/admin/wallets/{self.wallet.id}/freeze/')
        self.assertEqual(response.status_code, 200)

        # Customer gets 403 on the same endpoint (and on the others).
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.user_access}')
        response = self.client.post(f'/api/admin/wallets/{self.wallet.id}/freeze/')
        self.assertEqual(response.status_code, 403)
        response = self.client.post(f'/api/admin/wallets/{self.wallet.id}/unfreeze/')
        self.assertEqual(response.status_code, 403)
        response = self.client.post(f'/api/admin/wallets/{self.wallet.id}/adjust-balance/',
                                    {'amount': '100.00', 'reason': 'Not allowed'})
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Edge cases / filters
# ---------------------------------------------------------------------------

class EdgeCaseTests(TestCase):
    """Extra coverage for limits and TransactionService.history() filters.
    Note: history() returns a HistoryPage (items/total/page), not a list."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='customer', email='customer@example.com',
            password='password123', role='CUSTOMER')
        self.admin_user = User.objects.create_user(
            username='admin', email='admin@example.com',
            password='password123', role='ADMIN')
        self.service = WalletService()
        self.admin_service = AdminWalletService()
        self.wallet = self.service.create_wallet(self.user, 'USD')
        self.admin_service.adjust_balance(self.admin_user, self.wallet.id,
                                          Decimal('100000'), 'Initial funding')

    def test_deposit_exceeds_max_single_limit(self):
        with self.assertRaises(LimitExceeded):
            self.service.deposit(self.user, self.wallet.id, Decimal('100001'))

    def test_withdraw_exceeds_max_single_limit(self):
        with self.assertRaises(LimitExceeded):
            self.service.withdraw(self.user, self.wallet.id, Decimal('50001'))

    def test_history_filters(self):
        # One ADJUSTMENT already exists from setUp funding.
        self.service.deposit(self.user, self.wallet.id, Decimal('100'))
        self.service.withdraw(self.user, self.wallet.id, Decimal('50'))

        history = TransactionService().history(self.user, {'type': Transaction.Type.DEPOSIT})
        self.assertEqual(history.total, 1)
        self.assertEqual(history.items[0].type, Transaction.Type.DEPOSIT)

        history = TransactionService().history(self.user, {'status': Transaction.Status.COMPLETED})
        self.assertEqual(history.total, 3)  # adjustment + deposit + withdrawal

        # direction: incoming = receiver_wallet belongs to the user
        # (adjustment + deposit); outgoing = sender_wallet belongs to the user
        # (withdrawal only).
        history = TransactionService().history(self.user, {'direction': 'incoming'})
        self.assertEqual(history.total, 2)
        history = TransactionService().history(self.user, {'direction': 'outgoing'})
        self.assertEqual(history.total, 1)

        history = TransactionService().history(self.user, {'amount_min': Decimal('60')})
        self.assertEqual(history.total, 2)  # deposit 100 + adjustment 100000, not the 50 withdrawal

        # Reference filter matches by substring.
        deposit_ref = Transaction.objects.get(type=Transaction.Type.DEPOSIT).reference
        history = TransactionService().history(self.user, {'reference': deposit_ref})
        self.assertEqual(history.total, 1)

    def test_wallet_not_found(self):
        missing = uuid.uuid4()

        with self.assertRaises(WalletNotFound):
            self.service.deposit(self.user, missing, Decimal('100'))
        with self.assertRaises(WalletNotFound):
            self.service.withdraw(self.user, missing, Decimal('100'))

        # adjust_balance now converts the repository's Wallet.DoesNotExist into
        # the same WalletNotFound domain error used everywhere else.
        with self.assertRaises(WalletNotFound):
            self.admin_service.adjust_balance(self.admin_user, missing,
                                              Decimal('100'), 'Missing wallet')


# ---------------------------------------------------------------------------
# Auto-created default wallet + page-level wallet creation
# ---------------------------------------------------------------------------

class RegistrationWalletTests(TestCase):
    """Every new account gets a default PKR wallet (web and API), and the
    wallet_create page view opens wallets for additional currencies."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com',
            password='password123', role='CUSTOMER')
        self.wallet = WalletService().create_wallet(self.user, 'USD')

    def test_web_register_auto_creates_default_wallet(self):
        self.client.logout()
        response = self.client.post('/accounts/register/', {
            'username': 'webuser', 'email': 'webuser@example.com',
            'password': 'securepass123', 'password_confirm': 'securepass123'})
        self.assertRedirects(response, '/accounts/login/')
        wallet = Wallet.objects.get(user__username='webuser')
        self.assertEqual(wallet.currency, settings.DEFAULT_WALLET_CURRENCY)
        self.assertEqual(wallet.balance, Decimal('0'))
        self.assertEqual(wallet.status, Wallet.Status.ACTIVE)

    def test_api_register_auto_creates_default_wallet(self):
        response = self.client.post('/api/auth/register/', {
            'username': 'apiuser', 'email': 'apiuser@example.com',
            'password': 'securepass123'})
        self.assertEqual(response.status_code, 201)
        wallet = Wallet.objects.get(user__username='apiuser')
        self.assertEqual(wallet.currency, settings.DEFAULT_WALLET_CURRENCY)

    def test_wallet_create_page_opens_new_currency(self):
        self.client.force_login(self.user)
        response = self.client.post('/wallets/create/', {'currency': 'EUR'})
        self.assertRedirects(response, '/dashboard/')
        self.assertTrue(Wallet.objects.filter(user=self.user, currency='EUR').exists())

    def test_wallet_create_page_duplicate_currency_fails_gracefully(self):
        self.client.force_login(self.user)
        response = self.client.post('/wallets/create/', {'currency': 'USD'})
        self.assertRedirects(response, '/dashboard/')
        messages = [m for m in get_messages(response.wsgi_request)]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level_tag, 'error')
        self.assertEqual(Wallet.objects.filter(user=self.user).count(), 1)

    def test_wallet_create_page_rejects_bad_currency(self):
        self.client.force_login(self.user)
        response = self.client.post('/wallets/create/', {'currency': 'U'})
        self.assertRedirects(response, '/dashboard/')
        self.assertEqual(Wallet.objects.filter(user=self.user).count(), 1)

    def test_wallet_create_page_get_redirects_to_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get('/wallets/create/')
        self.assertRedirects(response, '/dashboard/')

    def test_wallet_create_page_requires_login(self):
        self.client.logout()
        response = self.client.post('/wallets/create/', {'currency': 'EUR'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
