from wallet.models import User


def get_user_by_id(user_id):
    return User.objects.filter(pk=user_id).first()


def get_user_by_email(email):
    return User.objects.filter(email=email).first()
