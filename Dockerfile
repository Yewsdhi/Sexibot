FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir poetry

COPY pyproject.toml ./
COPY README.md ./
COPY . .

RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi --no-root || \
    pip install --no-cache-dir fastapi uvicorn python-telegram-bot python-dotenv requests sqlalchemy python-dateutil pydantic

EXPOSE 8000

CMD ["python", "heroku.py"]
