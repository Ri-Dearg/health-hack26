import hashlib
import hmac

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


def hash_card_id(card_id: str) -> str:
    """Keyed hash of a keycard UID, so the raw card number is never stored.

    Changing SECRET_KEY invalidates every stored hash (cards must be re-enrolled).
    """
    normalised = card_id.strip().upper().replace(":", "").replace(" ", "")
    return hmac.new(settings.SECRET_KEY.encode(), normalised.encode(), hashlib.sha256).hexdigest()


class Staff(AbstractUser):
    """An OT staff member. Admins are superusers; everyone else is in the "OT staff" group."""

    staff_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    card_id_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False,
        help_text="Keyed hash of the keycard UID. Set via the 'Keycard ID' field.",
    )

    class Meta:
        verbose_name = "staff member"
        verbose_name_plural = "staff"

    def __str__(self):
        return self.get_full_name() or self.username

    def set_card_id(self, card_id: str):
        self.card_id_hash = hash_card_id(card_id) if card_id else None

    @classmethod
    def from_card_tap(cls, card_id: str):
        return cls.objects.filter(card_id_hash=hash_card_id(card_id), is_active=True).first()
