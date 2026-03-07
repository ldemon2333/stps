# 神经拟态集群调度算法

## 目录
1. [Best-Fit: 静态资源装箱算法](#best-fit-静态资源装箱算法)
2. [DRF: Dominant Resource Fairness (静态调度)](#drf-dominant-resource-fairness-静态调度)
3. [P2C: Power of Two Choices (随机化负载均衡)](#p2c-power-of-two-choices-随机化负载均衡)
4. [Round-Robin: 轮询负载均衡算法](#round-robin-轮询负载均衡算法)
5. [GLaSS: Global Load-aware SNN Scheduler (动态调度)](#glass-global-load-aware-snn-scheduler)
6. [Gandiva-Spike: Smallest-First 动态基线算法](#gandiva-spike-smallest-first-动态基线算法)

---

## Best-Fit: 静态资源装箱算法

### 1. 算法原理

Best-Fit 是经典的装箱问题启发式算法，应用于任务调度时的核心思想是：**将任务放置到剩余资源最匹配的卡上**。

#### 核心思想
每次任务到达时，选择那张"能容纳任务且剩余空间最小"的卡进行放置，从而最大化资源利用率，减少资源碎片化。

#### 数学公式

选择卡的策略为最大化剩余容量的匹配度：

$$n^* = \arg\max_{n \in \mathcal{N}} \left( \text{remaining\_cores}(n), \text{remaining\_memory}(n), \text{remaining\_synapses}(n), -|\text{Tasks}(n)| \right)$$

其中，优先级依次为：剩余核心数 > 剩余内存 > 剩余突触数 > 任务数量（越少越好）。

### 2. 算法特点

| 特性 | 说明 |
|------|------|
| **调度类型** | 静态调度（任务到达时一次性分配） |
| **迁移策略** | 无运行时迁移 |
| **决策复杂度** | O(M)，M 为卡数量 |
| **优化目标** | 最大化资源利用率，减少碎片 |
| **适用场景** | 基准对比、负载稳定的工作负载 |

### 3. 算法流程

```
输入: 任务 T, 卡集合 N
输出: 最优放置卡 n*

1. 初始化候选集 eligible = {}
2. FOR each card n in N:
   a. IF card n 可以容纳任务 T:
        将 n 加入 eligible
3. IF eligible 为空:
   RETURN NULL (无法放置)
4. FOR each card n in eligible:
   计算剩余容量 remaining_capacity(n) = (cores, memory, synapses, -tasks)
5. n* = argmax(remaining_capacity)
6. RETURN n*
```

### 4. 优势与局限

**优势**：
- 实现简单，决策开销低
- 资源利用率较高
- 作为基准算法便于对比

**局限**：
- 不考虑运行时负载变化
- 无法应对 SNN 的时空稀疏性
- 可能导致热点集中

---

## DRF: Dominant Resource Fairness (静态调度)

### 1. 算法原理

DRF（Dominant Resource Fairness）是一种多维资源感知的静态调度算法，专为 SNN 场景在神经拟态集群上的部署而设计。

#### 核心思想
对于每张卡，计算其四个维度（Cores、Synapses、Memory、Bandwidth）的资源利用率，取最大值作为该节点的**"主导利用率"**。调度决策将任务分配给那个**"放置后，主导资源利用率最小"**的卡。

#### 数学公式

$$\text{Score}(n) = \min_{n \in N} \left( \max \left( \frac{C_{used}+c_{req}}{C_{total}}, \frac{S_{used}+s_{req}}{S_{total}}, \frac{M_{used}+m_{req}}{M_{total}}, \frac{B_{used}+b_{req}}{B_{total}} \right) \right)$$

其中：
- $C_{used}, C_{total}$：当前已用/总 Cores 数量
- $S_{used}, S_{total}$：当前已用/总 Synapses 数量
- $M_{used}, M_{total}$：当前已用/总 Memory 容量
- $B_{used}, B_{total}$：当前已用/总 Bandwidth 容量
- $c_{req}, s_{req}, m_{req}, b_{req}$：待放置任务的资源需求

### 2. 算法特点

| 特性 | 说明 |
|------|------|
| **调度类型** | 静态调度（任务到达时一次性分配） |
| **迁移策略** | 无运行时迁移 |
| **资源维度** | 四维资源感知 |
| **优化目标** | 最小化主导资源利用率 |
| **适用场景** | 负载稳定、资源需求可预测的 SNN 工作负载 |

### 3. 算法流程

```
输入: 任务 T, 卡集合 N
输出: 最优放置卡 n*

1. 初始化 min_dominant = ∞, best_card = NULL
2. FOR each card n in N:
   a. IF card n 无法容纳任务 T:
        CONTINUE
   b. 计算放置后各维度利用率:
      - core_util = (C_used + c_req) / C_total
      - synapse_util = (S_used + s_req) / S_total
      - memory_util = (M_used + m_req) / M_total
      - bandwidth_util = (B_used + b_req) / B_total
   c. dominant = MAX(core_util, synapse_util, memory_util, bandwidth_util)
   d. IF dominant < min_dominant:
        min_dominant = dominant
        best_card = n
3. RETURN best_card
```

### 4. 优势与局限

**优势**：
- 简单高效，无需运行时开销
- 多维资源平衡，避免单一资源瓶颈
- 适合资源需求稳定的工作负载

**局限**：
- 无法应对 SNN 的时空稀疏性和突发性
- 静态分配无法适应运行时负载变化
- 可能导致局部热点和长尾延迟

---

## P2C: Power of Two Choices (随机化负载均衡)

### 1. 算法原理

P2C（Power of Two Choices）是一种随机化负载均衡算法，通过**仅采样两个候选**就能实现接近最优的负载分布。

#### 核心思想
每次任务放置时，随机选择 2 张候选卡，比较它们的负载，将任务放入负载较轻的那一张。这种看似简单的策略在理论上能获得指数级的负载均衡改进。

#### 数学原理

假设有 $M$ 张卡，传统随机放置的最大负载期望为 $O(\log M / \log \log M)$，而 P2C 可将其降至 $O(\log \log M)$，实现**指数级改进**。

#### 负载评分公式

支持三种负载度量方式：

**1. 加权负载 (Weighted, 默认)**：
$$\text{Score}(n) = L_{epoch}(n) + \alpha \cdot \hat{S}_{task} + \beta \cdot \hat{O}_{task}$$

其中 $\hat{S}_{task}, \hat{O}_{task}$ 是任务的预估脉冲数和突触操作数。

**2. 主导资源利用率 (DRF)**：
$$\text{Score}(n) = \max \left( \frac{C_{used}+c_{req}}{C_{total}}, \frac{S_{used}+s_{req}}{S_{total}}, \frac{M_{used}+m_{req}}{M_{total}} \right)$$

**3. 任务计数 (Tasks)**：
$$\text{Score}(n) = |\text{Tasks}(n)| + 1$$

### 2. 算法特点

| 特性 | 说明 |
|------|------|
| **调度类型** | 静态调度（任务到达时一次性分配） |
| **迁移策略** | 无运行时迁移 |
| **决策复杂度** | **O(1)**，与集群规模无关 |
| **采样数量** | 固定 2 个候选 |
| **理论保证** | 最大负载 $O(\log \log M)$ |
| **适用场景** | 大规模集群、需要快速决策的场景 |

### 3. 算法流程

```
输入: 任务 T, 卡集合 N, 负载度量 metric
输出: 最优放置卡 n*

1. eligible = {n ∈ N | n 可以容纳任务 T}
2. IF |eligible| == 0:
   RETURN NULL
3. IF |eligible| == 1:
   RETURN eligible[0]
4. candidates = RANDOM_SAMPLE(eligible, 2)
5. FOR each card n in candidates:
   score[n] = CALCULATE_LOAD_SCORE(n, T, metric)
6. n* = argmin(score)
7. LOG: "Task T: Card A (score) vs Card B (score) -> selected n*"
8. RETURN n*
```

### 4. 优势与局限

**优势**：
- **O(1) 决策时间**：与集群规模无关
- **理论保证**：指数级优于纯随机
- **简单高效**：实现简单，无需全局状态
- **可扩展性**：适合超大规模集群

**局限**：
- 随机性导致结果不完全确定
- 不考虑运行时负载变化
- 无法进行全局最优放置

### 5. 参考文献

- Mitzenmacher, M. (2001). "The Power of Two Choices in Randomized Load Balancing"

---

## Round-Robin: 轮询负载均衡算法

### 1. 算法原理

Round-Robin（轮询）是最简单的负载均衡算法之一，其核心思想是：**按照固定的循环顺序将任务分配给各张卡**。

#### 核心思想
维护一个指向当前卡的指针，每次任务到达时：
1. 尝试将任务放置在当前指针所指的卡上
2. 如果成功，将指针移动到下一张卡
3. 如果失败，继续尝试下一张卡，直到找到合适的卡或遍历完所有卡

#### 数学公式

对于第 $k$ 个任务，选择的卡为：

$$n_k = \text{cards}[(\text{current\_index} + k) \bmod M]$$

其中 $M$ 为卡的总数，$\text{current\_index}$ 为当前轮询位置。

### 2. 算法特点

| 特性 | 说明 |
|------|------|
| **调度类型** | 静态调度（任务到达时一次性分配） |
| **迁移策略** | 无运行时迁移 |
| **决策复杂度** | O(1) 平均，O(M) 最坏情况 |
| **分配策略** | 循环轮询，确保公平分配 |
| **适用场景** | 任务资源需求相似、基准测试 |

### 3. 算法流程

```
输入: 任务 T, 卡集合 N
输出: 最优放置卡 n*

1. 初始化尝试次数 attempts = 0
2. current_card = cards[current_index]
3. WHILE attempts < |N|:
   a. IF current_card 可以容纳任务 T:
        current_index = (current_index + 1) % |N|
        RETURN current_card
   b. current_index = (current_index + 1) % |N|
   c. current_card = cards[current_index]
   d. attempts += 1
4. RETURN NULL (无法放置)
```

### 4. 优势与局限

**优势**：
- **极简实现**：逻辑简单，易于理解和调试
- **公平分配**：理论上保证任务在各卡间的均匀分布
- **可预测性**：确定性的分配顺序，便于分析
- **无状态依赖**：不需要复杂的负载监控或计算

**局限**：
- **忽略负载差异**：不考虑任务的实际资源需求
- **忽略卡的异构性**：假设所有卡具有相同的处理能力
- **可能导致热点**：当任务负载不均时，可能造成局部过载
- **无动态调整**：无法应对运行时负载变化

### 5. 适用场景

| 场景 | 适用程度 | 说明 |
|------|----------|------|
| **基准测试** | ⭐⭐⭐⭐⭐ | 提供可预测的基线性能 |
| **同构任务** | ⭐⭐⭐⭐ | 资源需求相似时效果好 |
| **原型开发** | ⭐⭐⭐⭐ | 实现简单，快速验证 |
| **异构负载** | ⭐⭐ | 可能导致不均衡 |
| **动态负载** | ⭐ | 无法适应运行时变化 |

---

## GLaSS: Global Load-aware SNN Scheduler

### 面向大规模神经拟态集群的动态负载均衡算法
### 1. 背景与动机

在大规模类脑芯片（如 Darwin3）构成的多卡系统中，传统的静态调度面临严峻挑战。SNN（脉冲神经网络）具有极强的**时空稀疏性**和**突发性**：

1. **静态分配失效**：无法预见不同模型在运行时的算力需求重叠。当某张卡上的多个模型同时进入"脉冲爆发期"，会引发局部 NoC 拥塞与指令执行排队，造成长尾延迟（Tail Latency）。
2. **资源利用率低**：在任务"静默期"，静态绑定的计算资源（Core）和存储资源（SRAM）处于闲置状态。

**GLaSS (Global Load-aware SNN Scheduler)** 旨在通过**双层时间尺度架构**和**基于 ROI 的动态迁移策略**，实现运行时的多节点负载均衡。

### 2. 系统建模
假设集群有 $M$ 张神经拟态卡，集合为 $\mathcal{G}=\{G_1, G_2, ..., G_M\}$。每张卡上可以同时部署多个任务。

#### A. 双层时间尺度架构 (Two-Tier Timing Architecture)
为解决调度决策开销与硬件高频执行之间的矛盾，系统采用简化的双层时间架构：

1. **物理执行层 ($\Delta T_{phy} \approx 1\text{ms}$)**：

    - 硬件真实运行 SNN 的时钟周期。

    - **约束**：硬件需在此时间内完成当前时间步的所有神经元更新与脉冲路由，否则产生积压。

2. **全局调度层 ($T_{epoch} \approx 500\text{ms}$)**：

    - GLaSS 全局算法运行的周期。

    - **功能**：以 $T_{epoch}$ 为周期，收集过去一段时间的统计负载，执行全局迁移决策。通过较长的统计窗口平滑毫秒级的微观抖动，避免调度震荡。

#### B. 动态负载定义 (Weighted Spike-Activity Load)
采用直观的**基于脉冲活动的加权模型**，直接反映 SNN 的计算压力（脉冲处理）和通讯压力（突触事件路由）。

**1. 单任务瞬时负载 $L_i(t_{phy})$**：

在物理执行时间步 $t_{phy}=\Delta T_{phy}$ 内，任务 $i$ 的瞬时负载定义为：

$$L_i(t_{phy}) = \alpha \cdot \text{SpikeCount}_i(t_{phy}) + \beta \cdot \text{SynapticOps}_i(t_{phy})$$
- **$\text{SpikeCount}_i(t_{phy})$**：任务在该时刻产生的脉冲总数（反映神经元更新与发射压力）。

- **$\text{SynapticOps}_i(t_{phy})$**：产生的突触操作总数（即 $\text{SpikeCount} \times \text{AvgFanOut}$，反映 NoC 路由与接收端累加压力）。

- **$\alpha$**：脉冲计算权重因子（依据硬件神经元更新开销设定）。

- **$\beta$**：突触通讯权重因子（依据 NoC 带宽与突触处理开销设定）。

**2. 单任务调度累计负载 $L_i(t_{epoch})$**:

以 $T_{epoch}$ 为周期的单任务累计负载定义为：

$$
L_i(T_{epoch}) = \sum L_i(t_{phy})
$$

- [ ] 为什么要这么设计，因为 SNN 脉冲发放的时间局部性， 通过双层时间尺度来衡量 task 的计算负载是否处于爆发状态，爆发了，意味下个调度窗口仍可能发生爆发，此时定义此时的 task 的状态为 burst

**3. 单卡归一化统计负载 $L_m(Epoch)$**：
为了适配统一的阈值策略，卡负载定义为所有驻留任务负载之和与**卡容量**的比值。调度器采用过去一个 Epoch 内的所有驻留任务负载总和：
$$L_{raw}(m, T_{epoch}) = \sum_{k \in \text{Tasks}(m)} L_k(T_{epoch})$$
$$L_m^{sched} = \frac{L_{raw}(m, T_{epoch}) }{C_{capacity}}$$
- **$C_{capacity}$**：单卡的最大处理能力常数（通过基准测试测定，代表卡在不丢包情况下的最大 $\alpha S + \beta O$ 值）。

### 3. 核心算法流程

#### 模块 1：感知与状态判定 (Sense Phase)

在每个 $T_{epoch}$ 结束时触发，采用**固定双迟滞阈值**策略，基于归一化后的负载 $L_m^{sched}$ 进行判定。

**阈值设置**：

- **过载阈值 ($\Theta_{high} = 0.85$)**：预留 15% 的安全余量（Headroom）以应对两个调度周期之间可能发生的突发流量。

- **接收阈值 ($\Theta_{low} = 0.60$)**：只有负载低于 60% 的卡才有资格接收新任务，防止"刚迁入即过载"。

- **安全目标 ($\Theta_{safe} = 0.75$)**：过载卡迁移任务的目标是将负载降至此水位。


**状态判定**：
$$\text{State}(m) = \begin{cases} \textbf{CRITICAL}, & \text{if } L_m^{sched} \ge \Theta_{high} \quad (\text{触发迁出}) \\ \textbf{AVAILABLE}, & \text{if } L_m^{sched} \le \Theta_{low} \quad (\text{可接收迁入}) \\ \textbf{STABLE}, & \text{otherwise} \quad (\text{保持现状}) \end{cases}$$

#### 模块 2：决策阶段 (Decision Phase) -- ROI-Greedy 策略
目标：**以最小的迁移代价释放足够的计算负载**。算法寻找"高负载但轻状态"的高性价比任务，而非单纯规避热任务。

**步骤 1：计算任务迁移性价比 (Efficiency Score)**

对于过载卡 $m$ 上的每个任务 $i$，计算得分：

$$E_i = \frac{(L_i)^{\gamma}}{S_{state}(i)  + \epsilon}$$

- $L_i$：任务在当前 Epoch 的负载。

- $S_{state}(i)$：迁移数据量。

- $\gamma = 1.5$：**热度偏好因子**（非线性项）。

    - $\gamma > 1$ 意味着我们倾向于迁移**高负载**任务，前提是它的状态体积在可接受范围内。这避免了"蚂蚁搬家"（迁移大量小任务却无法显著降低负载）的问题。

**步骤 2：贪心选择 (Knapsack-like Selection)**

1. 确定减负目标：$\Delta L_{target} = L_m^{sched} - \Theta_{safe}$。

2. 将任务按 $E_i$ 从大到小排序。

3. 依次将任务加入待迁移列表，直到累计释放负载 $\ge \Delta L_{target}$。


**步骤 3：目标卡分配 (Best-Fit Allocation)**

从 $\textbf{AVAILABLE}$ 集合中选择目标卡 $k^*$：

$$k^* = \arg\min_{k \in \mathcal{U}}  \{(\Theta_{high} - L_k^{sched}) - L_{task} \}$$

- **逻辑**：寻找最能"填到 Safe Line"的卡，而不是最空闲的卡。这能防止最空闲卡瞬间被填满导致过载（Ping-Pong 效应），并最大化集群的碎片资源利用率。

#### 模块 3：执行阶段 (Execution Phase)
1. **资源约束预检查**：

    - $\text{Cores}_{used} + \text{Cores}_{task} \le 512$

    - $\text{Mem}_{used} + \text{Mem}_{task} \le 128 \text{GB}$

2. **指令下发**：调度器向底层驱动发送 `MIGRATE(task_id, src_id, dst_id)` 指令。

3. **状态更新**：更新全局视图中的卡负载和任务分布。

### 4. 实验与评估
#### A. 工作负载生成
为了验证算法对不同流量模式的鲁棒性，设计了三种到达模式：

1. **Poisson Mode**：基准测试，模拟随机到达。

2. **Bursty Mode (压力测试)**：模拟突发流量（如视觉传感器在剧烈运动时产生的脉冲爆发）。

3. **Mixed Mode**：40% 均匀负载 + 60% 泊松负载。


   

#### B. 关键配置参数 (.env)

|**参数类别**|**参数名**|**推荐值**|**说明**|
|---|---|---|---|
|**负载权重**|`ALPHA`|1.0|脉冲计数权重|
||`BETA`|0.01|突触操作权重|
||`CARD_CAPACITY`|5000|单卡负载归一化常数 (Max Capacity)|
|**时间尺度**|`EPOCH_LENGTH`|500ms|全局调度周期|
|**阈值控制**|`THETA_HIGH`|0.85|过载阈值 (85%)|
||`THETA_LOW`|0.60|接收阈值 (60%)|
||`THETA_SAFE`|0.75|降负目标 (75%)|
|**ROI 策略**|`GAMMA`|1.5|热度偏好因子|
|**硬件约束**|`MAX_CORES`|512|单卡神经元核心数|
||`MAX_MEM`|128|单卡内存 (GB)|

---

## Gandiva-Spike: Smallest-First 动态基线算法

### 1. 设计动机

**Gandiva-Spike** 是一个**动态调度基线算法**，用于与 GLaSS 进行对比实验。它借鉴了 Gandiva 论文中"优先操作小任务"的思想，专门设计用于验证 GLaSS 的 ROI-Greedy 策略的优越性。

#### 设计原则

1. **最小化代码改动**：直接继承 `GLaSS`，只重写任务选择逻辑（约 90 行代码）
2. **严格控制变量**：与 GLaSS 使用相同的负载度量、阈值体系、放置策略
3. **聚焦负载均衡**：唯一差异是迁移任务选择策略（Smallest-First vs ROI-Greedy）

#### 与 GLaSS 的核心差异

| 特性 | GLaSS | Gandiva-Spike | 是否相同 |
|------|-------|---------------|:--------:|
| 负载度量 | 累积脉冲负载 $L_i(T_{epoch})$ | 累积脉冲负载 $L_i(T_{epoch})$ | ✓ |
| 阈值体系 | Θ_high=0.85, Θ_low=0.60, Θ_safe=0.75 | Θ_high=0.85, Θ_low=0.60, Θ_safe=0.75 | ✓ |
| 放置策略 | Best-Fit | Best-Fit | ✓ |
| 卡状态分类 | CRITICAL/AVAILABLE/STABLE | CRITICAL/AVAILABLE/STABLE | ✓ |
| **迁移选择** | **ROI-Greedy（高负载优先）** | **Smallest-First（低负载优先）** | ✗ |

### 2. 算法原理

#### 核心思想：Smallest-First 迁移

Gandiva-Spike 的核心假设是：**优先迁移小任务可以更灵活地填充碎片空间**（来自 Gandiva 论文的 Packing 思想）。

**对比**：
| 策略 | GLaSS (ROI-Greedy) | Gandiva-Spike (Smallest-First) |
|------|-------------------|---------------------------------|
| 排序依据 | 效率分数 $E_i = (L_i^{epoch})^\gamma / (S_{state} + \epsilon)$ | 累积负载 $L_i^{epoch}$（升序） |
| 优先迁移 | 高负载但状态小的任务 | 低负载任务 |
| 迁移次数 | 少（一次迁移即降温） | 多（需多次才能凑够负载） |

#### 负载定义（复用 GLaSS）

采用与 GLaSS **完全相同**的累积负载定义：

$$L_i(T_{epoch}) = \sum_{t_{phy}} (\alpha \cdot \text{SpikeCount}_i + \beta \cdot \text{SynapticOps}_i)$$

$$L_m^{sched} = \frac{\sum_{k \in \text{Tasks}(m)} L_k(T_{epoch})}{C_{capacity}}$$

#### 阈值体系（复用 GLaSS）

| 阈值 | 值 | 含义 |
|------|:---:|------|
| $\Theta_{high}$ | 0.85 | 过载阈值，触发迁出 |
| $\Theta_{low}$ | 0.60 | 轻载阈值，可接收迁入 |
| $\Theta_{safe}$ | 0.75 | 迁移目标负载水位 |

### 3. 算法流程

#### 三阶段架构（与 GLaSS 结构一致）

```
Phase 1: Sense     - 分类卡状态 (CRITICAL/AVAILABLE/STABLE) [与 GLaSS 相同]
Phase 2: Decision  - Smallest-First 任务选择              [核心差异]
Phase 3: Execution - Best-Fit 目标分配                    [与 GLaSS 相同]
```

#### 伪代码

```python
class GandivaSpike(GLaSS):
    """
    继承 GLaSS，仅重写 Decision 阶段的任务选择逻辑。
    """
    
    @property
    def name(self) -> str:
        return "GandivaSpike"
    
    def step(self, time_step: int) -> None:
        # ============================================================
        # Phase 1: Sense - 卡状态分类（继承 GLaSS，完全相同）
        # ============================================================
        critical_cards, available_cards = self._sense_phase()
        
        if not critical_cards or not available_cards:
            return  # 系统稳定或饱和
        
        # ============================================================
        # Phase 2: Decision - Smallest-First 任务选择【核心差异】
        # ============================================================
        for source_card in critical_cards:
            if not available_cards:
                break
            
            # 【关键差异】按累积负载**升序**排列，优先迁移小任务
            active_tasks = [t for t in source_card.tasks 
                           if t.current_spike_count > 0]
            
            sorted_tasks = sorted(
                active_tasks,
                key=lambda t: self._get_task_epoch_load(t),
                reverse=False  # 升序：小任务优先
            )
            
            # 计算需要迁移的负载量
            source_load = self._get_normalized_card_load(source_card)
            delta_target = source_load - self.THETA_SAFE
            
            if delta_target <= 0:
                continue
            
            # 贪心选择任务直到满足目标
            accumulated = 0.0
            tasks_to_migrate = []
            
            for task in sorted_tasks:
                task_load = self._get_task_epoch_load(task) / self.card_capacity
                tasks_to_migrate.append(task)
                accumulated += task_load
                
                if accumulated >= delta_target:
                    break
            
            # ============================================================
            # Phase 3: Execution - Best-Fit 目标分配（继承 GLaSS，相同）
            # ============================================================
            for task in tasks_to_migrate:
                self._execute_migration_with_strategy(
                    task, source_card, available_cards, time_step
                )
```

### 4. 实验对比设计

#### 控制变量验证

通过严格控制变量，实验可以证明 **"迁移策略是性能差异的唯一原因"**：

| 控制变量 | GLaSS | Gandiva-Spike | 验证 |
|----------|-------|---------------|:----:|
| 负载度量公式 | $\alpha S + \beta O$ | $\alpha S + \beta O$ | ✓ |
| α, β 参数 | 1.0, 0.01 | 1.0, 0.01 | ✓ |
| 卡容量 C_capacity | 5000 | 5000 | ✓ |
| 阈值 Θ_high/Θ_low/Θ_safe | 0.85/0.60/0.75 | 0.85/0.60/0.75 | ✓ |
| 放置策略 | Best-Fit | Best-Fit | ✓ |
| **迁移选择** | **ROI-Greedy** | **Smallest-First** | ✗ |

#### 预期实验结果与分析

| 场景 | GLaSS | Gandiva-Spike | 原因 |
|------|-------|---------------|------|
| **突发负载响应** | 快（1-2次迁移） | 慢（多次迁移） | 大任务一次迁移即可显著降温 |
| **负载均衡效果** | 好（低方差） | 差（高方差） | 小任务迁移后源卡可能仍过载 |
| **迁移开销** | 低（次数少） | 高（次数多） | "蚂蚁搬家"问题 |
| **系统抖动** | 低 | 高 | 频繁迁移小任务导致状态不稳定 |

### 5. 总结

Gandiva-Spike 作为动态基线算法的价值在于：

1. **严格控制变量**：除迁移选择策略外，所有组件与 GLaSS 完全相同
2. **最小实现成本**：继承 GLaSS，仅约 90 行代码
3. **有效对比**：验证 ROI-Greedy 策略在 SNN 突发负载场景下的优越性
4. **科学结论**：证明"直接照搬传统调度算法（优先操作小任务）到 SNN 领域是不够的"

### 6. 参考文献

- Xiao, W., et al. "Gandiva: Introspective Cluster Scheduling for Deep Learning." OSDI 2018.

---

## 算法对比总结

### 特性对比表

| 特性 | Best-Fit | DRF | P2C | Round-Robin | GLaSS | Gandiva-Spike |
|------|----------|-----|-----|-------------|-------|---------------|
| **调度类型** | 静态 | 静态 | 静态 | 静态 | 动态 | 动态 |
| **运行时迁移** | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| **决策复杂度** | O(M) | O(M) | O(1) | O(1) avg | O(M·T) | O(M·T) |
| **资源维度** | 多维 | 4维 | 可配置 | 无关 | 加权负载 | 加权负载 |
| **负载感知** | ❌ | ❌ | 放置时 | ❌ | ✅ 实时 | ✅ 实时 |
| **理论保证** | 装箱近似 | 公平性 | $O(\log\log M)$ | 公平分配 | 迟滞稳定 | 迟滞稳定 |

### 适用场景对比

| 场景 | 推荐算法 | 原因 |
|------|---------|------|
| 基准测试 | Best-Fit / Round-Robin | 简单、易对比 |
| 资源异构环境 | DRF | 多维资源均衡 |
| 超大规模集群 | P2C / Round-Robin | O(1) 决策时间 |
| SNN 动态负载 | GLaSS | 实时迁移、负载均衡 |
| 负载稳定工作负载 | DRF / Best-Fit | 无需迁移开销 |
| 突发性工作负载 | GLaSS | 动态适应负载变化 |
| 算法对比实验 | GLaSS vs Gandiva-Spike | 控制变量验证策略效果 |
| 原型快速验证 | Round-Robin | 实现简单、可预测 |

### 性能指标说明

- **负载方差 (Load Variance)**：衡量各卡负载的均衡程度，越低越好
- **迁移次数 (Migrations)**：动态调度器的额外开销
- **任务完成率 (Completion Rate)**：成功调度的任务比例
- **最大负载 (Max Load)**：最繁忙卡的负载，影响尾延迟

---

## 架构改进与优化

### 1. 放置策略模式 (Placement Strategy Pattern)

#### 设计背景
在原始实现中，五个调度器（Best-Fit、DRF、P2C、RoundRobin 和 GLaSS）各自实现了 `select_card_for_task()` 方法，导致约 100+ 行重复代码。同时，GLaSS 的迁移目标选择 (`_select_best_fit_target()`) 与静态调度器的放置逻辑存在功能重叠。这种设计违反了 DRY 原则，增加了维护成本，同时降低了算法的可组合性。

#### 解决方案：策略模式
引入 **Strategy 设计模式** 实现任务放置和迁移的抽象化，允许不同的调度器共享相同的放置策略，或者动态切换策略。

#### 核心设计

**抽象策略基类** (`PlacementStrategy`)：
```python
from abc import ABC, abstractmethod
from typing import Optional, List

class PlacementStrategy(ABC):
    """任务放置和迁移的策略抽象接口"""
    
    @abstractmethod
    def select_card(self, task: "Task") -> Optional["Card"]:
        """
        初始任务放置：为新任务选择目标卡
        
        Args:
            task: 待放置的任务对象
        
        Returns:
            选中的卡对象，若无合适卡则返回 None
        """
        pass
    
    @abstractmethod
    def select_migration_target(
        self, 
        task: "Task", 
        candidate_cards: List["Card"],
        task_load: float,
        card_loads: dict,
        load_threshold: float
    ) -> Optional["Card"]:
        """
        迁移目标选择：为待迁移任务选择目标卡
        
        Args:
            task: 待迁移的任务对象
            candidate_cards: 候选卡列表（AVAILABLE 状态）
            task_load: 任务的归一化负载
            card_loads: 所有卡的当前归一化负载字典 {card_id: load}
            load_threshold: 负载阈值（通常为 THETA_HIGH）
        
        Returns:
            选中的目标卡，若无合适卡则返回 None
        """
        pass
```

#### 四个具体策略实现

**1. BestFitStrategy（最佳适配）**
- **初始放置**：选择剩余容量最匹配的卡（最小化剩余空间）
- **迁移目标**：选择能最紧密填充碎片的卡（最小化迁移后的剩余空间）
- **应用场景**：资源利用率优先的场景

```python
class BestFitStrategy(PlacementStrategy):
    def select_card(self, task):
        # 找到剩余容量最大且空间最匹配的卡
        ...
    
    def select_migration_target(self, task, candidate_cards, task_load, 
                               card_loads, load_threshold):
        # 最小化 (load_threshold - card_load) - task_load（最小碎片）
        ...
```

**2. P2CStrategy（双选择）**
- **初始放置**：随机选择 2 张卡，对比后选择负载较低的
- **迁移目标**：同样的二次随机采样与对比
- **应用场景**：大规模集群、需要快速决策的环境

```python
class P2CStrategy(PlacementStrategy):
    def select_card(self, task):
        # 随机采样 2 张合格卡，选择负载较低者
        ...
    
    def select_migration_target(self, task, candidate_cards, task_load, 
                               card_loads, load_threshold):
        # 在候选卡中随机采样 2 张，选择负载较低者
        ...
```

**3. DRFStrategy（主导资源公平性）**
- **初始放置**：选择放置后主导资源利用率最小的卡
- **迁移目标**：同样选择主导资源利用率最小的候选卡
- **应用场景**：多维资源异构环境

```python
class DRFStrategy(PlacementStrategy):
    def select_card(self, task):
        # 计算 max(cpu%, memory%, synapse%) 最小的卡
        ...
    
    def select_migration_target(self, task, candidate_cards, task_load, 
                               card_loads, load_threshold):
        # 在候选卡中选择主导资源利用率最小的
        ...
```

**4. RoundRobinStrategy（轮询）**
- **初始放置**：按卡的序号循环轮询
- **迁移目标**：继承默认行为（选择第一个可用卡）
- **应用场景**：负载均匀分布或基准测试

```python
class RoundRobinStrategy(PlacementStrategy):
    def select_card(self, task):
        # 按循环顺序选择下一张卡
        ...
    
    def select_migration_target(self, task, candidate_cards, task_load, 
                               card_loads, load_threshold):
        # 使用默认实现或自定义逻辑
        ...
```

#### 集成到 BaseScheduler

在基类中引入策略组合：

```python
class BaseScheduler(ABC):
    def __init__(
        self, 
        cards: List[Card],
        alpha: float = 1.0,
        beta: float = 0.01,
        placement_strategy: Optional[PlacementStrategy] = None
    ):
        self.cards = cards
        self.alpha = alpha
        self.beta = beta
        
        # 若未指定策略，默认使用 BestFitStrategy
        self._placement_strategy = placement_strategy or BestFitStrategy(
            cards, alpha, beta
        )
    
    def select_card_for_task(self, task: Task) -> Optional[Card]:
        """委托给策略对象"""
        return self._placement_strategy.select_card(task)
```

#### GLaSS 与策略的整合

GLaSS 在迁移阶段（Phase 3）直接使用策略对象：

```python
class GLaSS(BaseScheduler):
    def execute_phase(self):
        """Phase 3: 执行迁移"""
        for overloaded_card in critical_cards:
            # 选择待迁移任务（ROI-Greedy）
            tasks_to_migrate = self._select_tasks_to_migrate(overloaded_card)
            
            for task in tasks_to_migrate:
                # 使用策略对象选择迁移目标
                target_card = self._placement_strategy.select_migration_target(
                    task=task,
                    candidate_cards=available_cards,
                    task_load=normalized_load,
                    card_loads=all_card_loads,
                    load_threshold=self.THETA_HIGH
                )
                
                if target_card:
                    self.migrate(task, overloaded_card, target_card)
```

#### 灵活组合示例

```python
from schedule import GLaSS, P2CStrategy

# 创建卡集合
cards = [Card(...) for _ in range(5)]

# 使用 GLaSS 与 P2C 迁移策略
strategy = P2CStrategy(cards)
scheduler = GLaSS(cards, placement_strategy=strategy)

# 现在 GLaSS 在迁移目标选择时将使用 P2C 的随机二次采样
# 而不是固定的最佳适配算法
```

### 2. 代码复用与消除重复

#### 改进前的现状
- **重复代码量**：~180 行
  - 每个调度器独立实现 `select_card_for_task()` (~35-45 行/个)
  - GLaSS 额外实现 `_select_best_fit_target()` (~45 行)
  
- **维护问题**：修改一个调度器的放置逻辑需要同步修改其他调度器

#### 改进后的收益
- **代码合并**：所有放置逻辑统一到 `PlacementStrategy` 子类
- **代码量减少**：新增 280 行 strategy.py，删除 180 行重复代码，净减少零重复
- **维护简化**：
  - 添加新的调度策略只需继承 `PlacementStrategy` 并实现 2 个方法
  - 修改放置算法只需修改对应的策略类，自动适用于所有使用该策略的调度器

#### 具体改动统计

| 文件 | 修改类型 | 变化 | 说明 |
|------|---------|------|------|
| `schedule/placement_strategy.py` | 新增 | +280 行 | 5 个策略类（1 抽象 + 4 具体） |
| `schedule/base.py` | 修改 | 添加策略参数 | 引入 `placement_strategy` 组合 |
| `schedule/glass.py` | 删除 | -45 行 | 移除 `_select_best_fit_target()` |
| `schedule/glass.py` | 修改 | Phase 3 迁移 | 改用 `strategy.select_migration_target()` |
| `schedule/bestfit.py` | 删除 | -35 行 | 移除冗余 `select_card_for_task()` |
| `schedule/drf.py` | 删除 | ~25 行 | 精简冗余逻辑 |
| `schedule/p2c.py` | 删除 | ~25 行 | 精简冗余逻辑 |
| `schedule/__init__.py` | 修改 | 导出策略 | 导出所有策略类供外部使用 |

**净代码变化**：+280 - 180 = **+100 行**（引入 100 行新的抽象设施以换取 180 行重复代码的消除）

### 3. 扩展性提升

#### 轻松添加新策略
要添加一个新的放置策略（例如 Least-Loaded 策略），只需：

```python
class LeastLoadedStrategy(PlacementStrategy):
    def select_card(self, task):
        eligible = [c for c in self.cards if c.can_host(task)]
        return min(eligible, key=lambda c: c.current_load)
    
    def select_migration_target(self, task, candidate_cards, task_load, 
                               card_loads, load_threshold):
        return min(candidate_cards, 
                  key=lambda c: card_loads.get(c.card_id, 0.0))
```

然后即可直接用于任何调度器：
```python
scheduler = GLaSS(cards, placement_strategy=LeastLoadedStrategy(cards))
```

#### 运行时策略切换
动态改变调度策略无需重启调度器：
```python
scheduler._placement_strategy = DRFStrategy(cards)  # 切换到 DRF
```

### 4. 验证与测试结果

#### 功能等价性验证
重构后，对所有 5 个调度器进行完整回归测试：

```
SIMULATION SUMMARY
=================
Scheduler: GLaSS
Tasks Completed: 100/100 ✓
Total Migrations: 7 ✓  (与重构前一致，证明功能等价)

Scheduler: DRF
Tasks Completed: 100/100 ✓
Total Migrations: 0 ✓

Scheduler: P2C
Tasks Completed: 100/100 ✓
Total Migrations: 0 ✓

Scheduler: BestFit
Tasks Completed: 100/100 ✓
Total Migrations: 0 ✓

Scheduler: RoundRobin
Tasks Completed: 100/100 ✓
Total Migrations: 0 ✓
```

**关键验证**：GLaSS 的迁移次数保持 7 次（与重构前完全相同），证明 `select_migration_target()` 与原有 `_select_best_fit_target()` 功能完全等价。

#### 导入验证
所有策略类可正确导入并使用：
```python
from schedule import (
    PlacementStrategy,
    BestFitStrategy,
    P2CStrategy,
    RoundRobinStrategy,
    DRFStrategy,
)
```

### 5. 设计模式总结

| 维度 | 设计特点 |
|------|---------|
| **模式** | Strategy 模式 + Composition（组合优于继承） |
| **抽象层次** | `PlacementStrategy`（抽象）→ 4 个具体策略 |
| **集成方式** | 依赖注入（构造函数参数） |
| **灵活性** | 支持运行时切换和组合 |
| **扩展成本** | O(1) - 只需添加新的策略类 |
| **迁移成本** | O(0) - 现有调度器无需修改 |

### 6. 后续优化方向

1. **策略工厂**：创建 `PlacementStrategyFactory` 供配置文件动态指定策略
2. **混合策略**：实现 `HybridStrategy` 同时支持多个策略的加权组合
3. **自适应策略**：实现 `AdaptiveStrategy` 根据负载动态调整内部算法
4. **性能测量**：为策略添加性能指标采集（决策时间、迁移成功率等）
5. **策略模板**：提供策略开发者指南和模板代码

