.PHONY: venv test test-unit test-integration test-all test-unit-docker test-integration-docker clean

VENV := .venv
# Same install as CI: the locked test group, without the dev group's mypy.
SYNC := uv sync --frozen --no-default-groups --group test
RUN := uv run --no-sync python -m pytest

venv:
	$(SYNC)

# Default: unit tests only (fast)
test: test-unit

test-unit: venv
	$(RUN) tests/ --ignore=tests/integration -v

test-integration: venv
	$(RUN) tests/integration/ -v

test-all: test-unit test-integration

# Docker variants — match the GitHub Actions environment (Python 3.14). The venv
# lives outside the mount so the container never overwrites the host's .venv.
DOCKER := docker run --rm -v $(PWD):/work -w /work -e UV_PROJECT_ENVIRONMENT=/opt/venv python:3.14
DOCKER_SETUP := pip install -q uv==0.13.5 && $(SYNC)

test-unit-docker:
	$(DOCKER) bash -c "$(DOCKER_SETUP) && $(RUN) tests/ --ignore=tests/integration -v"

test-integration-docker:
	$(DOCKER) bash -c "$(DOCKER_SETUP) && $(RUN) tests/integration/ -v"

clean:
	rm -rf $(VENV) .pytest_cache __pycache__ tests/__pycache__ tests/integration/__pycache__
