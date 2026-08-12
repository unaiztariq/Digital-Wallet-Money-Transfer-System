from dataclasses import dataclass

from django.db.models import Q

from wallet.models import Transaction, User
from wallet.repositories import transaction_repository as transactions
from wallet.repositories.errors import InvalidStateTransition, PermissionDenied


@dataclass
class HistoryPage:
    items: list
    total: int
    page: int


class TransactionService:
    def history(self, user, filters=None, page=1):
        query = transactions.get_transactions_for_wallet_or_history(
            user=None if user.role == User.Role.ADMIN else user, filters=filters)
        total = query.count(); page = max(int(page), 1); size = 20
        return HistoryPage(list(query[(page - 1) * size:page * size]), total, page)

    def detail(self, user, txn_id):
        txn = transactions.get_transaction_by_id(txn_id)
        if txn is None: raise PermissionDenied('Transaction not found')
        if user.role != User.Role.ADMIN and not Transaction.objects.filter(pk=txn.id).filter(
            Q(sender_wallet__user=user) | Q(receiver_wallet__user=user)).exists():
            raise PermissionDenied('Transaction is not accessible')
        return txn

    def confirm(self, txn):
        return self._transition(txn, Transaction.Status.COMPLETED)

    def cancel(self, txn):
        return self._transition(txn, Transaction.Status.CANCELLED)

    def fail(self, txn, reason):
        txn = self._pending(txn); txn.status = Transaction.Status.FAILED; txn.failure_reason = reason
        txn.save(update_fields=['status', 'failure_reason', 'updated_at']); return txn

    def _pending(self, txn):
        if txn.status != Transaction.Status.PENDING: raise InvalidStateTransition('Transaction is not pending')
        return txn

    def _transition(self, txn, status):
        txn = self._pending(txn); txn.status = status; txn.save(update_fields=['status', 'updated_at']); return txn
