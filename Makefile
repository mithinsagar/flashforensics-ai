.PHONY: help install fixture analyze bench test lint serve web mcp docker clean

help:
	@echo "FlashForensics AI"
	@echo ""
	@echo "  make install    install backend (editable) and frontend dependencies"
	@echo "  make fixture    generate a damaged FAT32 test card with ground truth"
	@echo "  make analyze    run the pipeline on the fixture"
	@echo "  make bench      score recovery against ground truth"
	@echo "  make test       run the pytest suite"
	@echo "  make lint       ruff check the backend, typecheck the frontend"
	@echo "  make serve      start the API on :8000"
	@echo "  make web        start the dashboard on :3000"
	@echo "  make mcp        start the MCP server on stdio"
	@echo "  make docker     bring the whole stack up with compose"

install:
	cd backend && pip install -e ".[dev]"
	cd frontend && npm install

fixture:
	cd backend && python tools/make_fixture.py --output fixtures/card.img --size-mb 128

analyze:
	cd backend && flashforensics analyze fixtures/card.img

bench:
	cd backend && python tools/benchmark.py --image fixtures/card.img --json-out fixtures/benchmark.json

test:
	cd backend && pytest

lint:
	cd backend && ruff check flashforensics tools
	cd frontend && npx tsc --noEmit

serve:
	cd backend && flashforensics serve

web:
	cd frontend && npm run dev

mcp:
	cd backend && flashforensics-mcp

docker:
	docker compose up --build

clean:
	rm -rf backend/fixtures/*.img backend/fixtures/*.truth.json
	rm -rf backend/.pytest_cache backend/**/__pycache__
	rm -rf frontend/.next
