PYTHON ?= python
CARDS ?= 4
TASKS ?= 512
STEPS ?= 512
SEED ?= 21
LOG_DIR ?= log
DATA_DIR ?= data
ARRIVAL_MODE ?= bursty
DATA_OUTPUT ?=
# GG (GLaSS-Greedy) specific parameters
CARD_CAPACITY ?= 4500

# Common arguments for all simulations
COMMON_ARGS = --cards $(CARDS) --tasks $(TASKS) --steps $(STEPS) --seed $(SEED) \
              --log-dir $(LOG_DIR) --data-dir $(DATA_DIR) --arrival-mode $(ARRIVAL_MODE) --card-capacity $(CARD_CAPACITY)

# If DATA_OUTPUT is set, append to common args
ifneq ($(strip $(DATA_OUTPUT)),)
	COMMON_ARGS := $(COMMON_ARGS) --data-output $(DATA_OUTPUT)
endif

# GG (GLaSS-Greedy) specific arguments


.PHONY: all glass gandiva p2c drf bestfit rr list-schedulers clean help plot_step_load plot_spike compare \
        glass+bestfit glass+p2c glass+drf glass+rr \
        train-drl eval-drl compare-drl

# Define scheduler and strategy names
SCHEDULERS := glass gandiva drf p2c bestfit rr
STRATEGIES := bestfit p2c drf rr

# Detect if this is a plot_compare_variance call
IS_PLOT_COMPARE := $(filter plot_compare_variance,$(MAKECMDGOALS))
SCHEDULER_ARGS := $(filter $(SCHEDULERS),$(MAKECMDGOALS))
ifeq ($(IS_PLOT_COMPARE),plot_compare_variance)
OVERRIDE_SCHEDULERS := 1
endif

# Extract combination targets (glass+strategy format)
COMBO_TARGETS := $(filter glass+%,$(MAKECMDGOALS))
COMBO_STRATEGY := $(subst glass+,,$(COMBO_TARGETS))

# Run Round Robin static scheduler
rr:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler rr $(COMMON_ARGS)
endif

# Run bestfit static scheduler
bestfit:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler bestfit $(COMMON_ARGS)
endif

# Run simulation with GG (GLaSS-Greedy) dynamic scheduler (default: Best-Fit strategy)
glass:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler glass $(COMMON_ARGS) 
endif

# Run GLaSS dynamic scheduler (main algorithm)
gandiva:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler gandiva $(COMMON_ARGS) 
endif

# Run drf scheduler
drf:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler drf $(COMMON_ARGS)
endif

# Run p2c scheduler
p2c:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler p2c $(COMMON_ARGS)
endif

# Run glass-drl scheduler
glass-drl:
ifeq ($(OVERRIDE_SCHEDULERS),1)
	@true
else
	$(PYTHON) main.py --scheduler glass_drl --model-path $(MODEL_PATH) --delta $(DELTA) --top-k $(TOP_K) --window-size $(WINDOW_SIZE) $(COMMON_ARGS)
endif

# GG with specific placement strategies (glass+strategy format)
# These targets allow using a strategy with GG without running it as a separate scheduler

# GG + Best-Fit Strategy
glass+bestfit:
	$(PYTHON) main.py --scheduler glass $(COMMON_ARGS)  --placement-strategy bestfit

# GG + P2C Strategy
glass+p2c:
	$(PYTHON) main.py --scheduler glass $(COMMON_ARGS)  --placement-strategy p2c

# GG + DRF Strategy
glass+drf:
	$(PYTHON) main.py --scheduler glass $(COMMON_ARGS)  --placement-strategy drf

# GG + Round-Robin Strategy
glass+rr:
	$(PYTHON) main.py --scheduler glass $(COMMON_ARGS)  --placement-strategy rr

# Run all schedulers for comparison
compare: glass-drl glass gandiva drf p2c bestfit rr

# List available schedulers
list-schedulers:
	$(PYTHON) main.py --list-schedulers

# Plotting targets
.PHONY: plot_step_load plot_spike plot_compare_variance help

plot_step_load:
	$(PYTHON) plot/plot_step_loads.py $(DATA_DIR)/*_loads*.csv --format png

plot_spike:
	$(PYTHON) plot/plot_task_spikes.py --steps 60 --neuron-count 900 --complexity 1.2 --state-mb 14 --seed 21

plot_compare_variance:
ifdef SCHEDULER_ARGS
	@CSV_FILES=''; \
	for sched in $(SCHEDULER_ARGS); do \
		CSV_FILES="$$CSV_FILES $(DATA_DIR)/$${sched}_loads_*.csv"; \
	done; \
	$(PYTHON) plot/plot_compare_variance.py $$CSV_FILES
else
	@echo "Usage: make plot_compare_variance scheduler1 scheduler2 [scheduler3 ...]"
	@echo "Example: make plot_compare_variance drf glass bestfit"
	@echo "Available schedulers: $(SCHEDULERS)"
endif

# Help target
help:
	@echo "SNN Scheduler Simulation - Make Targets"
	@echo ""
	@echo "Basic Schedulers (run independently):"
	@echo "  make glass          - Run GLaSS dynamic scheduler (default Best-Fit strategy)"
	@echo "  make gandiva        - Run Gandiva-Spike dynamic scheduler (Smallest-First baseline)"
	@echo "  make drf            - Run DRF static scheduler"
	@echo "  make p2c            - Run P2C static scheduler"
	@echo "  make bestfit        - Run Best-Fit static scheduler"
	@echo "  make rr             - Run Round-Robin static scheduler"
	@echo "  make compare        - Run all 5 schedulers in sequence"
	@echo ""
	@echo "GLaSS with Custom Placement Strategies (glass+strategy format):"
	@echo "  make glass+bestfit  - GLaSS using Best-Fit strategy"
	@echo "  make glass+p2c      - GLaSS using P2C strategy"
	@echo "  make glass+drf      - GLaSS using DRF strategy"
	@echo "  make glass+rr       - GLaSS using Round-Robin strategy"
	@echo ""
	@echo "Run Multiple Schedulers:"
	@echo "  make glass drf      - Run both GLaSS (default) and DRF"
	@echo "  make glass p2c rr   - Run GLaSS (default), P2C, and RR"
	@echo ""
	@echo "Configuration Variables (prefix to any target):"
	@echo "  CARDS=8 make glass+p2c              - Use 8 cards instead of 4"
	@echo "  TASKS=200 make compare              - Schedule 200 tasks instead of 100"
	@echo "  STEPS=120 make glass+drf            - Run for 120 steps instead of 60"
	@echo "  ARRIVAL_MODE=bursty make glass+p2c  - Use bursty arrival pattern"
	@echo "  SEED=99 make compare                - Use seed 99 for reproducibility"
	@echo ""
	@echo "  DATA_OUTPUT=myname make glass       - Prefix data output files with 'myname'"
	@echo "Plotting:"
	@echo "  make plot_step_load                   - Plot step-wise load traces"
	@echo "  make plot_spike                       - Plot synthetic task spikes"
	@echo "  make plot_compare_variance drf glass  - Compare variance of schedulers"
	@echo ""
	@echo "Scripts (in script/ directory):"
	@echo "  bash script/demo.sh                - Demo all placement strategies"
	@echo "  bash script/experiment_full.sh     - Full experiment (12 runs)"
	@echo "  bash script/compare_schedulers.sh  - Generate comparison plots"
	@echo ""
	@echo "Utilities:"
	@echo "  make list-schedulers - List available scheduler names"
	@echo "  make clean           - Remove all generated CSV/PNG/PDF files"
	@echo "  make help            - Show this help message"
	@echo ""
	@echo "Documentation:"
	@echo "  README.md            - Quick start guide"
	@echo "  docs/algorithm.md    - Algorithm theory"
	@echo "  docs/pseudocode.md   - GLaSS pseudocode"
	@echo "  docs/experiment.md   - Experiment guide"


# Clean generated files
clean:
	rm -f $(LOG_DIR)/*.log 
	rm -f $(DATA_DIR)/*.csv
	rm -f plot/*.pdf
	rm -f figures/*
	find plot -name '*.png' -not -name 'metrics*.png' -not -name 'throughput_metrics*.png' -delete
	rm -f results/*.csv

# GLaSS-DRL targets
MODEL_PATH ?= models/glass_drl.zip
DRL_TIMESTEPS ?= 200000
DELTA ?= 0.1
TOP_K ?= 10
WINDOW_SIZE ?= 16

train-drl:
	bash script/train_drl.sh

eval-drl:
	bash script/eval_drl.sh

compare-drl: gandiva glass bestfit drf p2c
	$(PYTHON) main.py --scheduler glass_drl $(COMMON_ARGS) --model-path $(MODEL_PATH) --delta $(DELTA) --top-k $(TOP_K) --window-size $(WINDOW_SIZE)
	$(PYTHON) plot/plot_drl_comparison.py --data-dir $(DATA_DIR)