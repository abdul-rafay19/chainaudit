PY ?= python3
test:
	$(PY) -m pytest -q
run:
	$(PY) -m uvicorn app.main:app --reload --port 8000
demo-data:
	$(PY) scripts/generate_demo_data.py
openapi:
	$(PY) scripts/export_openapi.py
lint:
	$(PY) -m ruff check app tests scripts
