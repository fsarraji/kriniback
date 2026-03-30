import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'car_rental_backend.settings')
django.setup()

from agency.models import CustomUser

username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
email = os.getenv('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
password = os.getenv('DJANGO_SUPERUSER_PASSWORD', 'admin123')

if not CustomUser.objects.filter(username=username).exists():
    CustomUser.objects.create_superuser(username=username, email=email, password=password)
    print(f"Superuser '{username}' created successfully.")
else:
    print(f"Superuser '{username}' already exists.")
