"""Domain errors whose numeric codes map to API status codes later."""


class BusinessRuleError(Exception):
    code = 400

    def __init__(self, message='Business rule violation', code=None):
        self.code = self.code if code is None else code
        self.message = message
        super().__init__(message)


class WalletNotFound(BusinessRuleError):
    code = 404


class WalletFrozen(BusinessRuleError):
    code = 403


class InvalidAmount(BusinessRuleError):
    code = 400


class InsufficientBalance(BusinessRuleError):
    code = 400


class SelfTransfer(BusinessRuleError):
    code = 400


class LimitExceeded(BusinessRuleError):
    code = 400

    def __init__(self, limit):
        super().__init__(f'{limit} exceeded')


class DuplicateTransaction(BusinessRuleError):
    code = 409


class InvalidStateTransition(BusinessRuleError):
    code = 409


class PermissionDenied(BusinessRuleError):
    code = 403
