# Advance-Text2SQL

Requires Python 3.11+ and Docker Compose.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
```

Update PostgreSQL credentials in `.env` if needed.

## Run with Docker

```bash
docker compose up --build -d
```

## Run locally

```bash
docker compose up -d db
docker compose stop api
python -m uvicorn src.main:app --reload
```

API: http://localhost:8000. Swagger: http://localhost:8000/docs.
`GET /health` checks liveness. `POST /query` runs the deterministic pipeline.

## Test

```bash
python -m pytest -q
```

## Stop Docker services

```bash
docker compose down
```
