from rest_framework import serializers

from wallet.models import Transaction, User, Wallet


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ('id', 'currency', 'status', 'balance', 'created_at', 'updated_at')


class WalletCreateSerializer(serializers.Serializer):
    currency = serializers.CharField(max_length=3)

    def validate_currency(self, value):
        value = value.upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError('Currency must be three alphabetic characters.')
        return value


class DepositSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=4)


class WithdrawSerializer(DepositSerializer):
    pass


class TransferSerializer(serializers.Serializer):
    recipient_wallet_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=4)
    description = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class TransactionSerializer(serializers.ModelSerializer):
    sender_wallet_id = serializers.UUIDField(source='sender_wallet.id', allow_null=True, read_only=True)
    receiver_wallet_id = serializers.UUIDField(source='receiver_wallet.id', allow_null=True, read_only=True)

    class Meta:
        model = Transaction
        fields = ('id', 'reference', 'type', 'amount', 'currency', 'status', 'failure_reason',
                  'created_at', 'sender_wallet_id', 'receiver_wallet_id')


class AdjustBalanceSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=4)
    reason = serializers.CharField(max_length=1000)


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)


class UserAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'role', 'is_active', 'created_at')


class HistoryFilterSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Transaction.Type.choices, required=False)
    status = serializers.ChoiceField(choices=Transaction.Status.choices, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    amount_min = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    amount_max = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)
    reference = serializers.CharField(required=False, max_length=24)
    direction = serializers.ChoiceField(choices=('incoming', 'outgoing'), required=False)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
