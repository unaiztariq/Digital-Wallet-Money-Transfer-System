from django.db import transaction
from django.utils import timezone

from wallet.models import Transaction, Wallet, WalletConfiguration, WalletTransaction
from wallet.repositories import transaction_repository as transactions
from wallet.repositories import wallet_repository as wallets
from wallet.repositories.errors import DuplicateTransaction, InsufficientBalance, LimitExceeded, PermissionDenied, SelfTransfer, WalletFrozen
from wallet.services.wallet_service import _amount


class TransferService:
    def transfer(self, user, sender_wallet_id, recipient_wallet_id, amount, description=None, idempotency_key=None):
        if idempotency_key:
            old = Transaction.objects.filter(idempotency_key=idempotency_key, sender_wallet_id=sender_wallet_id).first()
            if old:
                if old.status in {Transaction.Status.COMPLETED, Transaction.Status.FAILED, Transaction.Status.CANCELLED}: return old
                raise DuplicateTransaction('Transaction with this idempotency key is pending')
        amount = _amount(amount)
        sender = wallets.get_wallet_or_raise(sender_wallet_id); receiver = wallets.get_wallet_or_raise(recipient_wallet_id)
        if sender.id == receiver.id: raise SelfTransfer('Cannot transfer to the same wallet')
        if sender.user_id != user.id: raise PermissionDenied('Wallet does not belong to this user')
        if sender.status != Wallet.Status.ACTIVE or receiver.status != Wallet.Status.ACTIVE: raise WalletFrozen('Both wallets must be active')
        config = WalletConfiguration.get_config()
        if amount > config.max_single_transfer: raise LimitExceeded('max_single_transfer')
        failure = None
        with transaction.atomic():
            sender, receiver = wallets.lock_wallets_for_transfer(sender_wallet_id, recipient_wallet_id)
            if sender.status != Wallet.Status.ACTIVE or receiver.status != Wallet.Status.ACTIVE: raise WalletFrozen('Both wallets must be active')
            if sender.balance < amount: raise InsufficientBalance('Insufficient balance')
            if wallets.sum_debits_for_date(sender.id, timezone.localdate(), Transaction.Type.TRANSFER) + amount > config.max_daily_transfer: raise LimitExceeded('max_daily_transfer')
            txn = transactions.create_transaction(reference=transactions.next_reference(timezone.localdate()), sender_wallet=sender, receiver_wallet=receiver,
                type=Transaction.Type.TRANSFER, amount=amount, currency=sender.currency, description=description,
                idempotency_key=idempotency_key)
            try:
                with transaction.atomic():
                    sender_balance, receiver_balance = sender.balance - amount, receiver.balance + amount
                    transactions.create_wallet_transaction(txn, sender, WalletTransaction.EntryType.DEBIT, amount, sender_balance)
                    transactions.create_wallet_transaction(txn, receiver, WalletTransaction.EntryType.CREDIT, amount, receiver_balance)
                    wallets.update_balance(sender, sender_balance); wallets.update_balance(receiver, receiver_balance)
                    txn.status = Transaction.Status.COMPLETED; txn.save(update_fields=['status', 'updated_at'])
                    return txn
            except Exception as exc:
                # The inner block already rolled back (no money moved). Persist
                # the FAILED audit row inside the OUTER transaction, then re-raise
                # AFTER the outer block commits so the row is not rolled back.
                failure = exc
                txn.status = Transaction.Status.FAILED
                txn.failure_reason = str(exc)
                txn.save(update_fields=['status', 'failure_reason', 'updated_at'])
        if failure is not None:
            raise failure
