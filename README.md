# Advance-Text2SQL

Skeleton Python 3.11+ cho Vietnamese NLP Text2SQL không dùng LLM.
Stack: FastAPI, PostgreSQL, SQLAlchemy 2, Pydantic 2, pytest.
Không dùng LangChain, LangGraph hoặc API AI.

## 1. Setup environment

Chạy từ thư mục gốc repository:

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cp .env.example .env
# PowerShell: Copy-Item .env.example .env
```

Điền `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD` trong `.env`. Mặc định dành cho local development;
đổi mật khẩu khi triển khai. Biến môi trường ưu tiên hơn `.env`.
SQLAlchemy URL xử lý mật khẩu có ký tự đặc biệt.

## 2. Chạy Docker Compose

```bash
docker compose up --build -d
docker compose logs -f api
docker compose down
```

API: http://localhost:8000, Swagger: http://localhost:8000/docs.
PostgreSQL dùng named volume giữ dữ liệu và chỉ publish trên localhost.
API container dùng host `db`, port `5432`; Compose đợi DB healthy.

## 3. Chạy API local

Chạy database bằng Docker rồi chạy API trong virtual environment:

```bash
docker compose up -d db
python -m uvicorn src.main:app --reload
```

Nếu API container đang chạy, dùng `docker compose stop api` trước khi chạy
API local cùng port 8000.

`GET /health` trả `{"status":"ok"}`. Đây là liveness, không kiểm tra DB.
`POST /query` nhận:

```json
{
  "question": "Liệt kê nhân viên ở chi nhánh Hà Nội",
  "user": {
    "user_id": "demo",
    "role": "viewer",
    "department_id": null,
    "branch_id": null,
    "team_id": null
  }
}
```

Response HTTP 200 theo `QueryResult`:

```json
{
  "columns": ["question"],
  "rows": [{"question": "Liệt kê nhân viên ở chi nhánh Hà Nội"}],
  "row_count": 1,
  "sql": null,
  "is_mock": true,
  "message": "Mock response; NLP and SQL execution are not implemented."
}
```

Mock không kết nối DB, không sinh SQL, không thực thi phân quyền.
`user` trong body chỉ là contract demo; cần lấy danh tính từ authentication
trước khi nối pipeline thật. Input không hợp lệ trả HTTP 422.

## 4. Chạy test

```bash
python -m pytest -q
```

Smoke tests kiểm tra startup, health, mock query, QueryIR và cấu hình DB
từ environment. Bộ test hiện tại không cần DB và chưa kiểm tra kết nối DB thật.

Các script skeleton:

```bash
python -m scripts.init_db
python -m scripts.generate_data
python -m evaluation.evaluate
```

`init_db` cần PostgreSQL và tạo các ORM table đã đăng ký (hiện chưa có bảng).
Hai script còn lại là TODO, chưa tạo dữ liệu hoặc tính điểm đánh giá.
Nếu có GNU Make: `make install`, `make run`, `make test`, `make up`.

## Cấu trúc và phát triển

- `src/api/`: routes và dependency injection.
- `src/nlp/`: normalizer, intent, entity, date parser.
- `src/schema/`: registry, linker, relationship graph.
- `src/query/`: QueryIR, planner, join resolver, SQL builder.
- `src/permission/`: context, column/row policy, resolver.
- `src/database/`: engine/session, ORM base, executor, schema loader.
- `src/validation/`, `src/response/`: SQL validator và formatter.
- `src/shared/`: enums, domain exceptions, models dùng chung.
- `schema/`, `scripts/`, `evaluation/`: schema spec và placeholder.
- `tests/unit/`, `tests/integration/`: model/config và API smoke tests.

Xem [docs/architecture.md](docs/architecture.md) để chia việc cho hai developer.
Interfaces dùng ABC; nghiệp vụ để TODO. Dependencies giới hạn major version;
chốt lockfile và bổ sung migration, authentication, logging trước khi triển khai thật.
