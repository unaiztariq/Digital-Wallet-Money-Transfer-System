from django.db import transaction
from django.utils import timezone
from decimal import Decimal, InvalidOperation

from wallet.models import Transaction, User, Wallet, WalletTransaction
from wallet.repositories import transaction_repository as transactions
from wallet.repositories import wallet_repository as wallets
from wallet.repositories.errors import InsufficientBalance, InvalidAmount, InvalidStateTransition, PermissionDenied, WalletNotFound
from wallet.services.transaction_service import TransactionService


class AdminWalletService:
    def _admin(self, actor_user):
        if actor_user.role != User.Role.ADMIN: raise PermissionDenied('Administrator role required')

    def freeze_wallet(self, actor_user, wallet_id):
        self._admin(actor_user); wallet = wallets.get_wallet_or_raise(wallet_id)
        if wallet.status != Wallet.Status.ACTIVE: raise InvalidStateTransition('Only active wallets can be frozen')
        wallet.status = Wallet.Status.FROZEN; wallet.save(update_fields=['status', 'updated_at']); return wallet

    def unfreeze_wallet(self, actor_user, wallet_id):
        self._admin(actor_user); wallet = wallets.get_wallet_or_raise(wallet_id)
        if wallet.status != Wallet.Status.FROZEN: raise InvalidStateTransition('Only frozen wallets can be unfrozen')
        wallet.status = Wallet.Status.ACTIVE; wallet.save(update_fields=['status', 'updated_at']); return wallet

    def adjust_balance(self, actor_user, wallet_id, amount, reason):
        self._admin(actor_user)
        try:
            raw = Decimal(str(amount))
        except (InvalidOperation, ValueError) as exc:
            raise InvalidAmount('Amount must be a number') from exc
        if raw == 0:
            raise InvalidAmount('Amount must not be zero')
        with transaction.atomic():
            try:
                wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
            except Wallet.DoesNotExist:
                raise WalletNotFound('Wallet not found')
            balance = wallet.balance + raw
            if balance < 0: raise InsufficientBalance('Insufficient balance')
            entry = WalletTransaction.EntryType.CREDIT if raw > 0 else WalletTransaction.EntryType.DEBIT
            txn = transactions.create_transaction(reference=transactions.next_reference(timezone.localdate()), sender_wallet=wallet if raw < 0 else None,
                receiver_wallet=wallet if raw > 0 else None, type=Transaction.Type.ADJUSTMENT, amount=abs(raw), currency=wallet.currency)
            transactions.create_wallet_transaction(txn, wallet, entry, abs(raw), balance); wallets.update_balance(wallet, balance)
            txn.status = Transaction.Status.COMPLETED; txn.failure_reason = reason; txn.save(update_fields=['status', 'failure_reason', 'updated_at']); return txn

    def list_users(self, actor_user):
        self._admin(actor_user); return User.objects.all().order_by('date_joined')

    def list_wallets(self, actor_user):
        self._admin(actor_user); return Wallet.objects.all().order_by('created_at')

    def list_all_transactions(self, actor_user, filters=None, page=1):
        self._admin(actor_user); return TransactionService().history(actor_user, filters, page)

    def get_failed_transactions(self, actor_user):
        self._admin(actor_user); return Transaction.objects.filter(status=Transaction.Status.FAILED).order_by('-created_at')
