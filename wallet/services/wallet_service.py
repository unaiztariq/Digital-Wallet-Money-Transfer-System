from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from wallet.models import Transaction, Wallet, WalletConfiguration, WalletTransaction
from wallet.repositories import transaction_repository as transactions
from wallet.repositories import wallet_repository as wallets
from wallet.repositories.errors import DuplicateTransaction, InsufficientBalance, InvalidAmount, LimitExceeded, WalletFrozen


def _amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidAmount('Amount must be a positive number') from exc
    if amount <= 0:
        raise InvalidAmount('Amount must be greater than zero')
    return amount


class WalletService:
    def create_wallet(self, user, currency):
        currency = (currency or '').upper()
        if len(currency) != 3 or not currency.isalpha():
            raise InvalidAmount('Currency must be three alphabetic characters')
        existing = Wallet.objects.filter(user=user, currency=currency).first()
        if existing and existing.status == Wallet.Status.CLOSED:
            existing.status = Wallet.Status.ACTIVE
            existing.save(update_fields=['status', 'updated_at'])
            return existing
        # Own savepoint so a duplicate-currency IntegrityError rolls back before
        # being converted to BusinessRuleError — otherwise the caller's
        # transaction is left poisoned and the next query 500s.
        with transaction.atomic():
            return wallets.create_wallet(user, currency)

    def get_wallet(self, user, wallet_id):
        return wallets.get_wallet_for_user_or_raise(wallet_id, user)

    def list_wallets(self, user):
        return wallets.get_wallets_for_user(user)

    def list_recipient_wallets(self, user):
        # Deprecated: retain for compatibility. Prefer per-wallet saved recipients.
        return Wallet.objects.filter(status=Wallet.Status.ACTIVE).exclude(user=user).order_by('user__username', 'created_at')

    def _replay(self, wallet_id, key):
        if not key:
            return None
        txn = Transaction.objects.filter(idempotency_key=key).filter(
            Q(sender_wallet_id=wallet_id) | Q(receiver_wallet_id=wallet_id)).first()
        if not txn:
            return None
        if txn.status in {Transaction.Status.COMPLETED, Transaction.Status.FAILED, Transaction.Status.CANCELLED}:
            return txn
        raise DuplicateTransaction('Transaction with this idempotency key is pending')

    def deposit(self, user, wallet_id, amount, description=None, idempotency_key=None):
        replay = self._replay(wallet_id, idempotency_key)
        if replay: return replay
        wallet = self.get_wallet(user, wallet_id)
        if wallet.status != Wallet.Status.ACTIVE: raise WalletFrozen('Wallet is not active')
        amount = _amount(amount)
        if amount > WalletConfiguration.get_config().max_single_deposit: raise LimitExceeded('max_single_deposit')
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
            if wallet.status != Wallet.Status.ACTIVE: raise WalletFrozen('Wallet is not active')
            txn = transactions.create_transaction(reference=transactions.next_reference(timezone.localdate()), sender_wallet=None,
                receiver_wallet=wallet, type=Transaction.Type.DEPOSIT, amount=amount, currency=wallet.currency,
                description=description, idempotency_key=idempotency_key)
            balance = wallet.balance + amount
            transactions.create_wallet_transaction(txn, wallet, WalletTransaction.EntryType.CREDIT, amount, balance)
            wallets.update_balance(wallet, balance)
            txn.status = Transaction.Status.COMPLETED; txn.save(update_fields=['status', 'updated_at'])
            return txn

    def withdraw(self, user, wallet_id, amount, description=None, idempotency_key=None):
        replay = self._replay(wallet_id, idempotency_key)
        if replay: return replay
        wallet = self.get_wallet(user, wallet_id)
        if wallet.status != Wallet.Status.ACTIVE: raise WalletFrozen('Wallet is not active')
        amount = _amount(amount)
        config = WalletConfiguration.get_config()
        if amount > config.max_single_withdrawal: raise LimitExceeded('max_single_withdrawal')
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
            if wallet.status != Wallet.Status.ACTIVE: raise WalletFrozen('Wallet is not active')
            if wallets.sum_debits_for_date(wallet.id, timezone.localdate(), Transaction.Type.WITHDRAWAL) + amount > config.max_daily_withdrawal: raise LimitExceeded('max_daily_withdrawal')
            if wallet.balance < amount: raise InsufficientBalance('Insufficient balance')
            txn = transactions.create_transaction(reference=transactions.next_reference(timezone.localdate()), sender_wallet=wallet,
                receiver_wallet=None, type=Transaction.Type.WITHDRAWAL, amount=amount, currency=wallet.currency,
                description=description, idempotency_key=idempotency_key)
            balance = wallet.balance - amount
            transactions.create_wallet_transaction(txn, wallet, WalletTransaction.EntryType.DEBIT, amount, balance)
            wallets.update_balance(wallet, balance)
            txn.status = Transaction.Status.COMPLETED; txn.save(update_fields=['status', 'updated_at'])
            return txn

    def transfer(self, user, sender_wallet_id, recipient_wallet_id=None, recipient_reference=None, amount=None, description=None, idempotency_key=None):
        from wallet.services.transfer_service import TransferService
        return TransferService().transfer(user, sender_wallet_id, recipient_wallet_id=recipient_wallet_id, recipient_reference=recipient_reference, amount=amount, description=description, idempotency_key=idempotency_key)

    def list_saved_recipients_for_wallet(self, sender_wallet_id):
        return wallets.get_saved_recipients_for_sender_wallet(sender_wallet_id)
