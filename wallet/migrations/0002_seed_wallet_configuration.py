from decimal import Decimal

from django.db import migrations


def seed_wallet_configuration(apps, schema_editor):
    WalletConfiguration = apps.get_model('wallet', 'WalletConfiguration')
    WalletConfiguration.objects.get_or_create(
        pk=1,
        defaults={
            'max_single_deposit': Decimal('100000.0000'),
            'max_single_withdrawal': Decimal('50000.0000'),
            'max_daily_withdrawal': Decimal('100000.0000'),
            'max_single_transfer': Decimal('100000.0000'),
            'max_daily_transfer': Decimal('500000.0000'),
        },
    )


def unseed_wallet_configuration(apps, schema_editor):
    apps.get_model('wallet', 'WalletConfiguration').objects.filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [('wallet', '0001_initial')]

    operations = [migrations.RunPython(seed_wallet_configuration, unseed_wallet_configuration)]
