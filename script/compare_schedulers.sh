#!/bin/bash
# Compare GLaSS scheduler with other algorithms
# Generates comparison plots: glass vs drf, glass vs p2c, etc.

set -e

DATA_DIR="data"
PLOT_DIR="plot"

# Create directories
mkdir -p "$DATA_DIR" "$PLOT_DIR"

# Find the latest data files for each scheduler
echo "Searching for scheduler data files..."

# Function to find the latest CSV file for a given scheduler
find_latest_csv() {
    local scheduler=$1
    local latest=$(ls -t "${DATA_DIR}/${scheduler}"_*_loads_*.csv 2>/dev/null | head -1)
    if [ -z "$latest" ]; then
        echo "ERROR: No data file found for scheduler: $scheduler" >&2
        return 1
    fi
    echo "$latest"
}

# Get latest files for each scheduler
GLASS_FILE=$(find_latest_csv "glass")
DRF_FILE=$(find_latest_csv "drf")
P2C_FILE=$(find_latest_csv "p2c")
BESTFIT_FILE=$(find_latest_csv "bestfit")
RR_FILE=$(find_latest_csv "roundrobin")

echo "Found data files:"
echo "  GLaSS:     $GLASS_FILE"
echo "  DRF:       $DRF_FILE"
echo "  P2C:       $P2C_FILE"
echo "  BestFit:   $BESTFIT_FILE"
echo "  RoundRobin: $RR_FILE"
echo ""

# Function to generate comparison plot
generate_comparison() {
    local name1=$1
    local file1=$2
    local name2=$3
    local file2=$4
    local output_name=$5
    
    echo "Generating comparison: $name1 vs $name2 ..."
    python "$PLOT_DIR/plot_compare_variance.py" \
        "$file1" "$file2" \
        --labels "$name1" "$name2" \
        --output "$PLOT_DIR/${output_name}.png" \
        --format png
    
    echo "  → $PLOT_DIR/${output_name}.png"
}

# Generate all comparisons
echo "Generating comparison plots..."
echo ""

generate_comparison "GLaSS" "$GLASS_FILE" "DRF" "$DRF_FILE" "glass_vs_drf"
generate_comparison "GLaSS" "$GLASS_FILE" "P2C" "$P2C_FILE" "glass_vs_p2c"
generate_comparison "GLaSS" "$GLASS_FILE" "BestFit" "$BESTFIT_FILE" "glass_vs_bestfit"
generate_comparison "GLaSS" "$GLASS_FILE" "RoundRobin" "$RR_FILE" "glass_vs_roundrobin"
generate_comparison "DRF" "$DRF_FILE" "P2C" "$P2C_FILE" "drf_vs_p2c"

echo ""
echo "All comparison plots generated successfully!"
echo ""
echo "Output files:"
ls -lh "$PLOT_DIR"/glass_vs_*.png "$PLOT_DIR"/drf_vs_*.png 2>/dev/null || true
