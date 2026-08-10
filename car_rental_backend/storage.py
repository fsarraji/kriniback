"""Backends de stockage qui optimisent les images à l'écriture.

Les images passent par Pillow (orientation EXIF, redimensionnement,
compression) avant d'être écrites sur le disque local ou sur Supabase S3.
"""

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from storages.backends.s3boto3 import S3Boto3Storage

from .images import optimize_image_bytes


def _optimize_or_pass(name, content):
    """Optimise le contenu si c'est une image, sinon le renvoie tel quel."""
    optimized = optimize_image_bytes(name, content)
    if optimized is None:
        return content
    optimized.seek(0)
    return ContentFile(optimized.read(), name=name)


class OptimizedFileSystemStorage(FileSystemStorage):
    def _save(self, name, content):
        content = _optimize_or_pass(name, content)
        return super()._save(name, content)


class OptimizedS3Storage(S3Boto3Storage):
    def _save(self, name, content):
        content = _optimize_or_pass(name, content)
        return super()._save(name, content)
