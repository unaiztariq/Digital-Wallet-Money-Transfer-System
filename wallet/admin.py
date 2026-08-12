from django.contrib import admin

from .models import DailyCounter, IdempotencyRecord, Transaction, User, Wallet, WalletConfiguration, WalletTransaction


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'email', 'role', 'is_active')
    search_fields = ('username', 'email')


@admin.register(WalletConfiguration)
class WalletConfigurationAdmin(admin.ModelAdmin):
    list_display = ('id', 'max_single_deposit', 'max_single_withdrawal', 'max_daily_withdrawal')


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'currency', 'status', 'balance')
    list_filter = ('status', 'currency')


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('reference', 'type', 'amount', 'currency', 'status', 'created_at')
    list_filter = ('type', 'status')


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction', 'wallet', 'entry_type', 'amount', 'balance_after', 'created_at')


@admin.register(DailyCounter)
class DailyCounterAdmin(admin.ModelAdmin):
    list_display = ('date', 'counter')


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ('idempotency_key', 'payload_hash', 'response_status', 'created_at')
