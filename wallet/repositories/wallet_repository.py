from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import IntegrityError
from django.db.models import Sum
from django.utils import timezone

from wallet.models import Transaction, Wallet, WalletConfiguration, WalletTransaction
from wallet.repositories.errors import BusinessRuleError, PermissionDenied, WalletNotFound
from wallet.models import SavedRecipient
from django.db import IntegrityError


def get_wallet_or_raise(wallet_id):
    try:
        return Wallet.objects.get(pk=wallet_id)
    except Wallet.DoesNotExist as exc:
        raise WalletNotFound('Wallet not found') from exc


def get_wallet_by_reference(reference_id):
    try:
        return Wallet.objects.get(reference_id=reference_id)
    except Wallet.DoesNotExist as exc:
        raise WalletNotFound('Wallet not found') from exc


def get_wallets_for_user(user):
    return Wallet.objects.filter(user=user).order_by('created_at')


def get_wallet_for_user_or_raise(wallet_id, user):
    wallet = get_wallet_or_raise(wallet_id)
    if wallet.user_id != user.id:
        raise PermissionDenied('Wallet does not belong to this user')
    return wallet


def lock_wallets_for_transfer(sender_id, receiver_id):
    wallets = list(Wallet.objects.select_for_update().filter(id__in=[sender_id, receiver_id]).order_by('id'))
    found = {wallet.id: wallet for wallet in wallets}
    if sender_id not in found or receiver_id not in found:
        raise WalletNotFound('Wallet not found')
    return found[sender_id], found[receiver_id]


def sum_debits_for_date(wallet_id, date, transaction_type=None):
    start = timezone.make_aware(datetime.combine(date, time.min))
    end = start + timedelta(days=1)
    entries = WalletTransaction.objects.filter(
        wallet_id=wallet_id, entry_type=WalletTransaction.EntryType.DEBIT,
        created_at__gte=start, created_at__lt=end,
    )
    if transaction_type:
        entries = entries.filter(transaction__type=transaction_type)
    return entries.aggregate(total=Sum('amount'))['total'] or Decimal('0')


def update_balance(wallet, new_balance):
    wallet.balance = new_balance
    wallet.save()
    return wallet


def create_wallet(user, currency):
    try:
        return Wallet.objects.create(user=user, currency=currency, config=WalletConfiguration.get_config())
    except IntegrityError as exc:
        raise BusinessRuleError('A wallet already exists for this currency') from exc


def get_saved_recipients_for_sender_wallet(sender_wallet_id):
    rows = SavedRecipient.objects.filter(sender_wallet_id=sender_wallet_id).select_related('recipient_wallet__user', 'recipient_wallet')
    return [r.recipient_wallet for r in rows]


def create_saved_recipient(sender_wallet, recipient_wallet):
    try:
        obj, _ = SavedRecipient.objects.get_or_create(sender_wallet=sender_wallet, recipient_wallet=recipient_wallet)
        return obj
    except IntegrityError:
        return SavedRecipient.objects.filter(sender_wallet=sender_wallet, recipient_wallet=recipient_wallet).first()


def delete_saved_recipient(sender_wallet_id, recipient_wallet_id):
    SavedRecipient.objects.filter(sender_wallet_id=sender_wallet_id, recipient_wallet_id=recipient_wallet_id).delete()
