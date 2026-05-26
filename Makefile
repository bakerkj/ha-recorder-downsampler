.PHONY: venv test test-unit test-integration test-all test-unit-docker test-integration-docker clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-test.txt

# Default: unit tests only (fast)
test: test-unit

test-unit: venv
	$(PYTEST) tests/ --ignore=tests/integration -v

test-integration: venv
	$(PYTEST) tests/integration/ -v

test-all: test-unit test-integration

# Docker variants — match the GitHub Actions environment (Python 3.14)
test-unit-docker:
	docker run --rm -v $(PWD):/work -w /work python:3.14 \
		bash -c "pip install -r requirements-test.txt -q && python -m pytest tests/ --ignore=tests/integration -v"

test-integration-docker:
	docker run --rm -v $(PWD):/work -w /work python:3.14 \
		bash -c "pip install -r requirements-test.txt -q && python -m pytest tests/integration/ -v"

clean:
	rm -rf $(VENV) .pytest_cache __pycache__ tests/__pycache__ tests/integration/__pycache__
