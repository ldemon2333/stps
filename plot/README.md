# Plot Directory

本目录包含用于可视化 GLaSS 调度器仿真结果的脚本。

## 支持的调度器

所有绘图脚本均支持以下调度器，可从 CSV 文件名或内容中自动识别：

| 调度器名称 | CSV 文件前缀 | 显示名称 | 类型 |
|-----------|-------------|---------|------|
| Gandiva   | `gandiva_`  | Gandiva | 动态调度 |
| Glass     | `glass_`    | Glass   | 动态调度 (GG) |
| Glass-DRL | `glass_drl_`| Glass-DRL | 深度强化学习 |
| BestFit   | `bestfit_`  | BestFit | 静态调度 |
| DRF       | `drf_`      | DRF     | 静态调度 |
| P2C       | `p2c_`      | P2C     | 静态调度 |
| RoundRobin| `rr_`       | RR      | 静态调度 |

## 脚本说明

### 1. `plot_step_loads.py`

**功能：** 绘制静态调度器（Static Scheduler）在仿真过程中的负载变化。

**输入：** CSV 文件（格式：`static_loads_*.csv`）
- `time_step`: 仿真时间步
- `card_id`: 处理卡 ID
- `load`: 该时刻的负载值
- `tasks`: 该卡上运行的任务数

**输出：** 
- `{filename}.pdf` - 主要包含三个子图：
  1. **主图**：所有卡在不同时间步的负载曲线
  2. **负载方差子图**：每个时间步的负载方差（红色填充），反映负载均衡程度
  3. **偏离平均子图**：每张卡相对于平均负载的偏离量（针对每张卡一个子图）
- `{filename}_tasks.pdf` - 每张卡上的任务数随时间的变化

**使用方法：**
```bash
python plot/plot_step_loads.py data/static_loads_20251231_143004.csv
```

---

### 2. `plot_task_spikes.py`

**功能：** 模拟单个任务在其生命周期内的 spike 强度变化，展示任务计算负载的动态特性。

**输入：** 命令行参数
- `--steps`: 仿真的物理时刻数（默认 50）
- `--neuron-count`: 任务的神经元数量（默认 800）
- `--complexity`: 复杂度系数（默认 1.0）
- `--state-mb`: 任务状态大小，单位 MB（默认 12.0）
- `--seed`: 随机种子（默认 21）
- `--output`: 输出图表路径（默认 `plot/task_spikes.pdf`）

**输出：** PDF 图表，展示：
- 任务在每个时刻的 spike 数量（蓝色柱状图）
- 突触操作数量（橙色曲线）
- 平均值和波动范围

**使用方法：**
```bash
# 默认参数
python plot/plot_task_spikes.py

# 自定义参数
python plot/plot_task_spikes.py --steps 100 --neuron-count 1000 --complexity 2.0
```

---

### 3. `plot_compare_variance.py`

**功能：** 对比两个不同调度器（如 GLaSS 动态调度器 vs Static 静态调度器）的负载均衡性能。

**输入：** 多个 CSV 文件（来自不同调度器的仿真结果）
- `glass_loads_*.csv` - GLaSS 调度器的负载数据
- `static_loads_*.csv` - Static 调度器的负载数据

**输出：**
- `data/variance_comparison_*.csv` - 包含两个调度器每个时间步的负载方差对比
  ```
  time_step,GLaSS (Dynamic),Static
  1,188578.63,188578.63
  2,1123939.64,1123939.64
  ...
  ```
- `plot/compare_variance.pdf` - 可视化对比图表，包含：
  1. **主图**：两个调度器的负载方差曲线对比（带平均值标注）
  2. **差值图**：方差差异柱状图
     - **绿色柱**：GLaSS 表现更优（方差更低）
     - **红色柱**：Static 表现更优
     - **虚线**：平均差值

**使用方法：**
```bash
# 自动检测标签
python plot/plot_compare_variance.py data/glass_loads_*.csv data/static_loads_*.csv

# 指定自定义标签
python plot/plot_compare_variance.py data/glass_loads_*.csv data/static_loads_*.csv \
  --labels "GLaSS" "Static Baseline" --output plot/custom_compare.pdf
```

---

### 4. `plot_cv.py`

**功能：** 绘制并对比多个调度算法的系数变异数（Coefficient of Variation, CV），衡量集群中各卡负载分布的离散程度。

**原理：** 
$$CV = \frac{\sigma}{\mu} = \frac{\sqrt{\text{variance}}}{\text{mean}}$$

- **范围**：$[0, \infty)$
- **CV = 0**：所有卡负载完全相同（完美均衡）
- **CV 越小**：负载分布越均匀（调度器越优秀）
- **CV 对长尾分布敏感**：当某张卡负载激增时，CV 会快速上升

**输入：** 多个 CSV 文件（来自不同调度器的仿真结果）
- 文件格式：`{scheduler}_poisson_loads_*.csv` 或 `{scheduler}_bursty_loads_*.csv`
- 列定义：`time_step`, `card_id`, `load`

**输出：** 
- `plot/cv_comparison.png` - PNG 图表，展示：
  - **主线**：GLaSS（深蓝色实线）
  - **对比线**：其他算法中 CV 最低的（如 RoundRobin，深红色虚线）
  - **图例**：包含每个算法的平均 CV 值
  - 采用稀疏标记（每5步一个）提高可读性

**使用方法：**
```bash
# 对比 GLaSS 与其他算法（含 Glass-DRL）
python plot/plot_cv.py data/glass_poisson_loads_*.csv data/drf_poisson_loads_*.csv \
  data/p2c_poisson_loads_*.csv data/bestfit_poisson_loads_*.csv \
  data/roundrobin_poisson_loads_*.csv data/glass_drl_poisson_loads_*.csv \
  --labels GLaSS DRF P2C BestFit RoundRobin Glass-DRL --output plot/cv_comparison.png

# 自动从文件名推断标签（推荐）
python plot/plot_cv.py data/*_loads_*.csv --output plot/cv_comparison.png
```

**典型输出：**
```
GLaSS: Avg CV = 0.2906, Max CV = 1.7321, Min CV = 0.0419
RoundRobin: Avg CV = 0.3165, Max CV = 1.7321, Min CV = 0.0500
Glass-DRL: Avg CV = 0.2750, Max CV = 1.5000, Min CV = 0.0380
Saved CV comparison plot to plot/cv_comparison.png
```

---

### 5. `plot_jfi.py`

**功能：** 绘制并对比多个调度算法的 Jain's Fairness Index (JFI)，评估资源分配的公平性。

**原理：** 
$$\mathcal{J} = \frac{(\sum_{m=1}^{M} L_m)^2}{M \cdot \sum_{m=1}^{M} L_m^2}$$

- **范围**：$[1/M, 1]$，其中 $M$ 是卡数量
- **JFI = 1**：完全公平，所有卡负载相同
- **JFI = 1/M**：最不公平，仅一张卡有负载
- **JFI > 0.8**：一般认为达到良好公平性
- **对长尾分布敏感**：极端不平衡情况下 JFI 会骤降

**输入：** 多个 CSV 文件（来自不同调度器的仿真结果）
- 文件格式：`{scheduler}_poisson_loads_*.csv` 或 `{scheduler}_bursty_loads_*.csv`
- 列定义：`time_step`, `card_id`, `load`

**输出：** 
- `plot/jfi_comparison.png` - PNG 图表，展示：
  - **主线**：GLaSS（深蓝色实线）
  - **对比线**：其他算法中 JFI 最高的（如 RoundRobin，深红色虚线）
  - **图例**：包含每个算法的平均 JFI 值
  - **参考线**：JFI=0.8（虚线，良好公平性阈值）
  - 采用稀疏标记（每5步一个）提高可读性

**使用方法：**
```bash
# 对比 GLaSS 与其他算法（含 Glass-DRL）
python plot/plot_jfi.py data/glass_poisson_loads_*.csv data/drf_poisson_loads_*.csv \
  data/p2c_poisson_loads_*.csv data/bestfit_poisson_loads_*.csv \
  data/roundrobin_poisson_loads_*.csv data/glass_drl_poisson_loads_*.csv \
  --labels GLaSS DRF P2C BestFit RoundRobin Glass-DRL --output plot/jfi_comparison.png

# 自动从文件名推断标签（推荐）
python plot/plot_jfi.py data/*_loads_*.csv --output plot/jfi_comparison.png
```

**典型输出：**
```
GLaSS: Avg JFI = 0.9108, Min JFI = 0.2500, Max JFI = 0.9983
RoundRobin: Avg JFI = 0.8991, Min JFI = 0.2500, Max JFI = 0.9975
Glass-DRL: Avg JFI = 0.9250, Min JFI = 0.3000, Max JFI = 0.9990
Saved JFI comparison plot to plot/jfi_comparison.png
```

---

### 7. `LIF.py`

**功能：** 绘制并对比多个调度算法的负载不平衡因子（Load Imbalance Factor, LIF），用于快速评估负载分布的不平衡程度。

**原理：** 
$$\text{LIF} = \frac{\rho_{\max}}{\rho_{\text{avg}}} - 1$$

- **范围**：$[0, \infty)$，无上界
- **LIF = 0**：完美均衡（所有卡负载相同）
- **LIF = 0.2**：中等不平衡（最大负载是平均负载的 1.2 倍）
- **LIF = 1.0**：严重不平衡（最大负载是平均负载的 2 倍）
- **计算简单**：仅需最大值和平均值，计算复杂度 O(M)
- **直观性强**：直接反映负载倍数关系

**输入：** 多个 CSV 文件（来自不同调度器的仿真结果）
- 文件格式：`{scheduler}_poisson_loads_*.csv` 或 `{scheduler}_bursty_loads_*.csv`
- 列定义：`time_step`, `card_id`, `load`

**输出：** 
- `plot/lif_comparison.png` - PNG 图表，展示：
  - 每个算法在每个时间步的 LIF 值
  - 平均 LIF 和最大 LIF 统计
  - 稀疏标记（每 5 步一个）提高可读性
  - 参考线：LIF=0.2（中等不平衡阈值）

**使用方法：**
```bash
# 对比所有算法的 LIF（含 Glass-DRL）
python plot/LIF.py data/glass_poisson_loads_*.csv data/drf_poisson_loads_*.csv \
  data/p2c_poisson_loads_*.csv data/bestfit_poisson_loads_*.csv \
  data/roundrobin_poisson_loads_*.csv data/glass_drl_poisson_loads_*.csv \
  --labels GLaSS DRF P2C BestFit RoundRobin Glass-DRL --output plot/lif_comparison.png

# 自动从文件名推断标签（推荐）
python plot/LIF.py data/*_loads_*.csv --output plot/lif_comparison.png
```

**典型输出：**
```
GLaSS: Avg LIF = 1.3648, Min LIF = 0.3739, Max LIF = 15.0000
DRF: Avg LIF = 0.3992, Min LIF = 0.0498, Max LIF = 3.0000
P2C: Avg LIF = 1.4575, Min LIF = 0.3640, Max LIF = 15.0000
BestFit: Avg LIF = 2.0373, Min LIF = 0.7431, Max LIF = 15.0000
RoundRobin: Avg LIF = 1.7974, Min LIF = 0.5981, Max LIF = 15.0000
Saved LIF comparison plot to plot/lif_comparison.png
```

---

### 8. `plot_drl_comparison.py`

**功能：** 专用于 GLaSS-DRL 与基线调度器的多维对比分析，生成 CV、JFI、LIF 和 SLA 违规率的对比图表。

**输入：** 自动扫描 `data/` 目录下所有调度器的 CSV 文件
- 支持 `glass_drl_loads_*.csv`、`glass_loads_*.csv`、`gandiva_loads_*.csv` 等
- 同时支持 `glass-drl`、`glass_drl`、`glassdrl` 三种文件名变体

**输出：** 多个对比图表（PNG 格式），包含 CV、JFI、LIF 时间序列对比

**使用方法：**
```bash
# 使用默认数据目录
python plot/plot_drl_comparison.py

# 指定数据目录和输出目录
python plot/plot_drl_comparison.py --data-dir data --output-dir figures
```

---

## 指标解释

### 负载方差（Load Variance）

$$\text{Variance} = \frac{1}{m} \sum_{i=1}^{m} (L_i - \bar{L})^2$$

其中：
- $m$ 是处理卡数量
- $L_i$ 是卡 $i$ 的负载
- $\bar{L}$ 是平均负载

**意义：**
- 方差 = 0：所有卡负载完全相同（完美平衡）
- 方差越大：卡之间的负载差异越大（不平衡）

**用途：** 评估调度器的负载均衡效果

---

### 系数变异数（Coefficient of Variation, CV）

$$CV = \frac{\sigma}{\mu} = \frac{\sqrt{\text{variance}}}{\text{mean}}$$

其中：
- $\sigma$ 是负载的标准差
- $\mu$ 是负载的平均值

**特点：**
- **归一化指标**：通过除以平均值，消除基础负载水平的影响
- **范围**：$[0, \infty)$，无上界
- **CV = 0**：完美均衡，所有卡负载相同
- **CV 越小越好**：反映负载分布的离散程度
- **敏感性**：对长尾分布（tail spikes）敏感，某张卡负载激增时快速上升

**应用场景：** 用于不同基础负载水平的调度器对比（如不同复杂度任务或不同工作负载配置）

---

### Jain's Fairness Index (JFI)

$$\mathcal{J} = \frac{(\sum_{m=1}^{M} L_m)^2}{M \cdot \sum_{m=1}^{M} L_m^2}$$

其中：
- $M$ 是处理卡数量
- $L_m$ 是卡 $m$ 的负载

**特点：**
- **范围**：$[1/M, 1]$，对于 4 张卡为 $[0.25, 1]$
- **JFI = 1**：完美公平，所有卡负载相同
- **JFI = 1/M**：最不公平，仅一张卡有负载
- **JFI > 0.8**：一般认为达到良好公平性
- **敏感性**：对长尾分布极敏感，极端不平衡情况下快速下降到 0.25

**应用场景：** 评估资源分配的公平性，特别强调对极端情况的关注

---

### 指标对比

| 指标 | 范围 | 最优值 | 敏感性 | 应用场景 |
|-----|------|--------|--------|---------|
| **方差** | $[0, \infty)$ | 0 | 高 | 基础负载均衡评估 |
| **CV** | $[0, \infty)$ | 0 | 中 | 不同工作负载对比 |
| **JFI** | $[1/M, 1]$ | 1 | 很高 | 公平性评估，对长尾敏感 |
| **LIF** | $[0, \infty)$ | 0 | 高 | 快速评估负载不平衡程度 |

### 负载不平衡因子（Load Imbalance Factor, LIF）

$$\text{LIF} = \frac{\rho_{\max}}{\rho_{\text{avg}}} - 1$$

其中：
- $\rho_{\max}$ 是所有卡的最大负载
- $\rho_{\text{avg}}$ 是所有卡的平均负载

**特点：**
- **范围**：$[0, \infty)$，无上界
- **LIF = 0**：完美均衡，所有卡负载相同
- **LIF = 0.2**：中等不平衡，最大负载是平均负载的 1.2 倍
- **LIF = 1.0**：严重不平衡，最大负载是平均负载的 2 倍
- **计算简单**：仅需每个时间步的最大值和平均值
- **直观性强**：直接反映负载倍数关系

**应用场景：** 快速评估负载不平衡程度，适合实时监控和对比

---

## 典型工作流

1. **运行仿真** (在项目根目录)
   ```bash
   make compare    # 运行所有调度器（含 Glass-DRL）
   # 或单独运行
   make glass-drl  # 运行 GLaSS-DRL 调度器
   make glass      # 运行 GLaSS (GG) 动态调度器
   make gandiva    # 运行 GLaSS 动态调度器
   ```

2. **绘制单个调度器的结果**
   ```bash
   python plot/plot_step_loads.py data/glass_drl_loads_*.csv
   python plot/plot_step_loads.py data/glass_loads_*.csv
   ```

3. **对比所有调度器**
   ```bash
   # 自动扫描所有加载数据
   python plot/plot_cv.py data/*_loads_*.csv
   python plot/plot_jfi.py data/*_loads_*.csv
   python plot/LIF.py data/*_loads_*.csv
   
   # DRL 专用多维对比
   python plot/plot_drl_comparison.py --data-dir data
   ```

4. **可视化任务特性**（可选）
   ```bash
   python plot/plot_task_spikes.py --steps 100
   ```

5. **查看结果**
   - 图表：`plot/*.png`, `figures/*.png`
   - 数据：`data/variance_comparison_*.csv`

---

## 输出示例

运行 `plot_compare_variance.py` 后的终端输出：

```
GLaSS (Dynamic): Avg Variance = 599274.77, Max Variance = 1991043.51
Static: Avg Variance = 990254.82, Max Variance = 3762161.90
Saved variance data to data/variance_comparison_20251231_152404.csv
Saved comparison plot to plot/compare_variance.pdf
```

**解释：**
- GLaSS 的平均方差约为 Static 的 60.5%
- GLaSS 比 Static 负载均衡度提升约 **39.5%**
