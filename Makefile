PYTHON ?= /root/miniconda3/envs/snn/bin/python
CARDS ?= 4
TASKS ?= 512
STEPS ?= 512
SEED ?= 21
LOG_DIR ?= log
DATA_DIR ?= data
ARRIVAL_MODE ?= bursty
DATA_OUTPUT ?=

# STPS knobs
FINGERPRINT_DIR ?= npz
BW_MAX ?= 5e6
D_MAX ?= 16
HORIZON ?= 64
SPLIT_THRESHOLD ?= 0.2

COMMON_ARGS = --cards $(CARDS) --tasks $(TASKS) --steps $(STEPS) --seed $(SEED) \
              --log-dir $(LOG_DIR) --data-dir $(DATA_DIR) --arrival-mode $(ARRIVAL_MODE)

ifneq ($(strip $(DATA_OUTPUT)),)
	COMMON_ARGS := $(COMMON_ARGS) --data-output $(DATA_OUTPUT)
endif

STPS_ARGS = --fingerprint-dir $(FINGERPRINT_DIR) --bw-max $(BW_MAX) --d-max $(D_MAX) \
            --horizon $(HORIZON) --centrality-split-threshold $(SPLIT_THRESHOLD)

.PHONY: all bestfit drf p2c rr stps stps-spatial stps-temporal \
        list-schedulers fingerprints clean help compare compare-stps \
        q1 q1-sweep q1-mix q1-all

# Baselines
rr:
	$(PYTHON) main.py --scheduler rr $(COMMON_ARGS)

bestfit:
	$(PYTHON) main.py --scheduler bestfit $(COMMON_ARGS)

drf:
	$(PYTHON) main.py --scheduler drf $(COMMON_ARGS)

p2c:
	$(PYTHON) main.py --scheduler p2c $(COMMON_ARGS)

# STPS family (paper §4.3)
stps:
	$(PYTHON) main.py --scheduler stps $(COMMON_ARGS) $(STPS_ARGS)

stps-spatial:
	$(PYTHON) main.py --scheduler stps-spatial $(COMMON_ARGS) $(STPS_ARGS)

stps-temporal:
	$(PYTHON) main.py --scheduler stps-temporal $(COMMON_ARGS) $(STPS_ARGS)

# Generate offline fingerprints (synthetic by default; replace with real ones for paper experiments).
fingerprints:
	mkdir -p $(FINGERPRINT_DIR)
	$(PYTHON) -m fingerprint.cli --synthetic --T $(HORIZON) --beta 4.0 --K 2 \
	    --out $(FINGERPRINT_DIR)/synthetic_bursty.npz
	$(PYTHON) -m fingerprint.cli --synthetic --T $(HORIZON) --beta 1.05 --K 1 \
	    --out $(FINGERPRINT_DIR)/synthetic_flat.npz
	$(PYTHON) -m fingerprint.cli --synthetic --T $(HORIZON) --beta 8.0 --K 3 \
	    --out $(FINGERPRINT_DIR)/synthetic_extreme.npz

# Run baselines + STPS family for comparison
compare: bestfit drf p2c rr
compare-stps: bestfit drf p2c rr stps stps-spatial stps-temporal

# Q1 — Spatial Load Balancing via Step A (see docs/Q1_TODO.md)
q1:
	$(PYTHON) script/q1_run.py main

q1-sweep:
	$(PYTHON) script/q1_run.py sweep

q1-mix:
	$(PYTHON) script/q1_run.py mix

q1-all:
	$(PYTHON) script/q1_run.py all

list-schedulers:
	$(PYTHON) main.py --list-schedulers

clean:
	rm -f $(LOG_DIR)/*.log
	rm -f $(DATA_DIR)/*.csv
	rm -f figures/*

help:
	@echo "SNN Scheduler Simulation - Make Targets"
	@echo ""
	@echo "Baselines:"
	@echo "  make bestfit       - Best-Fit (greedy)"
	@echo "  make drf           - Dominant Resource Fairness"
	@echo "  make p2c           - Power of Two Choices"
	@echo "  make rr            - Round Robin"
	@echo ""
	@echo "STPS family (paper §4.3):"
	@echo "  make fingerprints  - Generate synthetic *.npz fingerprints"
	@echo "  make stps          - Full 3-stage STPS scheduler"
	@echo "  make stps-spatial  - Ablation: spatial-only (no phase shift)"
	@echo "  make stps-temporal - Ablation: temporal-only (no fragmentation/hotspot split)"
	@echo ""
	@echo "Comparison:"
	@echo "  make compare       - Run all baselines"
	@echo "  make compare-stps  - Run baselines + STPS family"
	@echo ""
	@echo "Knobs (prefix any target):"
	@echo "  CARDS=8 TASKS=200 STEPS=120 SEED=99 ARRIVAL_MODE=bursty"
	@echo "  BW_MAX=5e6 D_MAX=16 HORIZON=64 FINGERPRINT_DIR=npz"
	@echo ""
	@echo "Utilities:"
	@echo "  make list-schedulers  - List registered schedulers"
	@echo "  make clean            - Remove generated logs/data"
