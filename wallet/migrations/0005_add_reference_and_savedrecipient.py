from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('wallet', '0004_exchange_rate'),
    ]

    operations = [
        migrations.AddField(
            model_name='wallet',
            name='reference_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.CreateModel(
            name='SavedRecipient',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sender_wallet', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='saved_recipients', to='wallet.wallet')),
                ('recipient_wallet', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='+', to='wallet.wallet')),
            ],
            options={
                'indexes': [models.Index(fields=['sender_wallet', 'created_at'])],
            },
        ),
        migrations.AddConstraint(
            model_name='savedrecipient',
            constraint=models.UniqueConstraint(fields=['sender_wallet', 'recipient_wallet'], name='uniq_sender_recipient'),
        ),
    ]
