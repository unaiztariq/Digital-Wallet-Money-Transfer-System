import hashlib
import json
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone

from wallet.models import IdempotencyRecord


class IdempotencyMiddleware:
    """Replay completed JSON POST responses for the same key and payload."""
    lock_timeout = timedelta(minutes=5)
    ttl = timedelta(hours=24)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key = request.headers.get('Idempotency-Key')
        if request.method != 'POST' or not request.path.startswith('/api/') or not key:
            return self.get_response(request)

        payload_hash = hashlib.sha256(request.body).hexdigest()
        now = timezone.now()

        # Serialize on the key: locking the row (or the index gap) by key alone
        # makes the check-then-act atomic and lets us detect payload mismatches
        # (same key, different payload) which the (key, payload_hash) unique
        # constraint alone cannot distinguish from a concurrent duplicate.
        with transaction.atomic():
            record = IdempotencyRecord.objects.select_for_update().filter(idempotency_key=key).first()
            if record is None:
                IdempotencyRecord.objects.filter(created_at__lt=now - self.ttl).delete()
                try:
                    record = IdempotencyRecord.objects.create(
                        idempotency_key=key, payload_hash=payload_hash, locked_at=now)
                except IntegrityError:
                    return JsonResponse({'detail': 'Request with this idempotency key is in progress', 'code': 409}, status=409)
            elif record.payload_hash != payload_hash:
                return JsonResponse({'detail': 'Idempotency-Key already used with a different payload', 'code': 400}, status=400)
            elif record.response_status is not None:
                return JsonResponse(record.response_body, safe=not isinstance(record.response_body, list),
                                    status=record.response_status)
            elif record.locked_at and record.locked_at >= now - self.lock_timeout:
                return JsonResponse({'detail': 'Request with this idempotency key is in progress', 'code': 409}, status=409)
            else:
                # Stale lock (previous attempt crashed): reclaim it.
                record.locked_at = now
                record.save(update_fields=['locked_at'])

        try:
            response = self.get_response(request)
        except Exception:
            # 500-level failure: never store a response for it, free the key so
            # the client can retry immediately.
            record.delete()
            raise

        if 200 <= response.status_code < 500:
            try:
                body = json.loads(response.content.decode(response.charset or 'utf-8'))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                body = {'detail': 'Response could not be replayed'}
            record.response_status = response.status_code
            record.response_body = body
            record.locked_at = None
            record.save(update_fields=['response_status', 'response_body', 'locked_at'])
        else:
            record.delete()
        return response
