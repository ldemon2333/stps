# GLaSS: Global Load-aware SNN Scheduler

神经拟态集群动态负载均衡调度器

## 项目概述

GLaSS 是一个面向类脑计算集群的动态调度框架，支持多种放置策略和负载均衡算法。

## 快速开始

### 运行调度器

```bash
# 单个调度器
make glass               # GLaSS 调度器（Best-Fit 策略）
make drf                 # DRF 调度器
make p2c                 # P2C 调度器
make bestfit             # Best-Fit 调度器
make rr                  # Round-Robin 调度器

# 对比运行所有调度器
make compare
```

### GLaSS + 放置策略组合

使用 `+` 符号指定 GLaSS 的放置策略：

```bash
make glass+bestfit       # GLaSS + Best-Fit（默认）
make glass+p2c           # GLaSS + P2C
make glass+drf           # GLaSS + DRF
make glass+rr            # GLaSS + Round-Robin
```

### 运行多个独立调度器

使用空格分隔运行多个独立调度器：

```bash
make glass drf           # 依次运行 GLaSS，然后 DRF
make glass p2c rr        # 依次运行三个调度器
```

### 自定义参数

```bash
# 环境变量方式
CARDS=8 TASKS=200 make glass+p2c
ARRIVAL_MODE=bursty SEED=99 make glass+drf

# Python 命令行方式
python main.py --scheduler glass --placement-strategy p2c --cards 8 --tasks 200
```

## 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CARDS` | 4 | 卡数量 |
| `TASKS` | 100 | 任务数量 |
| `STEPS` | 60 | 仿真时间步 |
| `SEED` | 42 | 随机种子 |
| `ARRIVAL_MODE` | poisson | 到达模式：poisson/bursty/mixed |

## 放置策略

| 策略 | 命令 | 特点 | 复杂度 |
|------|------|------|--------|
| **Best-Fit** | `glass+bestfit` | 最小化资源碎片 | O(M) |
| **P2C** | `glass+p2c` | 随机二选一，理论保证 | O(1) |
| **DRF** | `glass+drf` | 多维资源公平 | O(M) |
| **Round-Robin** | `glass+rr` | 简单轮询 | O(1) |

## 目录结构

```
v3/
├── main.py                 # 统一入口
├── Makefile                # 自动化构建
├── README.md               # 本文件
│
├── schedule/               # 调度算法
│   ├── base.py             # 基类
│   ├── glass.py            # GLaSS 动态调度
│   ├── bestfit.py          # Best-Fit
│   ├── drf.py              # DRF
│   └── p2c.py              # P2C
│
├── simulation/             # 仿真引擎
│   └── engine.py
│
├── util/                   # 工具模块
│   ├── card.py             # 卡资源管理
│   ├── task.py             # 任务模型
│   ├── sim.py              # 仿真辅助
│   └── metrics.py          # 评估指标
│
├── script/                 # 脚本
│   ├── demo.sh             # 功能演示
│   ├── experiment_full.sh  # 完整实验
│   └── compare_schedulers.sh # 调度器对比
│
├── plot/                   # 可视化
│   └── README.md           # 绘图说明
│
├── data/                   # 输出数据
├── log/                    # 运行日志
│
└── docs/                   # 详细文档
    ├── algorithm.md        # 算法理论
    ├── pseudocode.md       # 伪代码
    └── experiment.md       # 实验指南
```

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/algorithm.md](docs/algorithm.md) | 调度算法理论（Best-Fit, DRF, P2C, GLaSS） |
| [docs/pseudocode.md](docs/pseudocode.md) | GLaSS 算法伪代码 |
| [docs/experiment.md](docs/experiment.md) | 实验设计与流程 |
| [plot/README.md](plot/README.md) | 可视化脚本说明 |

## 脚本使用

```bash
# 功能演示 - 展示四种策略效果
bash script/demo.sh

# 完整实验 - 4调度器 × 3到达模式 = 12组
bash script/experiment_full.sh

# 生成对比图表
bash script/compare_schedulers.sh
```

## 常用工作流

### 1. 快速测试
```bash
make glass+p2c            # 运行 GLaSS+P2C
make plot_step_load       # 绘制负载曲线
```

### 2. 策略对比
```bash
make clean
for s in bestfit p2c drf rr; do
  SEED=42 make glass+$s
done
```

### 3. 完整实验
```bash
bash script/experiment_full.sh
```

## 输出文件

| 路径 | 说明 |
|------|------|
| `log/*.log` | 运行日志 |
| `data/*_loads_*.csv` | 负载数据 |
| `plot/*.png` | 可视化图表 |

## 帮助

```bash
make help                 # 查看所有 Make 目标
python main.py --help     # 查看命令行参数
```
