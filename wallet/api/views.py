from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from wallet.api.permissions import IsAdminRole, IsCustomerRole
from wallet.api.serializers import (AdjustBalanceSerializer, DepositSerializer, HistoryFilterSerializer,
    RegisterSerializer, TransactionSerializer, TransferSerializer, UserAdminSerializer,
    WalletCreateSerializer, WalletSerializer, WithdrawSerializer)
from wallet.services.admin_wallet_service import AdminWalletService
from wallet.services.transaction_service import TransactionService
from wallet.services.wallet_service import WalletService


def transaction_response(transaction, response_status=status.HTTP_200_OK, detail='Transaction completed'):
    data = TransactionSerializer(transaction).data
    wallet = transaction.sender_wallet if (transaction.sender_wallet_id and transaction.receiver_wallet_id) else (transaction.receiver_wallet or transaction.sender_wallet)
    if wallet is not None:
        data['balance'] = wallet.balance
    data['detail'] = detail
    return Response(data, status=response_status)


class RegisterAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_user_model().objects.create_user(role='CUSTOMER', **serializer.validated_data)
        return Response(UserAdminSerializer(user).data, status=status.HTTP_201_CREATED)


class WalletListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerRole]

    def get(self, request):
        return Response(WalletSerializer(WalletService().list_wallets(request.user), many=True).data)

    def post(self, request):
        serializer = WalletCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = WalletService().create_wallet(request.user, **serializer.validated_data)
        return Response(WalletSerializer(wallet).data, status=status.HTTP_201_CREATED)


class WalletRetrieveAPIView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerRole]

    def get(self, request, wallet_id):
        return Response(WalletSerializer(WalletService().get_wallet(request.user, wallet_id)).data)


class DepositAPIView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerRole]

    def post(self, request, wallet_id):
        serializer = DepositSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        txn = WalletService().deposit(request.user, wallet_id, idempotency_key=request.headers.get('Idempotency-Key'), **serializer.validated_data)
        return transaction_response(txn, status.HTTP_201_CREATED, 'Deposit successful')


class WithdrawAPIView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerRole]

    def post(self, request, wallet_id):
        serializer = WithdrawSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        txn = WalletService().withdraw(request.user, wallet_id, idempotency_key=request.headers.get('Idempotency-Key'), **serializer.validated_data)
        return transaction_response(txn, status.HTTP_201_CREATED, 'Withdrawal successful')


class TransferAPIView(APIView):
    permission_classes = [IsAuthenticated, IsCustomerRole]

    def post(self, request, wallet_id):
        serializer = TransferSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        txn = WalletService().transfer(request.user, wallet_id, idempotency_key=request.headers.get('Idempotency-Key'), **serializer.validated_data)
        return transaction_response(txn, status.HTTP_201_CREATED, 'Transfer submitted')


class TransactionListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = HistoryFilterSerializer(data=request.query_params); serializer.is_valid(raise_exception=True)
        values = serializer.validated_data; page = values.pop('page', 1)
        history = TransactionService().history(request.user, values, page)
        return Response({'count': history.total, 'page': history.page, 'results': TransactionSerializer(history.items, many=True).data})


class TransactionRetrieveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, transaction_id):
        return transaction_response(TransactionService().detail(request.user, transaction_id))


class AdminFreezeAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, wallet_id):
        wallet = AdminWalletService().freeze_wallet(request.user, wallet_id)
        return Response({'detail': 'Wallet frozen', 'wallet': WalletSerializer(wallet).data})


class AdminUnfreezeAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, wallet_id):
        wallet = AdminWalletService().unfreeze_wallet(request.user, wallet_id)
        return Response({'detail': 'Wallet unfrozen', 'wallet': WalletSerializer(wallet).data})


class AdminAdjustBalanceAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, wallet_id):
        serializer = AdjustBalanceSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        return transaction_response(AdminWalletService().adjust_balance(request.user, wallet_id, **serializer.validated_data), status.HTTP_201_CREATED, 'Balance adjusted')


class AdminUsersAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(UserAdminSerializer(AdminWalletService().list_users(request.user), many=True).data)


class AdminWalletsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response(WalletSerializer(AdminWalletService().list_wallets(request.user), many=True).data)


class AdminTransactionsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        serializer = HistoryFilterSerializer(data=request.query_params); serializer.is_valid(raise_exception=True)
        values = serializer.validated_data; page = values.pop('page', 1)
        history = AdminWalletService().list_all_transactions(request.user, values, page)
        return Response({'count': history.total, 'page': history.page, 'results': TransactionSerializer(history.items, many=True).data})
