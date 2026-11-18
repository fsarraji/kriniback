FROM python:3.12-slim

# Dépendances système (PDF, images, PostgreSQL)
RUN apt-get update && apt-get install -y \
    pkg-config \
    python3-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2-dev \
    libffi-dev \
    libjpeg-dev \
    libopenjp2-7-dev \
    libpng-dev \
    fonts-liberation \
    fontconfig \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Update fonts cache
RUN fc-cache -f -v

# Set working directory
WORKDIR /app

# Install Python Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Fichiers statiques (admin Django) servis par WhiteNoise. La clé ci-dessous ne sert
# qu'au build ; la vraie DJANGO_SECRET_KEY est fournie à l'exécution.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

# Exécution sans privilèges root
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

# Les migrations ne tournent plus au démarrage (risque de course avec plusieurs
# instances) : les lancer comme étape de release (`python manage.py migrate --noinput`).
# Render injecte le port via $PORT ; 1 worker + threads pour tenir dans 512 Mo de RAM.
CMD ["sh", "-c", "gunicorn car_rental_backend.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 120"]
