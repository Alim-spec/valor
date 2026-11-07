# Valor Fund Administration — application image (gunicorn + WhiteNoise for static files)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Almaty

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Static files are built into the image (URL_PREFIX must match the one used at runtime)
ARG URL_PREFIX=/valor-test
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build-only URL_PREFIX=${URL_PREFIX} \
    python manage.py collectstatic --noinput -v 0 \
    && useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=2", "--timeout=60", "--access-logfile=-"]
