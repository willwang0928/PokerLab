.PHONY: install backend frontend dev test lint

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd frontend && npm install

backend:
	cd backend && .venv/bin/python -m pokerlab

frontend:
	cd frontend && npm run dev

# Run API and UI together (Ctrl+C stops both).
dev:
	@trap 'kill 0' INT TERM; \
	(cd backend && .venv/bin/python -m pokerlab) & \
	(cd frontend && npm run dev) & \
	wait

test:
	cd backend && .venv/bin/pytest -q
	cd frontend && npm test

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run typecheck
