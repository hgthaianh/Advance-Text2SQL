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

Update PostgreSQL credentials and set a unique `AUTH_SECRET_KEY` of at least 32 characters in `.env`.

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
`GET /health` checks process liveness and `GET /health/ready` verifies PostgreSQL readiness. `POST /query` runs the deterministic pipeline.
Authentication uses `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, and `GET /auth/me`.
The default provider verifies active users and password hashes from the IAM schema; it does not ship demo credentials.

## Test

```bash
python -m pytest -q
```

## Stop Docker services

```bash
docker compose down
```
