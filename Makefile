PYTHON ?= python

.PHONY: golden-contract
golden-contract:
	cd backend && $(PYTHON) scripts/verify_golden_contract_data.py
	cd backend && $(PYTHON) -m pytest -m golden_contract_blocking -s -q
	$(PYTHON) backend/scripts/check_golden_baseline_change.py
