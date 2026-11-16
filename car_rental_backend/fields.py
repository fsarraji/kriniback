"""Champs de modèle utilitaires."""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

_PREFIX = "enc::"


def _fernet():
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        # Repli : clé dérivée de SECRET_KEY (changer SECRET_KEY rend les valeurs
        # illisibles — définir FIELD_ENCRYPTION_KEY en production).
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


class EncryptedCharField(models.CharField):
    """CharField chiffré au repos (Fernet).

    Les anciennes valeurs en clair (sans préfixe) restent lisibles et sont
    chiffrées à la prochaine sauvegarde.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value or str(value).startswith(_PREFIX):
            return value
        return _PREFIX + _fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value or not value.startswith(_PREFIX):
            return value
        try:
            return _fernet().decrypt(value[len(_PREFIX) :].encode()).decode()
        except InvalidToken:
            logger.error("encrypted_field_decrypt_failed")
            return None
