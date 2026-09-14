.PHONY: install test lint run validate import docker

install:
	pip install -r requirements-dev.txt

test:
	python -m pytest -q

lint:
	ruff check app scripts tests

validate:
	python scripts/validate_content.py content

run:
	uvicorn app.main:app --reload --port 8080

import:
	python scripts/import_registry.py "$(XLSX)" --out content/cards.yaml

docker:
	docker compose up --build -d
