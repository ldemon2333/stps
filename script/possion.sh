#!/bin/bash
# Poisson arrival mode experiment script
# Run experiments with multiple seeds and collect metrics

set -e

# ============ Configuration ============
SEEDS=(0 10 21 42 123 456 789 1234 5678 91011)  # Multiple seeds for reproducibility
CARDS=16
TASKS=4096
STEPS=218
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
SUMMARY_FILE="$RESULT_DIR/metrics_summary_${TIMESTAMP}.txt"

# ============ Functions ============

# Run analysis scripts and capture metrics for a given seed
run_analysis_for_seed() {
    local seed=$1
    local metrics_file="$RESULT_DIR/metrics_seed${seed}.txt"
    
    echo "========================================" | tee -a "$metrics_file"
    echo "Seed: $seed - $(date)" | tee -a "$metrics_file"
    echo "========================================" | tee -a "$metrics_file"
    
    # Run plot_cv.py and capture output
    echo "" >> "$metrics_file"
    echo "=== Coefficient of Variation (CV) ===" >> "$metrics_file"
    python plot/plot_cv.py --output "$PLOT_DIR/cv_seed${seed}.png" 2>&1 | tee -a "$metrics_file"
    
    # Run plot_jfi.py and capture output
    echo "" >> "$metrics_file"
    echo "=== Jain's Fairness Index (JFI) ===" >> "$metrics_file"
    python plot/plot_jfi.py --output "$PLOT_DIR/jfi_seed${seed}.png" 2>&1 | tee -a "$metrics_file"
    
    # Run LIF.py and capture output
    echo "" >> "$metrics_file"
    echo "=== Load Imbalance Factor (LIF) ===" >> "$metrics_file"
    python plot/LIF.py --output "$PLOT_DIR/lif_seed${seed}.png" 2>&1 | tee -a "$metrics_file"
    
    # Run compare_cv.py (outputs to compare_cv.png, rename with seed)
    echo "" >> "$metrics_file"
    echo "=== CV Comparison ===" >> "$metrics_file"
    python plot/compare_cv.py "$DATA_DIR"/*_${ARRIVAL_MODE}_*.csv --output "$PLOT_DIR/compare_cv_seed${seed}.png" 2>&1 | tee -a "$metrics_file"
    
    echo "" >> "$metrics_file"
    echo "Plots saved:" >> "$metrics_file"
    echo "  - $PLOT_DIR/cv_seed${seed}.png" >> "$metrics_file"
    echo "  - $PLOT_DIR/jfi_seed${seed}.png" >> "$metrics_file"
    echo "  - $PLOT_DIR/lif_seed${seed}.png" >> "$metrics_file"
    echo "  - $PLOT_DIR/compare_cv_seed${seed}.png" >> "$metrics_file"
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
echo "Poisson Arrival Mode Experiment"
echo "=============================================="
echo "Seeds: ${SEEDS[*]}"
echo "Cards: $CARDS, Tasks: $TASKS, Steps: $STEPS"
echo "Arrival Mode: $ARRIVAL_MODE"
echo "Results will be saved to: $RESULT_DIR"
echo "Summary file: $SUMMARY_FILE"
echo "=============================================="
echo ""

# Initialize summary file
echo "Experiment Summary - $TIMESTAMP" > "$SUMMARY_FILE"
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
    echo "Simulation complete for seed=$seed. Running analysis..."
    echo ""
    
    # Run analysis and save metrics
    run_analysis_for_seed "$seed"
    
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

# Generate aggregated metrics table
echo "Generating aggregated metrics table..."
python plot/metrics_table.py "$SUMMARY_FILE" --output "$PLOT_DIR/metrics.png"

echo ""
echo "Results Summary:"
echo "  - Individual metrics: $RESULT_DIR/metrics_seed*.txt"
echo "  - Combined summary:   $SUMMARY_FILE"
echo "  - Plots:              $PLOT_DIR/*_seed*.png"
echo "  - Metrics table:      $PLOT_DIR/metrics.png"
echo ""
echo "To view summary: cat $SUMMARY_FILE"
