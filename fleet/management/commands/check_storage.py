from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

import os
import uuid


class Command(BaseCommand):
    help = "Vérifie quel backend de stockage est actif et fait un test d'écriture réel."

    def handle(self, *args, **options):
        self.stdout.write("=== Configuration du stockage ===")
        self.stdout.write(
            f"Backend actif (default_storage) : "
            f"{default_storage.__class__.__module__}.{default_storage.__class__.__name__}"
        )
        self.stdout.write(f"MEDIA_URL  : {getattr(settings, 'MEDIA_URL', '(non défini -> S3)')}")
        self.stdout.write(f"MEDIA_ROOT : {getattr(settings, 'MEDIA_ROOT', '(non défini -> S3)')}")

        has_s3 = bool(
            os.getenv('AWS_ACCESS_KEY_ID')
            and os.getenv('AWS_SECRET_ACCESS_KEY')
            and os.getenv('AWS_STORAGE_BUCKET_NAME')
        )
        self.stdout.write(f"Variables S3/Supabase définies : {'OUI' if has_s3 else 'NON'}")
        if has_s3:
            bucket = os.getenv('AWS_STORAGE_BUCKET_NAME')
            endpoint = os.getenv('AWS_S3_ENDPOINT_URL')
            self.stdout.write(f"  AWS_STORAGE_BUCKET_NAME : {bucket}")
            self.stdout.write(f"  AWS_S3_ENDPOINT_URL     : {endpoint}")
            if bucket and endpoint:
                self.stdout.write(f"  Console Supabase        : https://supabase.com/dashboard/project/{bucket.split('-')[0]}/storage/buckets")

        self.stdout.write("")
        self.stdout.write("=== Test d'écriture réel ===")
        name = f"storage_check/{uuid.uuid4().hex}.txt"
        try:
            path = default_storage.save(name, ContentFile(b"krini storage check"))
            url = default_storage.url(path)
            self.stdout.write(self.style.SUCCESS(f"Fichier écrit : {path}"))
            self.stdout.write(self.style.SUCCESS(f"URL publique  : {url}"))
            default_storage.delete(path)
            self.stdout.write(self.style.SUCCESS("Fichier test supprimé (cleanup OK)."))
            self.stdout.write("")
            if has_s3:
                self.stdout.write(self.style.SUCCESS("=> Stockage actif : SUPABASE STORAGE (S3). Les images persisteront."))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "=> Stockage actif : DISQUE LOCAL. "
                        "Sur Render ce disque est éphémère -> images perdues à chaque redéploiement. "
                        "Configurez les variables AWS_* pour utiliser Supabase Storage."
                    )
                )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Erreur lors du test d'écriture : {exc}"))
