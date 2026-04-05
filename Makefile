.PHONY: dev test lint format typecheck check eval eval-seed eval-seed-clear eval-classify eval-tools eval-e2e eval-e2e-verbose eval-guardrails eval-memory eval-plan eval-saturation eval-all eval-langfuse

VENV := .venv/bin/
OLLAMA_URL ?= http://localhost:11434

dev:
	python3 -m venv .venv
	$(VENV)pip install -e ".[dev]"
	$(VENV)pre-commit install
	@echo "Done! Run: source .venv/bin/activate"

test:
	$(VENV)pytest tests/ -v

lint:
	$(VENV)ruff check app tests

format:
	$(VENV)ruff format app tests

typecheck:
	$(VENV)mypy app

check: lint typecheck test

# --- Eval targets ---

eval-seed:
	$(VENV)python scripts/seed_eval_dataset.py --db data/localforge.db

eval-seed-clear:
	$(VENV)python scripts/seed_eval_dataset.py --db data/localforge.db --clear

eval-classify:
	$(VENV)python scripts/run_eval.py --mode classify --threshold 0.8 --limit 100 --ollama $(OLLAMA_URL)

eval-tools:
	$(VENV)python scripts/run_eval.py --mode tools --threshold 0.7 --limit 100 --ollama $(OLLAMA_URL)

eval-e2e:
	$(VENV)python scripts/run_eval.py --mode e2e --threshold 0.5 --limit 100 --ollama $(OLLAMA_URL)

eval-e2e-verbose:
	$(VENV)python scripts/run_eval.py --mode e2e --threshold 0.5 --limit 100 --ollama $(OLLAMA_URL) -v

eval-guardrails:
	$(VENV)python scripts/run_eval.py --mode guardrails --threshold 0.9 --limit 100

eval-memory:
	$(VENV)python scripts/run_eval.py --mode memory --threshold 0.6 --limit 100 --ollama $(OLLAMA_URL)

eval-plan:
	$(VENV)python scripts/run_eval.py --mode plan --threshold 0.5 --limit 100 --ollama $(OLLAMA_URL)

eval-saturation:
	$(VENV)python scripts/context_saturation_analysis.py

eval-langfuse:
	$(VENV)python scripts/run_eval.py --mode e2e --threshold 0.5 --limit 100 --ollama $(OLLAMA_URL) --langfuse

eval-all: eval-seed eval-classify eval-tools eval-e2e eval-guardrails eval-memory

eval: eval-seed eval-classify eval-e2e
