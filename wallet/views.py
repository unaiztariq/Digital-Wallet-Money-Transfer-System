from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db import transaction
from django.shortcuts import redirect, render

from wallet.api.serializers import HistoryFilterSerializer
from wallet.models import ExchangeRate, Transaction, User, Wallet
from wallet.repositories.errors import BusinessRuleError, InvalidAmount
from wallet.services.admin_wallet_service import AdminWalletService
from wallet.services.transaction_service import TransactionService
from wallet.services.wallet_service import WalletService


def _present_wallets(wallets):
    wallets = list(wallets)
    for wallet in wallets:
        wallet.owner = wallet.user
    return wallets


def _present_transactions(user, txs, admin=False):
    txs = list(txs)
    for tx in txs:
        if not tx.description:
            tx.description = (tx.failure_reason
                              if tx.status == Transaction.Status.FAILED and tx.failure_reason
                              else '')
        tx.sender = tx.sender_wallet.user if tx.sender_wallet_id else None
        tx.recipient = tx.receiver_wallet.user if tx.receiver_wallet_id else None
        if admin:
            tx.direction = 'DEBIT' if tx.sender_wallet_id else 'CREDIT'
        elif tx.sender_wallet_id and tx.sender_wallet.user_id == user.id:
            tx.direction = 'DEBIT'
        elif tx.receiver_wallet_id and tx.receiver_wallet.user_id == user.id:
            tx.direction = 'CREDIT'
        else:
            tx.direction = 'CREDIT'
    return txs


def _selected_wallet(request, wallets):
    wallet_id = request.GET.get('wallet')
    for wallet in wallets:
        if str(wallet.id) == wallet_id:
            return wallet
    return wallets[0] if wallets else None


@login_required
def dashboard(request):
    wallets = _present_wallets(WalletService().list_wallets(request.user))
    history = TransactionService().history(request.user, {}, 1)
    transactions = _present_transactions(request.user, history.items[:5])
    rates = ExchangeRate.get_rates()
    total_pkr = Decimal('0')
    for wallet in wallets:
        rate = rates.get(wallet.currency)
        if rate is None:
            continue
        total_pkr += wallet.balance * rate
    currencies = list(settings.SUPPORTED_CURRENCIES)
    selected = (request.GET.get('currency') or '').upper()
    if selected not in currencies:
        selected = settings.DEFAULT_WALLET_CURRENCY
    total_balance = total_pkr / rates[selected]
    return render(request, 'wallet/dashboard.html', {
        'wallets': wallets,
        'transactions': transactions,
        'total_balance': total_balance,
        'total_pkr': total_pkr,
        'selected_currency': selected,
        'currencies': currencies,
    })


@login_required
def transfer(request):
    wallets = _present_wallets(WalletService().list_wallets(request.user))
    return render(request, 'wallet/transfer.html', {
        'wallets': wallets,
        'selected_wallet': _selected_wallet(request, wallets),
        'recipient_wallets': _present_wallets(WalletService().list_recipient_wallets(request.user)),
    })


@login_required
def deposit(request):
    wallets = _present_wallets(WalletService().list_wallets(request.user))
    return render(request, 'wallet/deposit.html', {
        'wallets': wallets,
        'selected_wallet': _selected_wallet(request, wallets),
    })


@login_required
def withdraw(request):
    wallets = _present_wallets(WalletService().list_wallets(request.user))
    return render(request, 'wallet/withdraw.html', {
        'wallets': wallets,
        'selected_wallet': _selected_wallet(request, wallets),
    })


@login_required
def transactions(request):
    params = request.GET.copy()
    direction = params.pop('direction', [''])[0]
    if direction not in {'incoming', 'outgoing'}:
        direction = ''
    serializer = HistoryFilterSerializer(data=params)
    if serializer.is_valid(raise_exception=False):
        filters = dict(serializer.validated_data)
    else:
        filters = {}
    page = filters.pop('page', 1)
    if direction:
        filters['direction'] = direction
    history = TransactionService().history(request.user, filters, page)
    total_pages = max((history.total + 19) // 20, 1)
    echo = serializer.validated_data if serializer.is_valid() else {}
    return render(request, 'wallet/transactions.html', {
        'transactions': _present_transactions(request.user, history.items),
        'history': history,
        'page_number': int(page),
        'total_pages': total_pages,
        'has_previous': int(page) > 1,
        'previous_page_number': max(int(page) - 1, 1),
        'next_page_number': int(page) + 1 if int(page) < total_pages else None,
        'active_filters': any(echo.get(field) not in (None, '') for field in (
            'type', 'status', 'reference', 'date_from', 'date_to', 'amount_min', 'amount_max')) or bool(direction),
        'direction': direction,
        'type': echo.get('type', ''),
        'status': echo.get('status', ''),
        'reference': echo.get('reference', ''),
        'date_from': echo.get('date_from', ''),
        'date_to': echo.get('date_to', ''),
        'amount_min': echo.get('amount_min', ''),
        'amount_max': echo.get('amount_max', ''),
    })


@login_required
def admin_console(request):
    if request.user.role != User.Role.ADMIN:
        return render(request, 'wallet/admin_console.html', {
            'users': [],
            'wallets': [],
            'admin_transactions': [],
            'admin_status': None,
        }, status=403)
    service = AdminWalletService()
    status_filter = request.GET.get('status')
    if status_filter not in Transaction.Status.values:
        status_filter = None
    history = service.list_all_transactions(request.user, {'status': status_filter} if status_filter else None, 1)
    return render(request, 'wallet/admin_console.html', {
        'users': list(service.list_users(request.user)),
        'wallets': _present_wallets(service.list_wallets(request.user)),
        'admin_transactions': _present_transactions(request.user, history.items, admin=True),
        'admin_status': status_filter,
    })


class WalletLoginView(LoginView):
    template_name = 'wallet/login.html'


login = WalletLoginView.as_view()
logout = LogoutView.as_view()


class WalletRegistrationForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(min_length=8)
    password_confirm = forms.CharField()

    def clean_username(self):
        username = self.cleaned_data['username']
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('A user with that username already exists.')
        return username

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('password') != cleaned_data.get('password_confirm'):
            self.add_error('password_confirm', 'Passwords do not match.')
        return cleaned_data


def register(request):
    form = WalletRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = get_user_model().objects.create_user(
                role=User.Role.CUSTOMER,
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password'],
            )
            WalletService().create_wallet(user, settings.DEFAULT_WALLET_CURRENCY)
        return redirect('login')
    return render(request, 'wallet/register.html', {'form': form})


@login_required
def open_wallet(request):
    """Open-wallet page: pick a supported currency from a dropdown."""
    wallets = WalletService().list_wallets(request.user)
    return render(request, 'wallet/open_wallet.html', {
        'supported_currencies': list(settings.SUPPORTED_CURRENCIES),
        'existing_currencies': [w.currency for w in wallets],
    })


@login_required
def wallet_create(request):
    """Opens a wallet for the given 3-letter currency code (page form)."""
    if request.method != 'POST':
        return redirect('dashboard')
    currency = (request.POST.get('currency') or '').strip().upper()
    if currency not in settings.SUPPORTED_CURRENCIES:
        messages.error(request, f'Unsupported currency: {currency}. Choose one of: {", ".join(settings.SUPPORTED_CURRENCIES)}.')
        return redirect('dashboard')
    try:
        WalletService().create_wallet(request.user, currency)
        messages.success(request, f'{currency} wallet opened.')
    except (InvalidAmount, BusinessRuleError) as exc:
        messages.error(request, str(exc))
    return redirect('dashboard')
