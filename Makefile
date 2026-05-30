.PHONY: bootstrap install-web install-py build-web dev-web dev-py start-py clean check-web check-py check

## Installation

bootstrap: install-web install-py

install-web:
	cd components/web && pnpm install

install-py:
	cd components/python && uv sync --dev

## Web build

build-web:
	cd components/web && pnpm build

dev-web:
	cd components/web && pnpm build --watch

## Development

dev-py:
	@echo "Starting web build watcher and Python server..."
	@trap 'kill 0' EXIT; \
		(cd components/web && pnpm build --watch) & \
		sleep 2 && cd components/python && uv run src/main.py

start-py: build-web
	cd components/python && uv run src/main.py

## Utilities

clean:
	rm -rf components/web/dist
	rm -rf components/web/node_modules
	rm -rf components/python/.venv
	rm -rf components/python/.uv-cache
	rm -rf components/python/.ruff_cache
	rm -rf components/python/.pytest_cache

check-web:
	cd components/web && pnpm check

check-py:
	cd components/python && uv run pytest

check: check-web check-py
