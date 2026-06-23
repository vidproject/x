default:
    @just --list

setup:
    uv sync
    npm install

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    npm run lint
    npm run typecheck
    npx prettier --check .

fmt:
    uv run ruff format .
    npm run format

test:
    uv run pytest -q

build-extension:
    npm run build:extension

ingest:
    uv run python -m scripts.ingest

clean:
    rm -rf extension/dist extension.zip .mypy_cache .ruff_cache .pytest_cache
