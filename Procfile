release: python manage.py migrate --noinput
web: gunicorn car_rental_backend.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 120
