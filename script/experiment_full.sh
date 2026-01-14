#!/bin/bash
# experiment_full.sh - 自动化实验脚本
# 运行 4 种调度算法 × 3 种到达模式 = 12 组实验

set -e  # 遇到错误立即退出

# 实验配置
CARDS=4
TASKS=100
STEPS=60
SEED=42

# 4 种调度算法
SCHEDULERS=("bestfit" "drf" "p2c" "glass")
# 3 种到达模式
ARRIVALS=("poisson" "bursty" "mixed")

echo "=========================================================="
echo "    GLaSS Experiment: Multi-Scheduler Comparison"
echo "=========================================================="
echo ""
echo "Configuration:"
echo "  Cards:      $CARDS"
echo "  Tasks:      $TASKS"
echo "  Steps:      $STEPS"
echo "  Seed:       $SEED"
echo "  Schedulers: ${SCHEDULERS[*]}"
echo "  Arrivals:   ${ARRIVALS[*]}"
echo ""

# 显示可用调度器
echo "Available schedulers in system:"
make list-schedulers 2>/dev/null || python main.py --list-schedulers
echo ""

# 清理旧数据（可选，取消注释启用）
# echo "Cleaning old data..."
# rm -f data/*_loads_*.csv
# rm -f log/*.log

echo "=========================================================="
echo "Starting experiments: ${#SCHEDULERS[@]} schedulers × ${#ARRIVALS[@]} arrivals = $((${#SCHEDULERS[@]} * ${#ARRIVALS[@]})) runs"
echo "=========================================================="
echo ""

# 计数器
run_count=0
total_runs=$((${#SCHEDULERS[@]} * ${#ARRIVALS[@]}))

for sched in "${SCHEDULERS[@]}"; do
  for arrival in "${ARRIVALS[@]}"; do
    run_count=$((run_count + 1))
    echo ">>> [$run_count/$total_runs] scheduler=$sched arrival=$arrival"
    
    # 运行仿真
    python main.py --scheduler "$sched" \
                   --cards $CARDS \
                   --tasks $TASKS \
                   --steps $STEPS \
                   --seed $SEED \
                   --arrival-mode "$arrival" \
                   --data-dir data \
                   --log-dir log
    
    echo "    ✓ Completed: data/${sched}_${arrival}_loads_*.csv"
    echo ""
  done
done

echo "=========================================================="
echo "    Experiment Completed!"
echo "=========================================================="
echo ""
echo "Output files:"
echo "  Logs: log/*.log"
echo "  Data: data/*_loads_*.csv"
echo ""
echo "Generated data files:"
ls -la data/*_loads_*.csv 2>/dev/null | tail -20 || echo "  (no data files found)"
echo ""
echo "=========================================================="
echo "    Next Steps"
echo "=========================================================="
echo ""
echo "1. Plot individual load curves:"
echo "   make plot_step_load"
echo ""
echo "2. Compare variance across schedulers (same arrival mode):"
echo "   # Poisson mode comparison"
echo "   python plot/plot_compare_variance.py data/bestfit_poisson_loads_*.csv data/drf_poisson_loads_*.csv data/p2c_poisson_loads_*.csv data/glass_poisson_loads_*.csv"
echo ""
echo "   # Bursty mode comparison"  
echo "   python plot/plot_compare_variance.py data/bestfit_bursty_loads_*.csv data/drf_bursty_loads_*.csv data/p2c_bursty_loads_*.csv data/glass_bursty_loads_*.csv"
echo ""
echo "3. Summary metrics:"
echo "   grep 'Tasks Completed' log/*.log"
echo "   grep 'Avg Load Imbalance' log/*.log"
echo "   grep '\[MIGRATION\]' log/*.log | wc -l"