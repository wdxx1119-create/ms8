install-dev:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

type:
	mypy src

clean:
	rm -rf dist build *.egg-info

release:
	mypy src/ms8
	ruff check src/ms8
	rm -f .coverage .coverage.*
	COVERAGE_FILE=/tmp/ms8-release/.coverage.make pytest tests/ --cov=src/ms8 --cov-fail-under=75
	MS8_HOME=/tmp/ms8-release OPENCLAW_MEMORY_SESSION_INGEST_ENABLED=0 MS8_DOCTOR_ALLOW_DEGRADED=1 python -m src.ms8 doctor
	python -m build

publish-test:
	bash scripts/publish_testpypi.sh

clear-release-env:
	bash -lc 'source scripts/clear_release_env.sh'
