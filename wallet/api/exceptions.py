from rest_framework.response import Response
from rest_framework.views import exception_handler

from wallet.repositories.errors import (BusinessRuleError, DuplicateTransaction, InsufficientBalance, InvalidAmount,
    InvalidStateTransition, LimitExceeded, PermissionDenied, SelfTransfer, WalletFrozen, WalletNotFound)


STATUS_CODES = {
    WalletNotFound: 404, WalletFrozen: 403, InvalidAmount: 400,
    InsufficientBalance: 400, SelfTransfer: 400, LimitExceeded: 400,
    DuplicateTransaction: 409, InvalidStateTransition: 409, PermissionDenied: 403,
    # Catch-all: any domain rule error not mapped above (e.g. the plain
    # BusinessRuleError raised on duplicate wallet creation) is a 400, not a 500.
    BusinessRuleError: 400,
}


def wallet_exception_handler(exc, context):
    for error_type, status_code in STATUS_CODES.items():
        if isinstance(exc, error_type):
            return Response({'detail': str(exc), 'code': exc.code}, status=status_code)
    return exception_handler(exc, context)
