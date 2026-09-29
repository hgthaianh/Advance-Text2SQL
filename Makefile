.PHONY: install run test up down init-db provision
install:
	python -m pip install -e ".[dev]"
run:
	python -m uvicorn src.main:app --reload
test:
	python -m pytest
up:
	docker compose up --build -d
down:
	docker compose down
init-db:
	python -m scripts.init_db --reset
provision: init-db
