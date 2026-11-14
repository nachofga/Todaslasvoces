# Alternativa a Render: sirve para Railway, Fly.io o un servidor propio.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TLV_MEDIA_ROOT=/datos/media
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN SECRET_KEY=build TLV_ALLOW_DEV_SECRET=1 python manage.py collectstatic --noinput
VOLUME /datos
EXPOSE 8000
CMD ["./start.sh"]
