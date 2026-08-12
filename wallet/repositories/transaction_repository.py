from django.db import IntegrityError
from django.db.models import Q

from wallet.models import DailyCounter, Transaction, WalletTransaction


def create_transaction(*, reference, sender_wallet, receiver_wallet, type, amount, currency, description=None, idempotency_key=None):
    return Transaction.objects.create(reference=reference, sender_wallet=sender_wallet, receiver_wallet=receiver_wallet,
        type=type, amount=amount, currency=currency, description=description or '', idempotency_key=idempotency_key,
        status=Transaction.Status.PENDING)


def create_wallet_transaction(transaction, wallet, entry_type, amount, balance_after):
    # Immutable ledger row: never update or delete.
    return WalletTransaction.objects.create(transaction=transaction, wallet=wallet, entry_type=entry_type,
        amount=amount, balance_after=balance_after)


def get_transaction_by_reference(ref):
    return Transaction.objects.filter(reference=ref).first()


def get_transactions_for_wallet_or_history(wallet=None, user=None, filters=None):
    query = Transaction.objects.select_related('sender_wallet__user', 'receiver_wallet__user')
    if wallet is not None:
        query = query.filter(Q(sender_wallet=wallet) | Q(receiver_wallet=wallet))
    elif user is not None:
        query = query.filter(Q(sender_wallet__user=user) | Q(receiver_wallet__user=user))
    for field, value in (filters or {}).items():
        if value in (None, ''):
            continue
        if field == 'date_from': query = query.filter(created_at__date__gte=value)
        elif field == 'date_to': query = query.filter(created_at__date__lte=value)
        elif field == 'amount_min': query = query.filter(amount__gte=value)
        elif field == 'amount_max': query = query.filter(amount__lte=value)
        elif field == 'reference': query = query.filter(reference__icontains=value)
        elif field in {'type', 'status'}: query = query.filter(**{field: value})
        elif field == 'direction' and user is not None:
            if value == 'incoming': query = query.filter(receiver_wallet__user=user)
            elif value == 'outgoing': query = query.filter(sender_wallet__user=user)
    return query.order_by('-created_at')


def get_transaction_by_id(txn_id):
    return Transaction.objects.filter(pk=txn_id).first()


def next_reference(date):
    try:
        counter, _ = DailyCounter.objects.select_for_update().get_or_create(date=date)
    except IntegrityError:
        try:
            counter = DailyCounter.objects.select_for_update().get(date=date)
        except DailyCounter.DoesNotExist:
            try:
                counter, _ = DailyCounter.objects.get_or_create(date=date)
            except IntegrityError:
                counter = DailyCounter.objects.select_for_update().get(date=date)
    counter.counter += 1
    counter.save(update_fields=['counter'])
    return f'TXN-{date:%Y%m%d}-{counter.counter:06d}'
