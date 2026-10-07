FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
# Import channel history: docker compose exec bot python -m scripts.import_channel_export /data/result.json
COPY scripts/import_channel_export.py ./scripts/

RUN useradd --system --uid 10001 --no-create-home botuser
USER botuser

CMD ["python", "-m", "bot.main"]
