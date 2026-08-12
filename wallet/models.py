import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer'
        ADMIN = 'ADMIN', 'Admin'
        SYSTEM = 'SYSTEM', 'System'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.CUSTOMER)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WalletConfiguration(models.Model):
    max_single_deposit = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    max_single_withdrawal = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    max_daily_withdrawal = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    max_single_transfer = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    max_daily_transfer = models.DecimalField(max_digits=18, decimal_places=4, default=0)

    # Spec limits (must match migrations/0002_seed_wallet_configuration.py).
    # get_or_create with defaults makes the singleton self-healing: data
    # migrations are wiped by TransactionTestCase's flush between tests, and
    # a freshly created row must carry the spec limits instead of zeros.
    _DEFAULTS = {
        'max_single_deposit': Decimal('100000.0000'),
        'max_single_withdrawal': Decimal('50000.0000'),
        'max_daily_withdrawal': Decimal('100000.0000'),
        'max_single_transfer': Decimal('100000.0000'),
        'max_daily_transfer': Decimal('500000.0000'),
    }

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1, defaults=cls._DEFAULTS)
        return config


class ExchangeRate(models.Model):
    currency = models.CharField(max_length=3, primary_key=True)
    rate_to_pkr = models.DecimalField(max_digits=20, decimal_places=8)
    updated_at = models.DateTimeField(auto_now=True)

    # Spec rates (must match migrations/0004_exchange_rate.py).
    # get_or_create makes rates self-healing: TransactionTestCase's flush
    # wipes seeded rows between tests, so freshly created rows use spec rates.
    _DEFAULTS = {
        'USD': Decimal('278.50000000'),
        'EUR': Decimal('301.00000000'),
        'GBP': Decimal('350.00000000'),
        'JPY': Decimal('1.85000000'),
        'CHF': Decimal('312.00000000'),
    }

    @classmethod
    def get_rates(cls) -> dict:
        rates = {'PKR': Decimal('1')}
        for code, rate in cls._DEFAULTS.items():
            row, _ = cls.objects.get_or_create(currency=code, defaults={'rate_to_pkr': rate})
            rates[code] = row.rate_to_pkr
        return rates


class Wallet(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        FROZEN = 'FROZEN', 'Frozen'
        CLOSED = 'CLOSED', 'Closed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wallets')
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.ACTIVE)
    balance = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    config = models.ForeignKey(WalletConfiguration, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Closing and reopening a wallet reuses this row; (user, currency) remains unique.
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'currency'], name='one_active_wallet_per_currency'),
            models.CheckConstraint(condition=Q(status__in=['ACTIVE', 'FROZEN', 'CLOSED']), name='ck_wallet_status_valid'),
        ]
        indexes = [models.Index(fields=['user', 'status'])]


class Transaction(models.Model):
    class Type(models.TextChoices):
        DEPOSIT = 'DEPOSIT', 'Deposit'
        WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'
        TRANSFER = 'TRANSFER', 'Transfer'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=24, unique=True)
    sender_wallet = models.ForeignKey(Wallet, null=True, blank=True, on_delete=models.PROTECT, related_name='sent_transactions')
    receiver_wallet = models.ForeignKey(Wallet, null=True, blank=True, on_delete=models.PROTECT, related_name='received_transactions')
    type = models.CharField(max_length=10, choices=Type.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.TextField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default='')
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name='ck_transaction_amount_positive'),
            models.CheckConstraint(condition=Q(status__in=['PENDING', 'COMPLETED', 'FAILED', 'CANCELLED']), name='ck_transaction_status_valid'),
            models.CheckConstraint(condition=Q(type__in=['DEPOSIT', 'WITHDRAWAL', 'TRANSFER', 'ADJUSTMENT']), name='ck_transaction_type_valid'),
        ]
        indexes = [
            models.Index(fields=['sender_wallet', 'created_at']),
            models.Index(fields=['receiver_wallet', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]


class WalletTransaction(models.Model):
    # Immutable ledger entry: create entries only; never update or delete them.
    class EntryType(models.TextChoices):
        DEBIT = 'DEBIT', 'Debit'
        CREDIT = 'CREDIT', 'Credit'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name='entries')
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='ledger_entries')
    entry_type = models.CharField(max_length=6, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=4)
    balance_after = models.DecimalField(max_digits=18, decimal_places=4)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(entry_type__in=['DEBIT', 'CREDIT']), name='ck_wallet_transaction_entry_type_valid'),
        ]
        indexes = [models.Index(fields=['wallet', 'created_at'])]


class DailyCounter(models.Model):
    date = models.DateField(primary_key=True)
    counter = models.BigIntegerField(default=0)


class IdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    response_status = models.IntegerField(null=True)
    response_body = models.JSONField(null=True)
    locked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['idempotency_key', 'payload_hash'], name='uniq_idempotency_key_hash'),
        ]
        indexes = [models.Index(fields=['created_at'])]
