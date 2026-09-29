FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY pyproject.toml constraints.txt README.md ./
COPY src ./src
COPY scripts ./scripts
COPY evaluation ./evaluation
RUN pip install --no-cache-dir --constraint constraints.txt . && useradd --create-home app
COPY dataset_v2 ./dataset_v2
COPY schema ./schema
USER app
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
