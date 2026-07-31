FROM python:3.12-slim

# Install system dependencies required for WeasyPrint and PostgreSQL
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

# Expose port and start Gunicorn (Render injects the port dynamically via $PORT)
# Optimization for 512MB RAM: Use 1 worker and multiple threads to reduce memory footprint.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn car_rental_backend.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 120"]
