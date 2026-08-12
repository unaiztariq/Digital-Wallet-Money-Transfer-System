from django.urls import path

from wallet import views
from wallet.api import views as api_views


urlpatterns = [
    # Root alias: anonymous users hitting '/' get 302'd to login by
    # @login_required; the canonical, named path is /dashboard/.
    path('', views.dashboard),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('wallets/create/', views.wallet_create, name='wallet_create'),
    path('open-wallet/', views.open_wallet, name='open_wallet'),
    path('transfer/', views.transfer, name='transfer'),
    path('saved-recipient/remove/', views.remove_saved_recipient, name='remove_saved_recipient'),
    path('deposit/', views.deposit, name='deposit'),
    path('withdraw/', views.withdraw, name='withdraw'),
    path('transactions/', views.transactions, name='transactions'),
    path('admin-console/', views.admin_console, name='admin_console'),
    path('accounts/login/', views.login, name='login'),
    path('accounts/register/', views.register, name='register'),
    path('accounts/logout/', views.logout, name='logout'),
    path('api/auth/register/', api_views.RegisterAPIView.as_view(), name='api_register'),
    path('api/wallets/', api_views.WalletListCreateAPIView.as_view(), name='api_wallets'),
    path('api/wallets/<uuid:wallet_id>/', api_views.WalletRetrieveAPIView.as_view(), name='api_wallet_detail'),
    path('api/wallets/<uuid:wallet_id>/deposit/', api_views.DepositAPIView.as_view(), name='api_deposit'),
    path('api/wallets/<uuid:wallet_id>/withdraw/', api_views.WithdrawAPIView.as_view(), name='api_withdraw'),
    path('api/wallets/<uuid:wallet_id>/transfer/', api_views.TransferAPIView.as_view(), name='api_transfer'),
    path('api/transactions/', api_views.TransactionListAPIView.as_view(), name='api_transactions'),
    path('api/transactions/<uuid:transaction_id>/', api_views.TransactionRetrieveAPIView.as_view(), name='api_transaction_detail'),
    path('api/admin/wallets/<uuid:wallet_id>/freeze/', api_views.AdminFreezeAPIView.as_view(), name='api_admin_freeze'),
    path('api/admin/wallets/<uuid:wallet_id>/unfreeze/', api_views.AdminUnfreezeAPIView.as_view(), name='api_admin_unfreeze'),
    path('api/admin/wallets/<uuid:wallet_id>/adjust-balance/', api_views.AdminAdjustBalanceAPIView.as_view(), name='api_admin_adjust_balance'),
    path('api/admin/users/', api_views.AdminUsersAPIView.as_view(), name='api_admin_users'),
    path('api/admin/wallets/', api_views.AdminWalletsAPIView.as_view(), name='api_admin_wallets'),
    path('api/admin/transactions/', api_views.AdminTransactionsAPIView.as_view(), name='api_admin_transactions'),
]
