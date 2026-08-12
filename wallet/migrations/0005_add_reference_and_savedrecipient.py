from django.db import migrations, models
import uuid


def _populate_reference_ids(apps, schema_editor):
    Wallet = apps.get_model('wallet', 'Wallet')
    seen = set()
    for w in Wallet.objects.all().order_by('id'):
        cur = getattr(w, 'reference_id', None)
        if cur is None or cur in seen:
            # generate a new unique UUID not already seen
            new = uuid.uuid4()
            while new in seen:
                new = uuid.uuid4()
            w.reference_id = new
            w.save(update_fields=['reference_id'])
            seen.add(w.reference_id)
        else:
            seen.add(cur)


class Migration(migrations.Migration):

    dependencies = [
        ('wallet', '0004_exchange_rate'),
    ]

    operations = [
        # Add the column nullable first (no unique constraint/no default so existing rows
        # are not all assigned the same UUID).
        migrations.AddField(
            model_name='wallet',
            name='reference_id',
            field=models.UUIDField(null=True, editable=False),
        ),

        # Create SavedRecipient model.
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

        # Populate unique UUIDs per existing wallet row.
        migrations.RunPython(_populate_reference_ids, reverse_code=migrations.RunPython.noop),

        # Make the field non-nullable and unique now that values are unique.
        migrations.AlterField(
            model_name='wallet',
            name='reference_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
