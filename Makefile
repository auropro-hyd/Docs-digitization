# ═══════════════════════════════════════════════════════════════════
# Auto Transcription Platform — Makefile
# ═══════════════════════════════════════════════════════════════════
#
# Usage:  make <target>
# Help:   make help
#
# ═══════════════════════════════════════════════════════════════════

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

# ── Directories ──────────────────────────────────────────────────
BACKEND_DIR  := backend
FRONTEND_DIR := frontend
VENV_DIR     := $(BACKEND_DIR)/.venv

# ── OS Detection ─────────────────────────────────────────────────
# $(OS) is set to Windows_NT on Windows; empty on macOS/Linux.
# Windows users should run make from Git Bash, MSYS2, or WSL.
ifeq ($(OS),Windows_NT)
    DETECTED_OS  := Windows
    VENV_BIN     := $(VENV_DIR)/Scripts
    VENV_BIN_REL := .venv/Scripts
    PYTHON       := $(VENV_BIN)/python.exe
    PIP          := $(VENV_BIN)/pip.exe
    VENV_REAL    := $(VENV_DIR)
else
    DETECTED_OS  := $(shell uname -s)
    VENV_BIN     := $(VENV_DIR)/bin
    VENV_BIN_REL := .venv/bin
    PYTHON       := $(VENV_BIN)/python
    PIP          := $(VENV_BIN)/pip
    VENV_REAL    := $(HOME)/.venvs/auto-transcription
endif

# ── Phony targets ───────────────────────────────────────────────
.PHONY: help setup venv install install-backend install-frontend \
	dev dev-fresh backend frontend \
	infra-up infra-down infra-restart infra-status infra-logs \
	db-logs db-shell db-reset \
	ollama-up ollama-down ollama-pull ollama-list ollama-logs \
	test test-unit test-integration test-cov test-all \
	lint lint-fix format typecheck lint-frontend check-all \
	build build-backend build-frontend \
	docker-build docker-up docker-down docker-logs docker-restart \
	health process-pdf kill \
	clean deep-clean reset \
	help

# ═════════════════════════════════════════════════════════════════
#  HELP
# ═════════════════════════════════════════════════════════════════

help: ## Show all available targets grouped by section
	@echo ""
	@echo "╔═══════════════════════════════════════════════════════╗"
	@echo "║   Auto Transcription Platform — Make Targets         ║"
	@echo "╚═══════════════════════════════════════════════════════╝"
	@echo ""
	@echo "  SETUP"
	@grep -E '^(setup|venv|install)[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  DEVELOPMENT"
	@grep -E '^(dev|backend|frontend)[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  INFRASTRUCTURE (Docker Compose)"
	@grep -E '^infra-[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  DATABASE"
	@grep -E '^db-[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  OLLAMA (LLM)"
	@grep -E '^ollama-[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  TESTING"
	@grep -E '^test[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  CODE QUALITY"
	@grep -E '^(lint|format|typecheck|check)[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  BUILD & DOCKER"
	@grep -E '^(build|docker)[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  CLEANUP"
	@grep -E '^(clean|deep-clean|reset)[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  QUICK TEST"
	@grep -E '^(health|process-pdf|kill)[a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ═════════════════════════════════════════════════════════════════
#  SETUP — First-time project initialization
# ═════════════════════════════════════════════════════════════════

setup: venv install infra-up ## Full first-time setup (venv + deps + infra)
	@echo ""
	@echo "✓ Setup complete. Run 'make dev' to start developing."

venv: ## Create Python virtual environment
ifeq ($(OS),Windows_NT)
	@if [ -x "$(VENV_BIN)/python.exe" ]; then \
		echo "✓ Virtual environment already exists at $(VENV_DIR)"; \
	else \
		echo "Creating virtual environment..."; \
		rm -rf "$(VENV_DIR)"; \
		uv venv "$(VENV_DIR)" --python python3.13 || python -m venv "$(VENV_DIR)"; \
		echo "✓ Virtual environment created at $(VENV_DIR)"; \
		echo "  Activate with: source $(VENV_BIN)/activate"; \
	fi
else
	@if [ -x "$(VENV_BIN)/python" ]; then \
		echo "✓ Virtual environment already exists at $(VENV_DIR)"; \
	else \
		echo "Creating virtual environment (outside workspace to avoid IDE watcher conflicts)..."; \
		rm -rf "$(VENV_DIR)" "$(VENV_REAL)"; \
		mkdir -p "$(dir $(VENV_REAL))"; \
		uv venv "$(VENV_REAL)" --python python3.13; \
		ln -sfn "$(VENV_REAL)" "$(VENV_DIR)"; \
		echo "✓ Virtual environment created at $(VENV_REAL)"; \
		echo "  Symlinked to $(VENV_DIR)"; \
		echo "  Activate with: source $(VENV_BIN)/activate"; \
	fi
endif

install: install-backend install-frontend ## Install all dependencies (backend + frontend)

install-backend: venv ## Install backend Python dependencies
	uv pip install -p "$(PYTHON)" -e "$(BACKEND_DIR)/.[dev]"

install-frontend: ## Install frontend Node.js dependencies
	cd $(FRONTEND_DIR) && npm install

# ═════════════════════════════════════════════════════════════════
#  DEVELOPMENT — Run services locally
# ═════════════════════════════════════════════════════════════════

dev: ## Start backend + frontend concurrently (Ctrl+C stops both)
	@$(MAKE) backend & $(MAKE) frontend & wait

dev-fresh: ## Clean frontend .next cache and start dev (use if Turbopack/SST errors persist)
	rm -rf $(FRONTEND_DIR)/.next
	$(MAKE) dev

backend: ## Start FastAPI backend (dev mode with auto-reload)
	cd $(BACKEND_DIR) && $(VENV_BIN_REL)/uvicorn app.main:app --reload --reload-exclude '.venv' --host 0.0.0.0 --port 8100

frontend: ## Start Next.js frontend (dev mode)
	cd $(FRONTEND_DIR) && npm run dev -- --port 3100

# ═════════════════════════════════════════════════════════════════
#  INFRASTRUCTURE — Docker Compose services (PostgreSQL + Ollama)
# ═════════════════════════════════════════════════════════════════

infra-up: ## Start PostgreSQL + Ollama containers
	cd $(BACKEND_DIR) && docker compose up -d
	@echo "Waiting for PostgreSQL to be ready..."
	@until docker compose -f $(BACKEND_DIR)/docker-compose.yml exec -T postgres pg_isready -U postgres > /dev/null 2>&1; do \
		sleep 1; \
	done
	@echo "✓ PostgreSQL is ready"
	@echo "✓ Ollama is running at http://localhost:11434"

infra-down: ## Stop all infrastructure containers
	cd $(BACKEND_DIR) && docker compose down

infra-restart: ## Restart all infrastructure containers
	cd $(BACKEND_DIR) && docker compose restart

infra-status: ## Show status of infrastructure containers
	cd $(BACKEND_DIR) && docker compose ps

infra-logs: ## Tail logs from all infrastructure containers
	cd $(BACKEND_DIR) && docker compose logs -f --tail=50

# ═════════════════════════════════════════════════════════════════
#  DATABASE — PostgreSQL management
# ═════════════════════════════════════════════════════════════════

db-logs: ## Tail PostgreSQL logs
	cd $(BACKEND_DIR) && docker compose logs -f postgres

db-shell: ## Open psql shell in the PostgreSQL container
	cd $(BACKEND_DIR) && docker compose exec postgres psql -U postgres -d autotranscription

db-reset: ## Drop and recreate the database (destructive!)
	@echo "WARNING: This will destroy all data in the database."
	@bash -c 'read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]' || exit 1
	cd $(BACKEND_DIR) && docker compose exec postgres psql -U postgres -c "DROP DATABASE IF EXISTS autotranscription;"
	cd $(BACKEND_DIR) && docker compose exec postgres psql -U postgres -c "CREATE DATABASE autotranscription;"
	@echo "✓ Database reset complete"

# ═════════════════════════════════════════════════════════════════
#  OLLAMA — Local LLM management
# ═════════════════════════════════════════════════════════════════

ollama-up: ## Start only the Ollama container
	cd $(BACKEND_DIR) && docker compose up -d ollama

ollama-down: ## Stop only the Ollama container
	cd $(BACKEND_DIR) && docker compose stop ollama

ollama-pull: ## Pull required models (gemma2:9b + gemma2:2b)
	@echo "Pulling Ollama models (this may take a while)..."
	docker compose -f $(BACKEND_DIR)/docker-compose.yml exec ollama ollama pull gemma2:9b || ollama pull gemma2:9b
	docker compose -f $(BACKEND_DIR)/docker-compose.yml exec ollama ollama pull gemma2:2b || ollama pull gemma2:2b
	@echo "✓ Models pulled successfully"

ollama-list: ## List installed Ollama models
	docker compose -f $(BACKEND_DIR)/docker-compose.yml exec ollama ollama list 2>/dev/null || ollama list

ollama-logs: ## Tail Ollama logs
	cd $(BACKEND_DIR) && docker compose logs -f ollama

# ═════════════════════════════════════════════════════════════════
#  TESTING
# ═════════════════════════════════════════════════════════════════

test: test-unit ## Run unit tests (alias)

test-unit: ## Run backend unit tests
	PYTHONPATH=$(BACKEND_DIR) $(VENV_BIN)/pytest $(BACKEND_DIR)/tests/unit -v

test-integration: ## Run backend integration tests (requires infra)
	PYTHONPATH=$(BACKEND_DIR) $(VENV_BIN)/pytest $(BACKEND_DIR)/tests/integration -v

test-cov: ## Run unit tests with coverage report
	PYTHONPATH=$(BACKEND_DIR) $(VENV_BIN)/pytest $(BACKEND_DIR)/tests/unit -v --cov=app --cov-report=term-missing --cov-report=html:$(BACKEND_DIR)/htmlcov

test-all: test-unit test-integration ## Run all tests (unit + integration)

# ═════════════════════════════════════════════════════════════════
#  CODE QUALITY — Linting, formatting, type checking
# ═════════════════════════════════════════════════════════════════

lint: ## Run ruff linter on backend
	$(VENV_BIN)/ruff check $(BACKEND_DIR)/app/ $(BACKEND_DIR)/tests/

lint-fix: ## Auto-fix backend lint issues
	$(VENV_BIN)/ruff check --fix $(BACKEND_DIR)/app/ $(BACKEND_DIR)/tests/

format: ## Auto-format backend code
	$(VENV_BIN)/ruff format $(BACKEND_DIR)/app/ $(BACKEND_DIR)/tests/

format-check: ## Check backend formatting (dry-run, no changes)
	$(VENV_BIN)/ruff format --check $(BACKEND_DIR)/app/ $(BACKEND_DIR)/tests/

typecheck: ## Run pyright type checker on backend
	cd $(BACKEND_DIR) && $(VENV_BIN_REL)/pyright

lint-frontend: ## Run ESLint on frontend
	cd $(FRONTEND_DIR) && npm run lint

validate-compliance-config: ## Validate compliance rule/profile config references
	PYTHONPATH=$(BACKEND_DIR) $(PYTHON) -c "from app.compliance.rules.registry import get_registry; from app.compliance.rules.profiles import validate_compliance_configs; validate_compliance_configs(get_registry()); print('✓ Compliance config validation passed')"

check-all: lint format-check typecheck lint-frontend validate-compliance-config test-unit ## Run ALL quality checks (lint + format + types + frontend lint + config + tests)
	@echo ""
	@echo "✓ All checks passed"

# ═════════════════════════════════════════════════════════════════
#  BUILD & DOCKER — Production builds
# ═════════════════════════════════════════════════════════════════

build: build-backend build-frontend ## Build everything for production

build-backend: ## Build backend Docker image
	docker build -t autotranscription-backend:latest $(BACKEND_DIR)/

build-frontend: ## Build frontend for production (static export)
	cd $(FRONTEND_DIR) && npm run build

docker-build: ## Build all Docker images (backend)
	docker build -t autotranscription-backend:latest $(BACKEND_DIR)/

docker-up: infra-up build-backend ## Start full stack in Docker (infra + backend image)
	@echo "Starting backend container..."
	docker run -d --name at-backend \
		--network $(BACKEND_DIR)_default \
		-e AT_ENV=prod \
		-e AT_PIPELINE__MODE=azure_di \
		-e AT_DATABASE__URL=postgresql+asyncpg://postgres:postgres@postgres:5432/autotranscription \
		-p 8100:8000 \
		autotranscription-backend:latest
	@echo "✓ Full stack running:"
	@echo "  Backend:    http://localhost:8100"
	@echo "  Health:     http://localhost:8100/api/documents/health"
	@echo "  PostgreSQL: localhost:5432"
	@echo "  Ollama:     http://localhost:11434"

docker-down: ## Stop full Docker stack (backend container + infra)
	docker stop at-backend 2>/dev/null || true
	docker rm at-backend 2>/dev/null || true
	$(MAKE) infra-down

docker-logs: ## Tail backend Docker container logs
	docker logs -f at-backend

docker-restart: docker-down docker-up ## Restart full Docker stack

# ═════════════════════════════════════════════════════════════════
#  QUICK TEST — Process a document
# ═════════════════════════════════════════════════════════════════

health: ## Check backend health
	@curl -sf http://localhost:8100/api/documents/health | python3 -m json.tool || echo "Backend not running"

process-pdf: ## Process a PDF (usage: make process-pdf PDF=path/to/file.pdf)
	@if [ -z "$(PDF)" ]; then \
		echo "Usage: make process-pdf PDF=path/to/file.pdf"; \
		exit 1; \
	fi
	curl -X POST http://localhost:8100/api/documents/process-file \
		-F "file=@$(PDF)" -s | python3 -m json.tool

kill: ## Kill processes on ports 8100 (backend) and 3100 (frontend)
ifeq ($(OS),Windows_NT)
	@for port in 8100 3100; do \
		pid=$$(netstat -ano 2>/dev/null | grep ":$$port " | grep LISTENING | awk '{print $$5}' | head -1); \
		if [ -n "$$pid" ] && [ "$$pid" != "0" ]; then \
			echo "  Port $$port: ENGAGED  (PID $$pid)"; \
			taskkill //PID $$pid //F 2>/dev/null || true; \
			echo "  Killed PID $$pid on port $$port"; \
		else \
			echo "  Port $$port: FREE"; \
		fi; \
	done
	@sleep 1
	@echo ""
	@echo "── Status after cleanup ─────────────────────────"
	@for port in 8100 3100; do \
		pid=$$(netstat -ano 2>/dev/null | grep ":$$port " | grep LISTENING | awk '{print $$5}' | head -1); \
		if [ -n "$$pid" ] && [ "$$pid" != "0" ]; then \
			echo "  Port $$port: STILL ENGAGED  (PID $$pid)"; \
		else \
			echo "  Port $$port: FREE"; \
		fi; \
	done
else
	@engaged=""; \
	for port in 8100 3100; do \
		pid=$$(lsof -ti:$$port 2>/dev/null); \
		if [ -n "$$pid" ]; then \
			name=$$(lsof -i:$$port -sTCP:LISTEN 2>/dev/null | tail -1 | awk '{print $$1}'); \
			echo "  Port $$port: ENGAGED  (PID $$pid — $$name)"; \
			engaged="$$engaged $$port"; \
		else \
			echo "  Port $$port: FREE"; \
		fi; \
	done; \
	if [ -z "$$engaged" ]; then \
		echo ""; \
		echo "✓ All ports already free — nothing to kill."; \
	else \
		echo ""; \
		for port in $$engaged; do \
			pid=$$(lsof -ti:$$port 2>/dev/null); \
			if [ -n "$$pid" ]; then \
				kill -9 $$pid 2>/dev/null || true; \
				echo "  Killed PID $$pid on port $$port"; \
			fi; \
		done; \
		sleep 1; \
		echo ""; \
		echo "── Status after cleanup ─────────────────────────"; \
		for port in 8100 3100; do \
			pid=$$(lsof -ti:$$port 2>/dev/null); \
			if [ -n "$$pid" ]; then \
				echo "  Port $$port: STILL ENGAGED  (PID $$pid)"; \
			else \
				echo "  Port $$port: FREE ✓"; \
			fi; \
		done; \
	fi
endif
	@if [ -f "$(FRONTEND_DIR)/.next/dev/lock" ]; then \
		rm -f "$(FRONTEND_DIR)/.next/dev/lock"; \
		echo "  Removed stale Next.js dev lock file"; \
	fi

# ═════════════════════════════════════════════════════════════════
#  CLEANUP
# ═════════════════════════════════════════════════════════════════

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(BACKEND_DIR)/dist $(BACKEND_DIR)/build $(BACKEND_DIR)/*.egg-info
	rm -rf $(BACKEND_DIR)/htmlcov
	rm -rf $(FRONTEND_DIR)/.next $(FRONTEND_DIR)/out
	@echo "✓ Caches and build artifacts removed"

deep-clean: clean ## Remove caches + venv + node_modules (full reset of deps)
	rm -rf $(VENV_DIR) $(VENV_REAL)
	rm -rf $(FRONTEND_DIR)/node_modules
	@echo "✓ Deep clean complete (venv + node_modules removed)"

reset: deep-clean ## Full environment reset (deps + infra + database)
	$(MAKE) infra-down
	cd $(BACKEND_DIR) && docker compose down -v
	@echo "✓ Full reset complete (containers, volumes, deps all removed)"
	@echo "  Run 'make setup' to start fresh"
