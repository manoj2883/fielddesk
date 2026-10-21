# Recipes use `uv run` so they work the same in Git Bash on Windows and in Linux CI.
.PHONY: install lint fmt test test-live gen-sample gen-data validate-data baseline serve

install:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest --cov --cov-report=term-missing

test-live:
	RUN_LIVE=1 uv run pytest -m live --force-enable-socket -p no:cacheprovider

gen-sample:
	uv run python -m data.generator.cli sample --n 10

gen-data:
	uv run python -m data.generator.cli generate

validate-data:
	uv run python -m data.generator.cli validate

baseline:
	uv run python -m evals.baseline --split dev

serve:
	uv run uvicorn fielddesk.api:app --reload
