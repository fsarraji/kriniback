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


MAX_SIGNED_CONTRACT_BYTES = 10 * 1024 * 1024  # 10 Mo
_SIGNED_MAGIC = {
    ".pdf": (b"%PDF",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG",),
}


def validate_signed_contract(f):
    """Contrat signé : PDF/JPG/PNG de 10 Mo max, type vérifié sur l'en-tête du fichier."""
    import os

    ext = os.path.splitext(getattr(f, "name", "") or "")[1].lower()
    if ext not in _SIGNED_MAGIC:
        raise ValidationError(
            "Format non accepté : PDF, JPG ou PNG uniquement.", code="bad_type"
        )
    if getattr(f, "size", 0) > MAX_SIGNED_CONTRACT_BYTES:
        raise ValidationError(
            "Fichier trop volumineux (maximum 10 Mo).", code="file_too_large"
        )
    head = f.read(8)
    f.seek(0)
    if not any(head.startswith(m) for m in _SIGNED_MAGIC[ext]):
        raise ValidationError(
            "Le contenu ne correspond pas à l'extension du fichier.", code="bad_content"
        )
