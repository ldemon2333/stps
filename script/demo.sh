#!/bin/bash

# GLaSS 放置策略 - 快速演示脚本
# 用法: bash demo.sh

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     GLaSS 放置策略组合功能 - 快速演示                         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# 清空旧数据
echo "🧹 清理旧数据..."
make clean > /dev/null 2>&1 || true

# 测试 1: 默认 Best-Fit
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 演示 1: GLaSS + Best-Fit（默认策略）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "命令: make glass+bestfit"
echo ""
CARDS=2 TASKS=10 STEPS=5 make glass+bestfit 2>&1 | grep -E "^(2026|Scheduler:|Tasks|Total Migrations)" | tail -5

# 测试 2: P2C 策略
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 演示 2: GLaSS + P2C 策略"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "命令: make glass+p2c"
echo ""
CARDS=2 TASKS=10 STEPS=5 make glass+p2c 2>&1 | grep -E "^(2026|Scheduler:|Tasks|Total Migrations)" | tail -5

# 测试 3: DRF 策略
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 演示 3: GLaSS + DRF 策略"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "命令: make glass+drf"
echo ""
CARDS=2 TASKS=10 STEPS=5 make glass+drf 2>&1 | grep -E "^(2026|Scheduler:|Tasks|Total Migrations)" | tail -5

# 测试 4: Round-Robin 策略
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 演示 4: GLaSS + Round-Robin 策略"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "命令: make glass+rr"
echo ""
CARDS=2 TASKS=10 STEPS=5 make glass+rr 2>&1 | grep -E "^(2026|Scheduler:|Tasks|Total Migrations)" | tail -5

# 总结
echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║ ✅ 演示完成！                                                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "📖 更多信息，请查看："
echo "   • README.md              - 快速参考"
echo "   • docs/algorithm.md      - 算法理论"
echo "   • 运行: make help        - 查看所有 Make 目标"
echo ""
echo "🚀 快速开始命令："
echo "   make glass+bestfit          # GLaSS + Best-Fit（默认策略）"
echo "   make glass+p2c              # GLaSS + P2C 策略"
echo "   CARDS=8 TASKS=200 make glass+p2c   # 8卡 200任务 P2C策略"
echo "   make glass drf              # 分别运行 GLaSS 和 DRF"
echo "   python main.py --scheduler glass --placement-strategy drf  # 直接 Python"
echo ""
