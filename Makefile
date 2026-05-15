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
