SHELL := /bin/sh
.DEFAULT_GOAL := help
AGENTS_METRICS ?= off
export AGENTS_METRICS

PACKAGES := code-flow code-quality local-guards usage-metrics
PYTHON ?= $(shell for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do command -v $$p 2>/dev/null && break; done)

.PHONY: help check test test-root test-package test-packages lint manifests links \
	sync sync-check validate

help:
	@printf '%s\n' \
	  'make check                        suites, lint, manifests, links, form sync check, plugin validation' \
	  'make test                         root suite and every package suite' \
	  'make test-root                    root suite only' \
	  'make test-package PACKAGE=NAME    one package suite' \
	  'make lint                         ruff on every tracked Python file' \
	  'make manifests                    every tracked JSON file parses' \
	  'make links                        relative links in README and docs resolve' \
	  'make sync                         regenerate agent forms and review standards in plugins/code-flow' \
	  'make sync-check                   fail when generated agent forms or standards are stale' \
	  'make validate                     claude plugin validate for the marketplace and every package' \
	  'Variables: PYTHON (default: the newest python3 found), AGENTS_METRICS (default: off)'

check: test lint manifests links sync-check
	@if command -v claude >/dev/null 2>&1; then $(MAKE) --no-print-directory validate; else echo 'claude CLI not found: plugin validation skipped'; fi

test: test-root test-packages

test-root:
	$(PYTHON) -m unittest discover -s tests -v

test-package:
	@test -n '$(PACKAGE)' || { echo 'usage: make test-package PACKAGE=NAME'; exit 2; }
	@test -d 'plugins/$(PACKAGE)' || { echo 'unknown package: $(PACKAGE)'; exit 2; }
	cd 'plugins/$(PACKAGE)' && $(PYTHON) -m unittest discover -s tests -v

test-packages:
	@for package in $(PACKAGES); do \
	  echo "--- plugins/$$package"; \
	  (cd "plugins/$$package" && $(PYTHON) -m unittest discover -s tests -v) || exit 1; \
	done

lint:
	@command -v ruff >/dev/null 2>&1 || { echo 'ruff is required for lint'; exit 1; }
	ruff check --no-cache $$(git ls-files '*.py')

manifests:
	$(PYTHON) -c 'import json, subprocess; files = [f for f in subprocess.run(["git", "ls-files", "*.json"], capture_output=True, text=True, check=True).stdout.splitlines() if f]; [json.load(open(f, encoding="utf-8")) for f in files]; print(f"manifests ok: {len(files)} JSON files")'

links:
	$(PYTHON) -m unittest tests.test_docs -v

sync:
	cd plugins/code-flow && $(PYTHON) tools/sync-agents.py && $(PYTHON) tools/sync-standards.py

sync-check:
	cd plugins/code-flow && $(PYTHON) tools/sync-agents.py --check && $(PYTHON) tools/sync-standards.py --check

validate:
	@for target in . $$(for package in $(PACKAGES); do echo "plugins/$$package"; done); do \
	  echo "--- claude plugin validate $$target"; \
	  claude plugin validate "$$target" || exit 1; \
	done
