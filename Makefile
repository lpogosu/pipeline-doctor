PYTHON ?= python
CORPUS ?= corpus
OUT ?= out

.DEFAULT_GOAL := help
.PHONY: help install demo report test lint fmt typecheck ci corpus bench clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the package with its development extras
	$(PYTHON) -m pip install -e ".[dev]"

demo: ## Analyse the bundled corpus and write JSON and HTML reports
	$(PYTHON) -m pipeline_doctor report $(CORPUS) --top 10 \
		--json $(OUT)/report.json --html $(OUT)/report.html

report: ## Same, with the extracted log evidence printed inline
	$(PYTHON) -m pipeline_doctor report $(CORPUS) --top 10 --spans

test: ## Run the test suite
	$(PYTHON) -m pytest

lint: ## Run ruff
	$(PYTHON) -m ruff check .

fmt: ## Apply the fixes ruff can apply
	$(PYTHON) -m ruff check --fix .

typecheck: ## Run mypy in strict mode, tests included
	$(PYTHON) -m mypy --strict pipeline_doctor scripts tests

ci: lint typecheck test ## Everything the pipeline runs

corpus: ## Regenerate the bundled sample corpus from its templates
	$(PYTHON) scripts/build_corpus.py --out $(CORPUS)

bench: ## Time ingest and analysis over the bundled corpus
	PYTHONPATH=. $(PYTHON) scripts/bench.py --corpus $(CORPUS)

clean: ## Remove reports and tooling caches
	rm -rf $(OUT) .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
