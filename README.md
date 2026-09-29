---
title: Advance Text2SQL
sdk: docker
app_port: 8000
base_path: /docs
---

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
The default provider verifies active users and password hashes from the IAM schema. The local Docker seed
includes synthetic test accounts; startup, credentials, API examples, and role-based test cases are in
[docs/usage_and_api_testing.md](docs/usage_and_api_testing.md).

## Documentation

- [System Architecture & Reference (Tiếng Việt)](docs/system_reference_vi.md): Toàn bộ kiến trúc, thư viện, modules, class, hàm, luồng dữ liệu 12 giai đoạn và phân tích chuyên sâu các thuật toán (Levenshtein, Trigram Index, Dijkstra, Fanout Pre-aggregation, Parameterized AST).
- [Architecture Overview](docs/architecture.md): Request path and design constraints.
- [Production Pipeline Bridge](docs/production_pipeline.md): Pipeline orchestration and integration.
- [Query Planning Engine](docs/query_planning.md): QueryIR, join planning, and fan-out prevention.
- [Schema Linking](docs/schema_linking.md): Lexical linking, ranking, and alias vocabulary.
- [Pipeline Operations](docs/pipeline_operations.md): Scoping, observability, and evaluation benchmarks.
- [API Usage & Testing](docs/usage_and_api_testing.md): Auth endpoints, credentials, and test queries.

## Test

```bash
python -m pytest -q
```

## Stop Docker services

```bash
docker compose down
```
