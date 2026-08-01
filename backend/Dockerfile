FROM node:22-slim AS flyai-runtime

COPY backend/node-requirements.txt /tmp/node-requirements.txt
RUN xargs npm install -g < /tmp/node-requirements.txt

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=flyai-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=flyai-runtime /usr/local/bin/flyai /usr/local/bin/flyai
COPY --from=flyai-runtime /usr/local/lib/node_modules/@fly-ai /usr/local/lib/node_modules/@fly-ai
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app /app/app
COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic
COPY shared /shared
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
