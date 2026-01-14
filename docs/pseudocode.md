# GLaSS 算法伪代码

**文档版本**：v2.0（2026-01-09）  
**对应实现**：`schedule/glass.py`, `simulation/engine.py`  
**主要更新**：替换 P95 分位数为累积负载；更新三阶段流程

## 总体架构

GLaSS 采用**双层时间尺度**设计：
- **物理执行层** ($\Delta T_{phy} \approx 1\text{ms}$)：硬件执行 SNN 计算
- **全局调度层** ($T_{epoch} \approx 500\text{ms}$)：GLaSS 执行负载均衡

### 核心概念：累积负载（Accumulated Load）

与早期版本的 P95 分位数不同，**当前实现采用直接累积**：

- $L_i(t_{phy})$ = 瞬时负载（物理时刻 $t_{phy}$ 的任务 $i$ 负载）：
  $$L_i(t_{phy}) = \alpha \cdot \text{SpikeCount}(t_{phy}) + \beta \cdot \text{SynapticOps}(t_{phy})$$

- $L_i(T_{epoch})$ = **累积负载**（当前 Epoch 内的总负载）：
  $$L_i(T_{epoch}) = \sum_{t=1}^{T_{epoch}/\Delta t_{phy}} L_i(t_{phy})$$

- $L_m^{raw}(T_{epoch})$ = 卡 $m$ 的累积负载：
  $$L_m^{raw}(T_{epoch}) = \sum_{i \in \text{Tasks}(m)} L_i(T_{epoch})$$

- $L_m^{sched}$ = 规范化卡负载（用于调度决策）：
  $$L_m^{sched} = \frac{L_m^{raw}(T_{epoch})}{C_{capacity}}$$

**优点**：
- 直接精确反映 Epoch 内总工作量
- 避免采样偏差和分位数计算开销
- 与物理执行层紧密对应

---

## 主算法流程与常量

```python
# 全局配置常量
THETA_HIGH = 0.85      # 过载阈值：触发迁出
THETA_LOW = 0.60       # 轻载阈值：可接收迁入
THETA_SAFE = 0.75      # 安全目标：迁出目标
GAMMA = 1.5            # ROI 热度因子（高值偏好重任务）
ALPHA = 1.0            # 脉冲权重
BETA = 0.01            # 突触操作权重
C_CAPACITY = 5000      # 卡容量常数（用于规范化）

class GLaSS(BaseScheduler):
    def __init__(cards, alpha, beta, card_capacity, gamma, placement_strategy):
        self.cards = cards
        self.alpha = alpha
        self.beta = beta
        self.card_capacity = card_capacity
        self.gamma = gamma
        self._placement_strategy = placement_strategy or BestFitStrategy(cards)
        
        # 追踪累积负载（每个 Epoch 重置）
        self._task_epoch_load = {}       # task_id -> L_i(T_epoch)
        self._card_epoch_load = {}       # card_id -> L_raw(m, T_epoch)
```

---

## 算法三阶段流程

### Phase 1: 感知与状态判定 (Sense Phase)

```python
def step(epoch_num):
    """
    执行一个 Epoch 的负载均衡调度。
    
    三个阶段：
    1. Sense：基于累积负载分类卡状态
    2. Decision：ROI-Greedy 任务选择
    3. Execution：执行迁移
    """
    
    # =================================================================
    # Phase 1: Sense - 基于累积负载分类卡状态
    # =================================================================
    card_normalized_loads = {}   # card_id -> L_m^sched
    card_states = {}              # card_id -> CardState
    critical_cards = []
    available_cards = []
    
    for card in self.cards:
        # 获取规范化负载：L_m^sched = L_raw(m, T_epoch) / C_capacity
        raw_load = self._card_epoch_load.get(card.card_id, 0.0)
        normalized_load = raw_load / self.card_capacity
        
        # 分类卡状态（固定阈值 + 迟滞）
        if normalized_load >= self.THETA_HIGH:
            state = CardState.CRITICAL
        elif normalized_load <= self.THETA_LOW:
            state = CardState.AVAILABLE
        else:
            state = CardState.STABLE
        
        card_states[card.card_id] = state
        card_normalized_loads[card.card_id] = normalized_load
        
        log(f"Card {card.card_id}: RawLoad={raw_load:.1f}, "
            f"NormalizedLoad={normalized_load:.3f}, State={state.value}")
        
        if state == CardState.CRITICAL:
            critical_cards.append(card)
        elif state == CardState.AVAILABLE:
            available_cards.append(card)
    
    # Early exit 检查
    if not critical_cards:
        log(">> System is stable. No migration needed.")
        return
    
    if not available_cards:
        log(">> System is saturated. No AVAILABLE cards.")
        return
```

### Phase 2: 决策阶段 - ROI-Greedy (Decision Phase)

```python
    # =================================================================
    # Phase 2: Decision - ROI-Greedy 任务选择
    # =================================================================
    
    for source_card in critical_cards:
        if not available_cards:
            break  # 无可用卡，退出
        
        source_normalized_load = card_normalized_loads[source_card.card_id]
        log(f">> Analyzing CRITICAL Card {source_card.card_id}...")
        
        # Skip 单任务情况（除非该任务已完成）
        if len(source_card.tasks) == 1:
            task = source_card.tasks[0]
            if task.current_spike_count > 0:  # 仍然活跃
                log("   Single active task present; skipping migration.")
                continue
        
        # --------- 步骤 1: 收集可迁移任务并计算 ROI ---------
        task_rois = []
        
        for task in source_card.tasks:
            # 跳过已完成的任务（活跃度 = 0）
            if task.current_spike_count == 0:
                log(f"   Task {task.task_id} is inactive; skip from migration.")
                continue
            
            # 计算任务 ROI（性价比）
            epoch_load = self._task_epoch_load.get(task.task_id, 0.0)
            state_size = task.state_size_mb
            
            # E_i = (L_i^epoch)^γ / (S_state + ε)
            # 高 γ（默认 1.5）偏好高负载任务，避免"蚂蚁搬家"问题
            efficiency = (epoch_load ** self.gamma) / (state_size + 0.001)
            
            task_rois.append({
                'task': task,
                'epoch_load': epoch_load,
                'state_size': state_size,
                'efficiency': efficiency,
                'normalized_load': epoch_load / self.card_capacity
            })
        
        if not task_rois:
            log("   All tasks on this card are inactive; skip migration.")
            continue
        
        # --------- 步骤 2: 按性价比排序（贪心） ---------
        task_rois.sort(key=lambda x: x['efficiency'], reverse=True)
        
        # --------- 步骤 3: 确定减负目标 ---------
        delta_target = source_normalized_load - self.THETA_SAFE
        
        if delta_target <= 0:
            log("   Load already below THETA_SAFE; no migration needed.")
            continue
        
        log(f"   Migration target: reduce by {delta_target:.3f} "
            f"({source_normalized_load:.2f} -> {self.THETA_SAFE:.2f})")
        
        # --------- 步骤 4: 贪心累积选择 ---------
        accumulated_load = 0.0
        tasks_to_migrate = []
        
        for roi in task_rois:
            log(f"   Candidate Task {roi['task'].task_id}: "
                f"Efficiency={roi['efficiency']:.2f}, "
                f"EpochLoad={roi['epoch_load']:.1f}, "
                f"StateSize={roi['state_size']:.1f}MB")
            
            tasks_to_migrate.append(roi)
            accumulated_load += roi['normalized_load']
            
            if accumulated_load >= delta_target:
                break
```

### Phase 3: 执行阶段 (Execution Phase)

```python
        # =================================================================
        # Phase 3: Execution - 执行迁移与约束检查
        # =================================================================
        
        for roi in tasks_to_migrate:
            task = roi['task']
            normalized_task_load = roi['normalized_load']
            
            # 使用放置策略选择目标卡
            target_card = self._placement_strategy.select_migration_target(
                task=task,
                candidate_cards=available_cards,
                task_load=normalized_task_load,
                card_loads=card_normalized_loads,
                load_threshold=self.THETA_HIGH
            )
            
            if target_card is None:
                log(f"   [SKIP] No suitable target for Task {task.task_id}")
                continue
            
            # 执行迁移
            success = self._execute_migration(task, source_card, target_card, epoch_num)
            
            if success:
                # 更新累积负载追踪
                task_epoch_load_value = self._task_epoch_load.get(task.task_id, 0.0)
                self._card_epoch_load[source_card.card_id] -= task_epoch_load_value
                self._card_epoch_load[target_card.card_id] += task_epoch_load_value
                
                # 更新规范化负载估计
                card_normalized_loads[source_card.card_id] -= normalized_task_load
                card_normalized_loads[target_card.card_id] += normalized_task_load
                
                log(f"   [MIGRATE] Task {task.task_id}: "
                    f"Card {source_card.card_id} -> Card {target_card.card_id}")
                
                # 重新分类目标卡
                new_target_state = self._classify_card(
                    card_normalized_loads[target_card.card_id]
                )
                
                # 如果目标卡不再 AVAILABLE，从池中移除
                if new_target_state != CardState.AVAILABLE:
                    available_cards.remove(target_card)
                    log(f"   Target Card {target_card.card_id} no longer AVAILABLE")
                
                if not available_cards:
                    break  # 无更多可用卡

    # Phase 3 后：不在此重置累积负载！
    # 引擎会在记录指标后调用 reset_epoch_loads()
```

---

## 辅助函数

### 物理层累积负载

```python
def record_physical_tick(scheduler_step):
    """
    在物理层 tick 中累积负载。
    
    在双层时间架构中，此函数在每个物理 tick 被调用（频率 >> 调度器 tick）。
    用于累积 L_i(T_epoch) 和 L_m^raw(T_epoch)。
    
    实现位置：schedule/glass.py::record_physical_tick()
    """
    for card in self.cards:
        card_tick_load = 0.0
        
        for task in card.tasks:
            # 瞬时负载：L_i(t_phy) = α·SpikeCount + β·SynapticOps
            task_tick_load = (
                self.alpha * task.current_spike_count +
                self.beta * task.current_synaptic_ops
            )
            
            # 累积到任务 epoch 负载
            if task.task_id not in self._task_epoch_load:
                self._task_epoch_load[task.task_id] = 0.0
            self._task_epoch_load[task.task_id] += task_tick_load
            
            card_tick_load += task_tick_load
        
        # 累积到卡 epoch 负载
        if card.card_id not in self._card_epoch_load:
            self._card_epoch_load[card.card_id] = 0.0
        self._card_epoch_load[card.card_id] += card_tick_load
```

### 执行迁移

```python
def _execute_migration(task, source_card, target_card, epoch_num):
    """
    执行任务迁移。
    
    返回：
        成功迁移则 True，否则 False
    """
    if not target_card.can_host(task):
        log(f"Target card lacks resources for Task {task.task_id}")
        return False
    
    # 从源卡移除任务
    source_card.evict(task)
    
    # 向目标卡添加任务
    if not target_card.put(task):
        # 回滚
        source_card.put(task)
        log(f"Failed to place Task {task.task_id} on target card")
        return False
    
    # 更新任务的归属卡
    task.host_card_id = target_card.card_id
    
    # 记录迁移事件
    log_migration_event({
        'epoch': epoch_num,
        'task_id': task.task_id,
        'src_card': source_card.card_id,
        'dst_card': target_card.card_id,
        'task_epoch_load': self._task_epoch_load.get(task.task_id, 0.0),
        'state_size_mb': task.state_size_mb
    })
    
    return True
```

### 卡状态分类

```python
def _classify_card(normalized_load):
    """
    基于规范化负载分类卡状态。
    
    使用固定阈值 + 迟滞控制（Hysteresis），避免动态阈值在
    高负载下失效（"比烂"问题）。
    """
    if normalized_load >= self.THETA_HIGH:
        return CardState.CRITICAL
    elif normalized_load <= self.THETA_LOW:
        return CardState.AVAILABLE
    else:
        return CardState.STABLE
```

### 重置 epoch 负载

```python
def _reset_epoch_loads():
    """
    重置 epoch 累积负载，为下一个 epoch 做准备。
    
    由仿真引擎在记录指标后调用：
    - 记录指标快照（使用当前累积值）
    - 调用 scheduler.reset_epoch_loads()
    - 清零所有累积计数器
    """
    self._task_epoch_load.clear()
    for card_id in self._card_epoch_load:
        self._card_epoch_load[card_id] = 0.0
```

---

## 放置策略接口 (Placement Strategy)

GLaSS 通过可插拔的放置策略实现灵活的目标卡选择。定义在 [schedule/placement_strategy.py](../schedule/placement_strategy.py)。

```python
class PlacementStrategy(ABC):
    """抽象放置策略基类。"""
    
    def __init__(cards, alpha, beta):
        self.cards = cards
        self.alpha = alpha
        self.beta = beta
    
    @abstractmethod
    def select_card(task, available_cards):
        """
        初始放置：选择放置任务的卡。
        
        用于任务初始到达时的放置决策。
        """
        raise NotImplementedError
    
    @abstractmethod
    def select_migration_target(
        task,
        candidate_cards,
        task_load,
        card_loads,
        load_threshold
    ):
        """
        迁移目标选择。
        
        参数：
            task: 要迁移的任务
            candidate_cards: 候选卡（如 AVAILABLE 卡）
            task_load: 任务的规范化负载
            card_loads: Dict[card_id] -> 当前规范化负载
            load_threshold: 最大负载阈值（如 THETA_HIGH）
        
        返回：
            选中的目标卡，或 None
        """
        raise NotImplementedError


# 内置策略实现（均在 schedule/placement_strategy.py）

class BestFitStrategy(PlacementStrategy):
    """
    最优适配（Best-Fit）：选择剩余空间最匹配的卡。
    
    目标最小化浪费空间：
    k* = argmin_k | (THETA_HIGH - L_k) - L_task |
    """
    def select_migration_target(...):
        eligible = [c for c in candidates if c.can_host(task)]
        return min(eligible, key=lambda c: waste(c, task_load))


class P2CStrategy(PlacementStrategy):
    """
    二次随机选择（Power of Two Choices）。
    
    随机采样 2 张候选卡，选择负载较低者。
    支持多种负载度量：weighted, drf, tasks。
    """
    def select_migration_target(...):
        if len(candidates) <= 2:
            return min(candidates, key=lambda c: load(c))
        
        sampled = random.sample(candidates, 2)
        return min(sampled, key=lambda c: load(c))


class DRFStrategy(PlacementStrategy):
    """
    主导资源公平（Dominant Resource Fairness）。
    
    最小化目标卡的主导资源利用率：
    dominant = max(core_util, memory_util, synapse_util)
    """
    def select_migration_target(...):
        eligible = [c for c in candidates if c.can_host(task)]
        return min(eligible, key=lambda c: dominant_resource(c))


class RoundRobinStrategy(PlacementStrategy):
    """
    轮询（Round-Robin）。
    
    按顺序轮流分配，简单高效。
    """
    def select_migration_target(...):
        return candidates[self.next_index % len(candidates)]
```

---

## 算法复杂度分析

**时间复杂度**（每个 Epoch）：

| 阶段 | 操作 | 复杂度 | 说明 |
|------|------|--------|------|
| **Sense** | 遍历卡 + 分类 | $O(M)$ | 逐卡分类，无排序 |
| **Decision** | 任务 ROI 计算 + 排序 | $O(N \log N)$ | 单卡任务数 N，进行排序 |
| **Execution** | 迁移执行 | $O(K)$ | K = 迁移任务数 |
| **总计** | | $O(M + N \log N + K)$ | 线性卡数，对数任务数 |

其中：
- $M$ = 卡数量（系统规模）
- $N$ = 单卡最大任务数（工作负载密度）
- $K$ = 实际迁移任务数 ($K \leq N \cdot M$)

**空间复杂度**：
- 累积负载追踪：$O(M + N \cdot M)$（任务级 + 卡级）
- 临时数据结构：$O(M + N)$（决策过程）

---

## 关键设计特点

### 1. 累积负载 vs P95 分位数（v1.0 vs v2.0）

**实现采用累积负载**（v2.0），而非伪代码早期版本的 P95 分位数（v1.0）：

| 特性 | P95 分位数 | 累积负载 |
|------|-----------|---------|
| **计算方式** | 采样 + 排序 | 逐 tick 累加 |
| **精度** | 滤波微观抖动 | 精确总量 |
| **开销** | 每 epoch $O(T \log T)$ | 每 tick $O(1)$ |
| **实现复杂度** | 中等 | 简单 |
| **对突发敏感** | 高（P95 捕捉） | 直接反映 |

**选择累积负载的原因**：
- 直接精确反映 Epoch 内总工作量
- 避免采样偏差
- 与物理执行层紧密对应（逐 tick 累积）
- 实现更简洁，便于调试

### 2. 固定阈值 + 迟滞控制

使用三级分类避免"比烂"问题（高负载环境下动态阈值失效）：

```
                STABLE zone
        CRITICAL <---------> AVAILABLE
    (L >= 0.85)    (0.60 <= L <= 0.85)    (L <= 0.60)
         |                                      |
    trigger out                          trigger in
    target: -> 0.75                    highest priority
```

**迟滞的好处**：
- 避免频繁状态跳转（Ping-Pong 问题）
- 在高负载下保持稳定性
- 清晰的语义（三态而非二态）

### 3. ROI-Greedy 决策

贪心算法兼顾负载与成本：

$$E_i = \frac{(L_i^{epoch})^{\gamma}}{S_{state} + \epsilon}$$

- **分子** $(L_i^{epoch})^{\gamma}$：高负载任务优先
  - $\gamma = 1.5$：幂次放大，避免"蚂蚁搬家"（无数轻任务）
  - 高 $\gamma$ 偏好少量重任务迁移

- **分母** $S_{state}$：轻量级任务优先
  - 状态大小代表迁移成本
  - 优先迁移状态轻的任务，降低通信开销

### 4. 可插拔放置策略

四种内置策略，灵活适应不同场景：

| 策略 | 初始放置 | 迁移目标 | 复杂度 | 适用场景 |
|------|---------|---------|--------|----------|
| **Best-Fit** | 最小碎片 | 最优匹配 | O(M) | 通用/保守 |
| **P2C** | 二选一 | 二选一 | O(1) | 大规模集群 |
| **DRF** | 多维公平 | 主导资源最低 | O(M) | 异构资源 |
| **RoundRobin** | 轮询 | 轮询 | O(1) | 简单均衡 |

### 5. 双层时间架构

解耦物理执行与调度决策，降低调度开销：

```
物理层 (1ms ticks)        调度层 (500ms epoch)
├─ Tick 1  ─┐
├─ Tick 2  ─┤
├─ ...     ─├─ [Sense + Decision + Execution]
│          ─┤
└─ Tick N  ─┘
     ↓
record_physical_tick()  →  累积负载 L_i(T_epoch)
     ↓
step(epoch)  →  调度决策
     ↓
reset_epoch_loads()  →  清零计数器
```

**优势**：
- 物理层高频运行，调度层低频决策
- 累积负载更精确（全 epoch 数据）
- 降低单次决策开销

---

## 数据结构

```python
@dataclass
class TaskROI:
    """任务 ROI 评分（决策过程中使用）。"""
    task: Task
    epoch_load: float           # L_i(T_epoch)
    state_size_mb: float        # 迁移数据量
    efficiency_score: float     # E_i = (L_i^epoch)^γ / (S_state + ε)


class CardState(Enum):
    """卡状态分类（基于规范化负载）。"""
    CRITICAL = "CRITICAL"      # L >= Θ_high（≥ 0.85）
    AVAILABLE = "AVAILABLE"    # L <= Θ_low（≤ 0.60）
    STABLE = "STABLE"          # 其他情况
```

---

## 实现参考

- **主类**：[schedule/glass.py](../schedule/glass.py) - GLaSS 调度器实现
- **放置策略**：[schedule/placement_strategy.py](../schedule/placement_strategy.py) - 四种策略
- **仿真引擎**：[simulation/engine.py](../simulation/engine.py) - 双层时间架构集成
- **工具模块**：[util/card.py](../util/card.py), [util/task.py](../util/task.py) - 卡和任务模型
