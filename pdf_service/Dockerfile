FROM python:3.12-slim

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
    && rm -rf /var/lib/apt/lists/*

RUN fc-cache -f -v

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
