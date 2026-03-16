FROM python:3.11-slim

# Install ffmpeg (with libass for subtitle burning), fonts, fontconfig, and CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    libass-dev \
    fonts-noto-cjk \
    fontconfig \
    ca-certificates \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
