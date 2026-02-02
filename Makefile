PYTHON ?= python3
PYTHONPATH := backend

.PHONY: test test-bd

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q

test-bd:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q -k bd_
