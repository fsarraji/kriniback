"""Validateurs réutilisables pour les fichiers téléversés."""

from django.core.exceptions import ValidationError

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 Mo


def validate_image_size(f):
    """Refuse les images de plus de 5 Mo (le type est déjà vérifié par Pillow/ImageField)."""
    if f is not None and getattr(f, "size", 0) > MAX_IMAGE_BYTES:
        raise ValidationError(
            "Image trop volumineuse (maximum %(max)s Mo).",
            code="file_too_large",
            params={"max": MAX_IMAGE_BYTES // (1024 * 1024)},
        )
