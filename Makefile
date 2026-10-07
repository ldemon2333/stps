PYTHON ?= python
OUTPUT ?= data/phase1

.PHONY: test demo validate cluster-demo cluster-validate stps-compare stps-hotspots
test:
	$(PYTHON) -m pytest -q tests/

# Creates timestamped results; never overwrites a previous experiment.
demo:
	$(PYTHON) script/validate_single_card.py --output-root $(OUTPUT)

validate: test demo

cluster-demo:
	$(PYTHON) script/validate_cluster.py --output-root data/cluster

cluster-validate: test cluster-demo

stps-compare:
	$(PYTHON) script/compare_stps.py --output-root data/stps_comparison

stps-hotspots:
	$(PYTHON) script/compare_stps_hotspots.py --output-root data/stps_hotspots
