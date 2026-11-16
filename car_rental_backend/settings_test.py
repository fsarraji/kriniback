"""Réglages de test : SQLite local, jamais la base de production (Supabase).

Usage : python manage.py test --settings=car_rental_backend.settings_test
"""
import os

os.environ["DJANGO_DEBUG"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
for _k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_STORAGE_BUCKET_NAME"):
    os.environ[_k] = ""

from .settings import *  # noqa: E402,F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
