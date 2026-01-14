#!/bin/bash
# Poisson arrival mode experiment script - Throughput and Migration Analysis
# Run experiments with multiple seeds and analyze throughput/migration metrics

set -e

# ============ Configuration ============
SEEDS=(0 10 21 42 123 456 789 1234 5678 91011)  # Multiple seeds for reproducibility
CARDS=4
TASKS=100
STEPS=60
ARRIVAL_MODE=poisson

# Output directories
DATA_DIR="data"
PLOT_DIR="plot"
RESULT_DIR="results"
LOG_DIR="log"

# ============ Setup ============
mkdir -p "$DATA_DIR" "$PLOT_DIR" "$RESULT_DIR" "$LOG_DIR"

# Timestamp for this experiment batch
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY_FILE="$RESULT_DIR/throughput_summary_${TIMESTAMP}.txt"

# ============ Functions ============

# Collect throughput metrics for a given seed
collect_metrics_for_seed() {
    local seed=$1
    local metrics_file="$RESULT_DIR/throughput_seed${seed}.txt"
    
    echo "========================================" | tee -a "$metrics_file"
    echo "Seed: $seed - $(date)" | tee -a "$metrics_file"
    echo "========================================" | tee -a "$metrics_file"
    
    # Run plot_throughput.py to collect and display metrics (but don't save plot)
    echo "" >> "$metrics_file"
    echo "=== Throughput and Migration Metrics ===" >> "$metrics_file"
    python plot/plot_throughput.py 2>&1 | tee -a "$metrics_file"
    
    echo "" >> "$metrics_file"
    
    # Append to summary file
    cat "$metrics_file" >> "$SUMMARY_FILE"
}

# Clean data directory for next seed run
clean_data() {
    echo "Cleaning $DATA_DIR/*.csv for next seed run..."
    rm -f "$DATA_DIR"/*.csv
}

# ============ Main Experiment Loop ============

echo "=============================================="
echo "Poisson Arrival Mode - Throughput Analysis"
echo "=============================================="
echo "Seeds: ${SEEDS[*]}"
echo "Cards: $CARDS, Tasks: $TASKS, Steps: $STEPS"
echo "Arrival Mode: $ARRIVAL_MODE"
echo "Results will be saved to: $RESULT_DIR"
echo "Summary file: $SUMMARY_FILE"
echo "=============================================="
echo ""

# Initialize summary file
echo "Throughput Analysis Summary - $TIMESTAMP" > "$SUMMARY_FILE"
echo "Configuration: CARDS=$CARDS, TASKS=$TASKS, STEPS=$STEPS, ARRIVAL_MODE=$ARRIVAL_MODE" >> "$SUMMARY_FILE"
echo "Seeds: ${SEEDS[*]}" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

# Run experiments for each seed
for seed in "${SEEDS[@]}"; do
    echo ""
    echo "=============================================="
    echo "Running experiment with SEED=$seed"
    echo "=============================================="
    echo ""
    
    # Run all schedulers with make compare
    make compare \
        SEED=$seed \
        CARDS=$CARDS \
        TASKS=$TASKS \
        STEPS=$STEPS \
        ARRIVAL_MODE=$ARRIVAL_MODE \
        DATA_DIR=$DATA_DIR \
        LOG_DIR=$LOG_DIR
    
    echo ""
    echo "Simulation complete for seed=$seed. Collecting metrics..."
    echo ""
    
    # Collect throughput metrics
    collect_metrics_for_seed "$seed"
    
    # Clean data for next run
    clean_data
    
    echo ""
    echo "Seed $seed completed."
    echo ""
done

echo ""
echo "=============================================="
echo "All experiments completed!"
echo "=============================================="
echo ""

# Generate aggregated throughput metrics table
echo "Generating aggregated throughput metrics table..."
python plot/throughput_table.py "$SUMMARY_FILE" --output "$PLOT_DIR/throughput_metrics.png"

echo ""
echo "Results Summary:"
echo "  - Individual metrics: $RESULT_DIR/throughput_seed*.txt"
echo "  - Combined summary:   $SUMMARY_FILE"
echo "  - Throughput table:   $PLOT_DIR/throughput_metrics.png"
echo ""
echo "To view summary: cat $SUMMARY_FILE"
