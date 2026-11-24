FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py ./wsgi.py

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/instance /app/app/static/uploads \
    && chown -R appuser:appuser /app

USER appuser

CMD ["sh", "-c", "gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-8080} wsgi:app"]
