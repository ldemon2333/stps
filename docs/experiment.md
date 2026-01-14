# GLaSS 实验流程指南

## 多调度算法对比实验框架

---

## 1. 实验目标

本实验框架支持多种调度策略在 SNN 类脑集群中的性能对比评估：

- **Static Scheduling（静态调度）**：任务初始放置后不再迁移，作为基准 (Baseline)
- **GLaSS (Dynamic)**：运行时动态负载均衡，基于 ROI-Greedy 迁移策略
- **可扩展**：支持自定义调度算法，只需实现 `BaseScheduler` 接口

**核心评估指标**：
1. 负载均衡度（Load Balance）：多卡负载方差
2. 系统吞吐量（Throughput）：单位时间完成的任务数
3. 资源利用率（Utilization）：核心、内存、突触资源使用效率
4. 迁移开销（Migration Cost）：动态调度的额外代价
5. 任务完成率（Completion Rate）：成功完成的任务比例

---

## 2. 环境准备

### 2.1 代码仓库结构

```
v3/
├── main.py                    # 统一入口（支持 --scheduler 选择算法）
├── Makefile                   # 自动化运行脚本
├── README.md                  # 快速开始指南
├── .env                       # 硬件配置（Darwin3 参数）
│
├── docs/                      # 详细文档
│   ├── algorithm.md           # GLaSS 算法理论文档
│   ├── pseudocode.md          # GLaSS 伪代码实现指南
│   └── experiment.md          # 本实验流程文档
│
├── script/                    # 脚本
│   ├── demo.sh                # 功能演示
│   ├── experiment_full.sh     # 完整实验
│   └── compare_schedulers.sh  # 调度器对比
│
├── schedule/                  # 调度算法模块（可扩展）
│   ├── __init__.py            # 调度器注册表
│   ├── base.py                # BaseScheduler 抽象基类
│   └── glass.py               # GLaSS 动态调度实现
│
├── simulation/
│   ├── __init__.py
│   └── engine.py              # 统一仿真引擎（SimulationEngine）
│
├── util/
│   ├── __init__.py
│   ├── card.py                # 神经拟态卡资源管理
│   ├── task.py                # SNN 任务动态负载模型
│   ├── sim.py                 # 日志、任务生成、到达模式
│   └── metrics.py             # 评估指标计算与输出
│
├── plot/
│   ├── plot_static_loads.py   # 绘制负载曲线
│   └── plot_task_spikes.py    # 绘制任务脉冲活动
│
├── log/                       # 运行日志输出目录
└── data/                      # CSV 数据输出目录
```

### 2.2 环境依赖

```bash
# Python 环境 (推荐 3.10+)
python --version

# 安装依赖
pip install matplotlib  # 用于可视化
```

### 2.3 硬件配置检查

查看 [.env](.env) 文件中的神经拟态卡参数（基于 Darwin3 架构）：

```bash
cat .env
```

**默认配置**：
- 单卡核心数：512
- 单卡突触容量：50,000
- 单卡内存：128 GB
- 卡间带宽：1000 Mbps

### 2.4 查看可用调度器

```bash
python main.py --list-schedulers
```

**当前支持的调度器**：
- `bestfit`：静态装箱算法，任务初始放置后不迁移（基准 Baseline）
- `roundrobin` / `rr`：轮询算法，静态循环分配（基准对比）
- `p2c`：二次随机选择，静态随机化负载均衡（基准对比）
- `drf`：主导资源公平（DRF），多维资源感知的静态调度（基准对比）
- `glass`：GLaSS 动态调度，支持运行时任务迁移和负载均衡（主要算法）
- `glass` / `dynamic`：GLaSS 动态负载均衡

---

## 3. 实验设计

### 3.1 实验变量设置

#### A. 固定参数（控制变量）

| 参数 | 符号 | 值 | 说明 |
|-----|------|-----|------|
| 神经拟态卡数量 | `CARDS` | 4 | 模拟 4 卡集群 |
| 总任务数 | `TASKS` | 100 | 待调度的 SNN 任务总数 |
| 仿真时间步 | `STEPS` | 60 | 离散时间步（每步 ≈ 1ms 物理时间） |
| 随机种子 | `SEED` | 42 | 保证实验可重现 |
| 负载权重 α | `ALPHA` | 1.0 | 脉冲计数权重 |
| 负载权重 β | `BETA` | 0.01 | 突触操作权重 |

#### B. 自变量（实验对照）

**实验因子 1：调度策略 (--scheduler)**
- `static`：静态调度（无迁移）
- `glass`：GLaSS 动态调度
- *(可扩展更多调度器)*

**实验因子 2：工作负载模式 (--arrival-mode)**
- `poisson`：泊松到达（随机均匀）
- `bursty`：突发到达（60% 在 t=0，30% 在 t=T/2，10% 分散）
- `mixed`：混合模式（40% 均匀 + 60% 泊松）

**实验设计矩阵（全因子设计 2×3）**：

| 实验组 | 调度策略 | 到达模式 | 标签 |
|--------|---------|---------|------|
| Exp-1  | Static  | Poisson | S-P  |
| Exp-2  | Static  | Bursty  | S-B  |
| Exp-3  | Static  | Mixed   | S-M  |
| Exp-4  | GLaSS   | Poisson | G-P  |
| Exp-5  | GLaSS   | Bursty  | G-B  |
| Exp-6  | GLaSS   | Mixed   | G-M  |

---

## 4. 实验执行步骤

### 4.1 方式一：使用 Makefile（推荐）

```bash
# 查看帮助
make help

# 查看可用调度器
make list-schedulers
```

#### Step 1: 运行静态调度实验

```bash
# Exp-1: Static + Poisson
make static CARDS=4 TASKS=100 STEPS=60 SEED=42 ARRIVAL_MODE=poisson

# Exp-2: Static + Bursty
make static CARDS=4 TASKS=100 STEPS=60 SEED=42 ARRIVAL_MODE=bursty

# Exp-3: Static + Mixed
make static CARDS=4 TASKS=100 STEPS=60 SEED=42 ARRIVAL_MODE=mixed
```

#### Step 2: 运行 GLaSS 动态调度实验

```bash
# Exp-4: GLaSS + Poisson
make dynamic CARDS=4 TASKS=100 STEPS=60 SEED=42 ARRIVAL_MODE=poisson

# Exp-5: GLaSS + Bursty
make dynamic CARDS=4 TASKS=100 STEPS=60 SEED=42 ARRIVAL_MODE=bursty

# Exp-6: GLaSS + Mixed
make dynamic CARDS=4 TASKS=100 STEPS=60 SEED=42 ARRIVAL_MODE=mixed
```

**输出结果**：
- 日志文件：`log/simulation_<timestamp>.log`
- 数据文件：`data/<scheduler>_loads_<timestamp>.csv`

---

### 4.2 方式二：直接运行 Python 脚本

```bash
# 静态调度 + 泊松到达
python main.py \
  --scheduler static \
  --cards 4 \
  --tasks 100 \
  --steps 60 \
  --seed 42 \
  --arrival-mode poisson

# GLaSS 动态调度 + 突发到达
python main.py \
  --scheduler glass \
  --cards 4 \
  --tasks 100 \
  --steps 60 \
  --seed 42 \
  --arrival-mode bursty
```

**完整参数说明**：
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--scheduler` | str | static | 调度算法名称 |
| `--cards` | int | 4 | 神经拟态卡数量 |
| `--tasks` | int | 100 | 总任务数 |
| `--steps` | int | 60 | 仿真时间步数 |
| `--seed` | int | 42 | 随机种子 |
| `--arrival-mode` | str | poisson | 到达模式 |
| `--log-dir` | str | log | 日志目录 |
| `--data-dir` | str | data | 数据目录 |
| `--alpha` | float | 1.0 | 脉冲权重 |
| `--beta` | float | 0.01 | 突触操作权重 |

---

## 5. 数据收集与分析

### 5.1 运行时日志分析

实验运行过程中，所有关键事件都会记录到 `log/simulation_<timestamp>.log`。

**日志内容示例**：

```
2025-12-30 19:12:10 [INFO] Starting dynamic simulation | cards=4 tasks=100 steps=60 seed=42 arrival=poisson
2025-12-30 19:12:10 [INFO] Arrival plan (poisson): [3, 1, 2, 0, 4, ...]
2025-12-30 19:12:11 [INFO] Time step 1
2025-12-30 19:12:11 [INFO] Card 0 load=272.95 tasks=1
2025-12-30 19:12:11 [INFO] Card 1 load=49.62 tasks=1
2025-12-30 19:12:11 [INFO] --- Time Step 1 Global Balancing ---
2025-12-30 19:12:11 [INFO] Load bands | avg=272.97 overload>327.56 underload<218.38
2025-12-30 19:12:11 [INFO] >> System is stable. No migration needed.
...
2025-12-30 19:12:15 [INFO] >>> [MIGRATION] Moving Task 42 from Card 3 to 1
```

**关键日志字段**：
- **Time Step**: 当前仿真步数
- **Card X load=Y tasks=Z**: 卡 X 的负载 Y，承载任务数 Z
- **Global Balancing**: GLaSS 全局均衡触发（仅 glass/dynamic 调度器）
- **[MIGRATION]**: 任务迁移事件（仅动态调度器）

---

### 5.2 CSV 数据文件格式

每次实验会在 `data/` 目录下生成时序负载数据：

**文件格式**：`<scheduler>_loads_<timestamp>.csv`（如 `static_loads_*.csv`、`glass_loads_*.csv`）

**CSV 表头**：
```csv
time_step,card_id,load,tasks
```

**字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `time_step` | int | 时间步序号（1 ~ STEPS） |
| `card_id` | int | 卡编号（0 ~ CARDS-1） |
| `load` | float | 该卡在该时刻的归一化负载 (α·Spikes + β·Ops) |
| `tasks` | int | 该卡上的任务数量 |

**示例数据**：
```csv
time_step,card_id,load,tasks
1,0,272.95,1
1,1,49.62,1
1,2,505.91,1
1,3,263.39,1
2,0,208.11,1
2,1,300.21,2
...
```

---

## 6. 可视化与对比

### 6.1 绘制负载曲线

使用 [plot/plot_static_loads.py](plot/plot_static_loads.py) 绘制时序负载图：

```bash
# 绘制静态调度的负载曲线
python plot/plot_static_loads.py data/static_loads_20251230_120000.csv

# 绘制 GLaSS 动态调度的负载曲线
python plot/plot_static_loads.py data/glass_loads_20251230_120500.csv
```

**生成文件**：
- `plot/static_loads_20251230_120000.pdf`：多卡负载时序曲线
- `plot/static_loads_20251230_120000_tasks.pdf`：多卡任务数量时序曲线

**图表解读**：
- **负载曲线**：观察各卡负载随时间的波动幅度
  - **Static**: 各卡负载可能严重不均（某些卡过载，某些卡空闲）
  - **GLaSS**: 通过任务迁移拉平负载差异
- **偏差曲线**：每张卡与平均负载的差值
  - 数值越接近 0，负载越均衡

---

### 6.2 绘制任务脉冲活动模式

验证任务的突发特性（Hotspot Burst Dynamics）：

```bash
python plot/plot_task_spikes.py \
  --steps 60 \
  --neuron-count 900 \
  --complexity 1.2 \
  --state-mb 14 \
  --seed 21
```

**生成文件**：
- `plot/task_spikes.pdf`：单任务脉冲计数时序图
- `plot/task_spikes_ops.pdf`：单任务突触操作时序图

**观察重点**：
- 是否出现明显的**周期性突发**（Hotspot Period）
- 突发期与静默期的负载对比

---

## 7. 评估指标计算

### 7.1 负载均衡度（Load Balance）

**定义**：所有卡负载的方差（越小越均衡）

$$\text{Imbalance} = \frac{1}{T} \sum_{t=1}^{T} \left[ \frac{1}{M} \sum_{m=1}^{M} (L_m(t) - \bar{L}(t))^2 \right]$$

**计算方法**（Python 示例）：

```python
import pandas as pd
import numpy as np

# 读取 CSV
df = pd.read_csv('data/glass_loads_20251230_120000.csv')

# 按时间步分组计算方差
imbalance_per_step = []
for t in df['time_step'].unique():
    loads = df[df['time_step'] == t]['load'].values
    variance = np.var(loads)
    imbalance_per_step.append(variance)

avg_imbalance = np.mean(imbalance_per_step)
print(f"Average Load Imbalance: {avg_imbalance:.2f}")
```

**对比目标**：
- **Static**: 高方差（负载不均）
- **GLaSS**: 低方差（动态迁移拉平负载）

---

### 7.2 系统吞吐量（Throughput）

**定义**：单位时间完成的任务数

$$\text{Throughput} = \frac{\text{Completed Tasks}}{T_{total}}$$

**提取方法**：从日志文件中统计 "Tasks completed this step" 事件：

```bash
grep "Tasks completed this step" log/simulation_20251230_120000.log | wc -l
```

**对比目标**：
- **GLaSS** 的吞吐量应 **≥ Static**（因为避免了过载卡的排队延迟）

---

### 7.3 迁移开销（Migration Overhead）

**定义**：动态调度额外产生的迁移次数

**提取方法**：从日志中统计迁移事件：

```bash
grep "\[MIGRATION\]" log/simulation_20251230_120000.log | wc -l
```

**分析指标**：
- **迁移总次数**：越少越好（过多迁移意味着系统不稳定）
- **单次迁移平均收益**：$\Delta L / \text{Cost}$（从日志中提取任务负载和状态大小）

**合理范围**：
- 在 Poisson 模式下，迁移次数应 < 10% 的任务数
- 在 Bursty 模式下，迁移次数可能达到 20%-30%（突发期集中迁移）

---

### 7.4 资源利用率（Utilization）

**定义**：集群整体资源的使用效率

$$\text{Core Utilization} = \frac{1}{T} \sum_{t=1}^{T} \frac{\sum_{m=1}^{M} \text{Cores}_{used}(m, t)}{M \times 512}$$

**计算方法**：从 CSV 中读取任务数，结合任务平均核心需求计算。

**对比目标**：
- **GLaSS** 通过迁移避免资源碎片，利用率应更高

---

## 8. 完整实验脚本示例

以下脚本自动运行所有 6 组实验并生成对比报告：

```bash
#!/bin/bash
# experiment_full.sh - 自动化实验脚本

CARDS=4
TASKS=100
STEPS=60
SEED=42

SCHEDULERS=("static" "glass")
ARRIVALS=("poisson" "bursty" "mixed")

echo "===== GLaSS Experiment: Multi-Scheduler Comparison ====="
echo "Configuration: cards=$CARDS tasks=$TASKS steps=$STEPS seed=$SEED"
echo ""

# 显示可用调度器
echo "Available schedulers:"
make list-schedulers
echo ""

for sched in "${SCHEDULERS[@]}"; do
  for arrival in "${ARRIVALS[@]}"; do
    echo ">>> Running: scheduler=$sched arrival=$arrival"
    python main.py --scheduler $sched --cards $CARDS --tasks $TASKS \
                   --steps $STEPS --seed $SEED --arrival-mode $arrival
    echo ""
  done
done

echo "===== Experiment Completed ====="
echo "Logs saved to: log/"
echo "Data saved to: data/"
echo ""
echo "Next Steps:"
echo "1. Plot load curves:"
echo "   python plot/plot_static_loads.py data/static_loads_<timestamp>.csv"
echo "   python plot/plot_static_loads.py data/glass_loads_<timestamp>.csv"
echo ""
echo "2. Calculate metrics:"
echo "   grep 'Tasks completed' log/*.log"
echo "   grep '\[MIGRATION\]' log/*.log | wc -l"
```

**运行方式**：
```bash
chmod +x experiment_full.sh
./experiment_full.sh
```

---

## 9. 预期实验结果

### 9.1 负载均衡度对比

| 实验组 | 调度策略 | 到达模式 | 平均负载方差 | 说明 |
|--------|---------|---------|-------------|------|
| S-P    | Static  | Poisson | ~8000       | 静态调度下各卡负载严重不均 |
| G-P    | GLaSS   | Poisson | ~2000       | GLaSS 拉平负载，方差降低 75% |
| S-B    | Static  | Bursty  | ~15000      | 突发流量下静态调度崩溃（某些卡极度过载） |
| G-B    | GLaSS   | Bursty  | ~4000       | GLaSS 通过迁移应对突发，方差降低 73% |

### 9.2 迁移开销统计

| 实验组 | 迁移次数 | 迁移/任务比例 | 说明 |
|--------|---------|-------------|------|
| G-P    | ~15     | 15%         | 泊松模式下迁移适中 |
| G-B    | ~35     | 35%         | 突发模式触发更多迁移 |
| G-M    | ~22     | 22%         | 混合模式介于两者之间 |

### 9.3 吞吐量提升

- **Static Poisson**: 完成 95 个任务（5 个因过载排队超时）
- **GLaSS Poisson**: 完成 100 个任务（无超时）
- **吞吐量提升**: +5.3%

---

## 10. 实验检查清单

### 实验前检查

- [ ] 确认 Python 环境可用（`python --version`）
- [ ] 安装可视化依赖（`pip install matplotlib`）
- [ ] 检查 `.env` 配置文件存在且参数正确
- [ ] 确认 `log/` 和 `data/` 目录可写（或运行时自动创建）

### 实验中记录

- [ ] 每组实验运行后保存日志文件名和时间戳
- [ ] 记录任何异常（如任务无法分配、迁移回滚等）
- [ ] 对比动态和静态模式的日志差异

### 实验后分析

- [ ] 绘制所有 6 组实验的负载曲线
- [ ] 计算负载方差、迁移次数、吞吐量
- [ ] 对比 Poisson/Bursty/Mixed 三种模式下的性能差异
- [ ] 分析 GLaSS 的适用场景和局限性

---

## 11. 常见问题与调试

### Q1: 任务无法分配（Pending tasks 一直不减少）

**原因**：集群资源不足，所有卡都无法容纳新任务。

**解决方案**：
- 增加卡数量：`make dynamic CARDS=6`
- 减少任务数量：`make dynamic TASKS=50`
- 降低任务资源需求（修改 [util/task.py](util/task.py) 中的 `neuron_count` 范围）

---

### Q2: 动态调度没有触发任何迁移

**原因**：负载可能未达到过载阈值（`THETA_HIGH=0.85`）。

**诊断方法**：
1. 检查日志中的 "Load bands" 信息：
   ```
   Load bands | avg=272.97 overload>327.56 underload<218.38
   ```
2. 查看是否有卡负载超过 `overload` 阈值

**解决方案**：
- 降低阈值：修改 [schedule/glass.py](schedule/glass.py) 中的 `overload_cutoff`
- 增加任务密度：`python main.py --scheduler glass --tasks 200 --steps 30`（短时间内到达更多任务）

---

### Q3: 可视化脚本报错 `FileNotFoundError`

**原因**：CSV 文件路径不正确或文件未生成。

**解决方案**：
```bash
# 查看已生成的数据文件
ls -lh data/

# 使用完整路径运行绘图脚本
python plot/plot_static_loads.py data/glass_loads_20251230_120000.csv
```

---

## 12. 扩展实验方向

### 12.1 参数敏感性分析

研究 GLaSS 算法关键参数对性能的影响：

| 参数 | 默认值 | 实验范围 | 观察指标 |
|------|--------|---------|---------|
| `THETA_HIGH` | 0.85 | 0.7 ~ 0.95 | 迁移频率与负载均衡度 |
| `GAMMA` | 1.5 | 1.0 ~ 2.5 | 高负载任务迁移比例 |
| `EPOCH_LENGTH` | 500ms | 100ms ~ 2000ms | 调度开销与响应速度 |

---

### 12.2 大规模集群实验

测试 GLaSS 在更大规模下的扩展性：

```bash
# 10 卡 + 500 任务 + 100 时间步
python main.py --scheduler glass --cards 10 --tasks 500 --steps 100

# 20 卡 + 1000 任务 + 200 时间步
python main.py --scheduler glass --cards 20 --tasks 1000 --steps 200
```

**关注指标**：
- 调度决策时间（从日志时间戳计算）
- 迁移次数是否随卡数线性增长

---

### 12.3 异构硬件实验

修改 `.env` 文件模拟异构卡配置（如混合使用不同代次的 Darwin 芯片）：

```bash
# Card 0-1: Darwin3 (512 cores)
# Card 2-3: Darwin2 (256 cores, 降低一半资源)
```

修改 [util/card.py](util/card.py) 支持异构卡初始化。

---

## 13. 参考文档

- **快速开始**：[README.md](../README.md) - 项目概述与快速使用
- **算法理论**：[algorithm.md](algorithm.md) - GLaSS 数学模型与设计原理
- **伪代码实现**：[pseudocode.md](pseudocode.md) - 算法详细步骤
- **调度器实现**：
  - [schedule/base.py](../schedule/base.py) - 调度器基类与注册机制
  - [schedule/glass.py](../schedule/glass.py) - GLaSS 动态调度算法
- **仿真引擎**：[simulation/engine.py](../simulation/engine.py) - 统一仿真执行框架
- **工具模块**：[util/](../util/) - 任务、卡片、指标定义
- **主入口**：[main.py](../main.py) - CLI 命令行接口
- **脚本**：[script/](../script/) - 自动化实验脚本

---

## 14. 总结

本实验通过 **全因子对比设计（2×3）** 系统评估了 GLaSS 动态调度算法在不同工作负载下的性能表现。通过可扩展的调度器框架和完整的可视化工具链，研究者可以快速复现实验并分析关键指标。

**框架特性**：
1. **可扩展调度器**：基于 `BaseScheduler` 抽象类，轻松添加新算法
2. **统一仿真引擎**：`SimulationEngine` 支持任意注册的调度器
3. **命令行友好**：`--scheduler` 参数动态选择调度算法
4. **完整指标体系**：负载快照、迁移事件、吞吐量统计

**核心结论**（基于预期结果）：
1. **负载均衡提升 70%+**：GLaSS 显著降低多卡负载方差
2. **吞吐量提升 5%-10%**：避免过载卡排队延迟
3. **迁移开销可控**：在合理阈值下，迁移次数 < 30% 任务数
4. **突发流量鲁棒性**：在 Bursty 模式下优势最显著

**扩展新调度器**：
```python
# schedule/my_scheduler.py
from schedule.base import BaseScheduler, register_scheduler

class MyScheduler(BaseScheduler):
    @property
    def name(self) -> str:
        return "MyScheduler"
    
    def step(self, time_step: int) -> None:
        # 实现调度逻辑
        pass

register_scheduler("my_scheduler", MyScheduler)
```

**下一步工作**：
- 在真实 Darwin3 硬件上验证仿真结果
- 支持累积负载统计的持久化存储和分析
- 引入网络拓扑约束（考虑卡间通信延迟）
- 集成预测模型（基于历史负载预测未来突发）
- 实现更复杂的任务到达模式（如周期性、自相似性）
