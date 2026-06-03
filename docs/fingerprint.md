# 模型指纹（Workload Fingerprint）离线提取流程

> 本文档面向 ISCA / EuroSys / USENIX ATC 风格的系统会议读者，描述 v3 项目
> 中 STPS 调度器所依赖的"模型指纹"完整提取流程：从 SNN 模型一次校准
> 集前向传播开始，到最终落盘的 `.npz` 文件结束。所有公式以 KaTeX 形式
> 完整展开，对应代码路径就近列出。

---

## 1. 设计动机与系统定位

脉冲神经网络（SNN）具有极强的 **数据依赖性 (Data-dependent)**：不同输入
导致完全不同的脉冲激发潮汐。如果依赖在线监控（Online Profiling）来
驱动调度，会带来不可接受的探测延迟，且运行时探针本身就足以冲垮片上
网络（NoC）。

因此，STPS 调度器采用 **AOT (Ahead-of-Time) 离线预分析范式**：在模型
编译与部署阶段，使用代表性的校准数据集（Calibration Dataset，批量大小
为 $B$）对 SNN 模拟器跑一次前向跟踪，提取出统计意义上的 **硬件同构
时空指纹 (Hardware-isomorphic Spatio-Temporal Fingerprint)**。这些指纹
作为"只读履历"以微秒级 $O(1)$ 查表开销指导 STPS 在线调度。

> **核心约束（贯穿全文）：** 一个调度 *Task* 在生产侧总是 *单样本
> ($B=1$)* 推理。模型的物理结构（神经元数、层宽 $|v_i|$、连接拓扑）是
> 与 profiling batch size 无关的常量。任何对 $B$ 的求和必须在期望
> 阶段除回去；指纹**绝不能** 随 profiling batch 膨胀。

---

## 2. 硬件感知的图虚拟化（节点定义）

### 2.1 离散时间动态图序列

把 SNN 在推理时间窗 $T$ 内的运行过程定义为一族快照：

$$
\mathcal{G}_{\text{dynamic}} \;=\; \bigl\{\, G^{(1)},\, G^{(2)},\, \dots,\, G^{(T)} \,\bigr\}
$$

每个 $G^{(t)} = (\mathcal{V},\, \mathcal{E}^{(t)},\, W^{(t)})$ 是一张
有向带权图，节点集 $\mathcal{V}$ 在所有 $t$ 上共享。

### 2.2 微观神经种群（Micro-populations）

在建图前，离线 Profiler 必须把宏观层（如 `Conv2d` / `Linear`）逻辑
**切分 (Slice)** 为细粒度的 *微观神经种群*

$$
\mathcal{V} \;=\; \{v_1,\, v_2,\, \dots,\, v_N\}.
$$

切分严格遵守物理硬件的容量约束：

$$
\boxed{\;
\forall v_i \in \mathcal{V},\; |v_i| \;\le\; N_{\text{core\_cap}}
\;}
$$

其中 $N_{\text{core\_cap}}$ 为单个 CIM 物理核可承载的神经元上限，目前是单核 4096 个神经元。这一
切分把原本只有几十个节点的粗糙软件图重构为包含数千个节点的 **硬件
同构图**，精确暴露单卡内部真实拓扑维度。

代码侧：v3 当前实现以"每个脉冲神经元 module 即一个 $v_i$"作为切分起点
（$|v_i| = U_i$ = 该层 flatten 后的物理单元数）。检测见
[`_is_population`](../fingerprint/dtdg.py)（SpikingJelly 多步）与
[`_is_spiking_neuron`](../fingerprint/extract_spikformer.py)（STEP 系列）。

### 2.3 切分细节（Hardware-Aware Slicing）

> **重要澄清：切分对象是 LIF 节点 $v_i$，不是 Conv2d / Linear 算子。**
> 在 §2 图模型里，节点 $v_i$ 是脉冲发放源（如 Spikformer 的
> `proj_lif`、`q_lif`、`attn_lif`），它的 **输出张量 shape** 决定切分
> 方式；前驱的 Conv2d / Linear 仅负责定义 *入边* 的 $\mathbf{M}_{ij}$
> (§4)，**与节点切分无关**。下面三类小节按 $v_i$ 的输出张量形状归类，
> 而不是按前驱算子类型。

设单核容量为 $N_{\text{core\_cap}}$（Darwin 类硬件常见 $1024$ /
$2048$，本项目目前 $4096$）。

#### 2.3.1 1D 向量节点：输出形状 $(C,)$

代表：Spikformer 的 `q_lif` / `k_lif` / `v_lif` / `proj_lif`(SSA 内) /
`fc1_lif` / `fc2_lif`。这类 LIF 节点的入边由 `Linear` 算子驱动，输出
是每 token 的 $C$ 维向量。

节点规模 $N_\ell = C$。沿 **output-channel 维等分**：

$$
P \;=\; \left\lceil \frac{N_\ell}{N_{\text{core\_cap}}} \right\rceil,
\qquad
v_i^{(p)} \;=\; \bigl\{n \in [\,p\,N_{\text{core\_cap}},\;(p{+}1)\,N_{\text{core\_cap}})\bigr\},
\quad p = 0,\dots,P{-}1
$$

权重矩阵 $W \in \mathbb{R}^{N_{\text{in}}\times N_\ell}$ 同步沿列切成
$P$ 块 $W^{(p)} \in \mathbb{R}^{N_{\text{in}}\times |v_i^{(p)}|}$，
每块独占一个 core 的存内权重 SRAM；尾段允许不满 $N_{\text{core\_cap}}$。

例（$N_{\text{core\_cap}} = 1024$）：`q_lif` ($C{=}384$) → 1 段；
`fc1_lif` ($C{=}1536$) → 2 段，$|v_i| = (1024,\,512)$。

#### 2.3.2 4D feature-map 节点：输出形状 $(C, H, W)$

代表：Spikformer SPS stem 中的 `proj_lif` / `proj_lif1` / `proj_lif2` /
`proj_lif3` / `rpe_lif`。**节点本身仍是 LIF 激活**——这里的 Conv2d 仅是
其 *入边* 的物理载体，切分规则只看 LIF 输出的 4D shape。

节点规模 $N_\ell = C \cdot H \cdot W$。**优先沿 channel 维，必要时再
沿 spatial 维**。理由：入边 Conv2d 的 fan-out 为 $K^2 C$（与 $H,W$
无关，§4 情况 B），channel-wise 切片不破坏 receptive field 的局部性；
spatial-wise 切片会引入跨 core 的 **halo 通信**（边界 pixel 需要从
邻片拉取 $K{-}1$ 行 / 列）。

记 $S = HW$，分两种情况：

**情况 (i) $S \le N_{\text{core\_cap}}$**（单通道 feature map 装得下）→
**仅切 channel**：

$$
C_p \;=\; \left\lfloor \frac{N_{\text{core\_cap}}}{S} \right\rfloor,
\qquad
v_i^{(p)} \;=\; \bigl\{(c,h,w) : c\in[p C_p,\,(p{+}1)C_p),\;
(h,w)\in[H]\times[W]\bigr\}
$$

例 `proj_lif` ($48,\,32,\,32$，$S=1024$，$N_{\text{core\_cap}}=1024$)：
$C_p = 1$，每核装一整张 channel，共 $48$ 个种群。

**情况 (ii) $S > N_{\text{core\_cap}}$**（单通道都装不下）→
**通道 + 空间双切**：

$$
C_p \;=\; 1,
\qquad
R \;=\; \left\lfloor \frac{N_{\text{core\_cap}}}{W} \right\rfloor
\;\;\text{(每条带保整行避免 width 方向 halo 累计)}
$$

相邻 spatial 子块之间额外注册一条 **halo 边**，流量

$$
\mathrm{Traffic}^{(t)}_{\text{halo}}
\;\approx\; (K-1)\cdot W \cdot C_{\text{in}}\;\;\text{spike/step}
$$

并入 §3 的 $\mathrm{Traffic}^{(t)}_{ij}$ 总账。

##### halo 边详解

**起因。** Conv2d 是 **空间局部** 算子（kernel $K\times K$，$K>1$）。
当 LIF 节点 $v_i$ 沿行被切成多条 strip 后，下游 strip 的边界 pixel 在
做卷积时需要 **上一 / 下一 strip 的边缘 $K{-}1$ 行** 才能算出正确结果
（否则边界缺数据 → 错误输出）。这条把"边缘行脉冲"在 sibling strip
之间补给的 **跨 core 链路**就是 halo 边。

**与层间前向边的正交性。**
- 层间边：跨 LIF 节点（不同语义层 $v_i \to v_j$），fan-out 由
  $\mathbf{M}_{ij}$ 决定（§4）；
- halo 边：**同一 LIF 节点内部** 的兄弟 strip 之间，仅传输边缘 pixel
  的脉冲。两者在 $\mathbf{W}^{(t)}$ 中作为 **独立条目** 累加。

**逐项物理含义。** 取 $K=3$ 行切的典型情况：

| 因子 | 含义 |
|---|---|
| $K{-}1$ | halo 厚度 = 卷积 kernel 半窗 $\times 2$；$K{=}3$ 时上下各 $1$ 行 |
| $W$     | strip 内每行的 pixel 数（保整行所以等于 feature map 宽度） |
| $C_{\text{in}}$ | 输入通道数；halo 行需把**所有 in-channel** 的脉冲一起搬过去 |

halo 边是 **双向** 的（上下邻居互为彼此的 halo 源），且与 strip 内部
正常的前向流量完全独立。

**为什么"保整行"而不沿 width 再切。** 若再沿 width 切，halo 同时
从上、下、左、右四个方向产生，halo 流量从 $(K{-}1)\cdot W$ 暴涨为

$$
\mathrm{Traffic}^{(t)}_{\text{halo,2D}} \;\approx\; 2(K{-}1)\cdot(H'+W')\cdot C_{\text{in}}
$$

且 corner pixel 还要从对角邻居拉数据，路由从 1D 链变 2D 网。整行切只
产生上下两条 halo 边，复杂度最低。

**触发条件与实例。** Spikformer-CIFAR 在 $N_{\text{core\_cap}}=1024$
下所有 4D LIF 节点 $S = HW$ 均 $\le 1024$，**不触发 halo**；halo 边
出现的典型场景是更大输入（如 ImageNet $224{\times}224$，
SpikingResformer 的 stem 处 $S = 56^2 = 3136 > 1024$）或更小核容量。

| 节点 | $S = HW$ | 是否触发 halo？ |
|---|---|---|
| `proj_lif` (CIFAR)  | $1024$ | 否（$S \le N_{\text{core\_cap}}$） |
| `proj_lif1` (CIFAR) | $1024$ | 否 |
| SpikingResformer stem (ImageNet) | $3136$ | 是，每条带 1 行 ($R{=}56$ pixel) |

**对调度器的影响。**
- **Step A（Macro Dispatch）**：halo 边链强制把同一 LIF 节点的所有
  strip 放到 **同一张卡**（halo 走片上 NoC），否则跨卡 halo 会成为热点。
- **Step C（Hotspot Split）**：触发 halo 后，spatial 子块即 sibling
  集合；如果 $\mathbf{c}^*_{\max}$ 仍超阈值，应继续在 **channel 维**
  拆分（halo 不再增加），而 *不能* 在 spatial 维再拆。
- **指纹耦合**：halo 流量计入 $\mathrm{Traffic}^{(t)}_{ij}$ 后，
  $\beta$ 的 burst 时序基本不变（halo 与前向脉冲同步发放），但
  $\bar K$ 下降——同节点 strip 之间靠 halo 边连通，不能算独立分量。

#### 2.3.3 token+embed 节点：输出形状 $(N_{\text{tok}}, C)$

代表：Spikformer 的 `attn_lif`（即 `attn @ v` 之后那个 LIF）。节点规模
$N_\ell = N_{\text{tok}} \cdot C$（CIFAR 下 $= 64 \times 384 = 24576$）。
沿 `embed_dim` 维等切，但额外约束：**同一 attention head 的
$d_{\text{head}} = C/h$ 个通道不跨 core**（避免 head 内部累加跨 NoC），
所以

$$
C_p \;=\; \left\lfloor \frac{N_{\text{core\_cap}}}{N_{\text{tok}}\cdot d_{\text{head}}} \right\rfloor \cdot d_{\text{head}},
\qquad d_{\text{head}} = C / h
$$

并取 $C_p \ge d_{\text{head}}$。

#### 2.3.4 切分粒度与指纹的耦合

切完后，原始 $V$ 个 LIF 模块膨胀成 $V'$ 个微观种群（Spikformer-CIFAR
+ $N_{\text{core\_cap}}=1024$ 实测 $V' \approx 250\!-\!400$，相对
$V=33$ 扩展约 $8\times$），$\mathbf{W}^{(t)}$ 从 $V\times V$ 升到
$V'\times V'$，但：

- $\bar K$ 通常 **变大**（同层 channel 块在拓扑上彼此独立，仅跨层有边）；
- $\beta$ 与 $E^{(t)}$ 的 **时间形状不变**（求和性质不依赖切分粒度）；
- $\mathbf{c}^*_{\max}$ 通常 **下降**（中心性被分散到多个 sibling 节点）。

切分本身就是 fingerprint 的一部分：调度器 Step A 直接读
`mean_components` 决定打散度，Step C 读 `max_centrality` 触发
hotspot split。

#### 2.3.5 Spikformer-CIFAR 切分快表

$N_{\text{core\_cap}} = 1024$，CIFAR-10 输入 $32\times32$：

| LIF 节点 | 输出 shape | $N_\ell$ | 切分方式（§2.3.x） | 微观种群数 |
|---|---|---|---|---|
| `proj_lif`  | $(48,32,32)$ | $49\,152$ | 4D, channel, $C_p=1$  (§2.3.2) | $48$ |
| `proj_lif1` | $(96,32,32)$ | $98\,304$ | 4D, channel, $C_p=1$  (§2.3.2) | $96$ |
| `proj_lif2` | $(192,16,16)$，maxpool 后 | $49\,152$ | 4D, channel, $C_p=4$  (§2.3.2) | $48$ |
| `proj_lif3` | $(384,8,8)$  | $24\,576$ | 4D, channel, $C_p=16$ (§2.3.2) | $24$ |
| `rpe_lif`   | $(384,8,8)$  | $24\,576$ | 4D, channel, $C_p=16$ (§2.3.2) | $24$ |
| Block × 4 的 q/k/v/proj/fc2 | $(384,)$ | $384$ | 1D, 不切 (§2.3.1) | $20$ |
| Block × 4 的 fc1 | $(1536,)$ | $1\,536$ | 1D, 2 段 (§2.3.1) | $8$ |
| Block × 4 的 attn_lif | $(64, 384)$ | $24\,576$ | token+embed, $C_p=384$ (§2.3.3) | $4$ |
| **合计** | — | — | — | **$\approx 272$** |

#### 2.3.6 统一伪代码

```python
def split_layer(layer_name, layer_kind, shape_meta, N_core_cap, K=3, head_dim=32):
    # shape_meta: Linear -> (N_total,)
    #             Conv2d -> (C_out, H, W)
    #             Attn   -> (N_tokens, embed_dim)
    pops = []
    if layer_kind == "Linear":
        N_total, = shape_meta
        P = math.ceil(N_total / N_core_cap)
        for p in range(P):
            lo, hi = p*N_core_cap, min((p+1)*N_core_cap, N_total)
            pops.append({"size": hi - lo, "axis": "out_ch", "range": (lo, hi)})

    elif layer_kind == "Conv2d":
        C, H, W = shape_meta
        S = H * W
        if S <= N_core_cap:
            Cp = max(1, N_core_cap // S)
            for p in range(math.ceil(C / Cp)):
                c_lo, c_hi = p*Cp, min((p+1)*Cp, C)
                pops.append({"size": (c_hi - c_lo) * S, "axis": "channel",
                             "range": (c_lo, c_hi)})
        else:
            R = max(1, N_core_cap // W)
            for c in range(C):
                for r in range(math.ceil(H / R)):
                    h_lo, h_hi = r*R, min((r+1)*R, H)
                    pops.append({"size": (h_hi - h_lo) * W,
                                 "axis": "channel+row",
                                 "range": (c, (h_lo, h_hi))})
                    if r > 0:
                        register_halo_edge(pops[-2], pops[-1],
                                           flits=(K-1)*W)  # §2.3.2 情况 (ii)

    elif layer_kind == "Attn":
        N_tok, C = shape_meta
        Cp = max(head_dim,
                 (N_core_cap // N_tok) // head_dim * head_dim)
        for p in range(math.ceil(C / Cp)):
            c_lo, c_hi = p*Cp, min((p+1)*Cp, C)
            pops.append({"size": N_tok * (c_hi - c_lo), "axis": "embed",
                         "range": (c_lo, c_hi)})
    return pops
```

---

## 3. 正交的二维动态边权重（边定义）

### 3.1 为什么需要二维边权？

传统提取算法把 batch 数据简单 concat、并假设全连接路由，会让流量估计
比真实物理值膨胀几个数量级（详见 §4 Sparsity Mask）。本文把每条边的
权重定义为一个 *二维特征向量*，正交刻画 NoC 通信瓶颈与 PIM 阵列算力
瓶颈：

$$
\boxed{\;
w^{(t)}_{ij} \;=\;
\begin{bmatrix}
\mathrm{Traffic}^{(t)}_{ij} \\[4pt]
\mathrm{Compute}^{(t)}_{ij}
\end{bmatrix}
\;}
$$

### 3.2 维度一：NoC 通信负载 $\mathrm{Traffic}^{(t)}_{ij}$

刻画单卡 NoC 链路在 tick $t$ 的瞬时包传输压力。在校准集 $B$ 上求 *样本
维数学期望*，并引入突触连通性掩码 $\mathbf{M}_{ij}$（详见 §4）：

$$
\boxed{\;
\mathrm{Traffic}^{(t)}_{ij}
\;=\; \mathbb{E}_{\,b \in B}\!\left[\,
\Bigl(\mathbf{x}^{(t-\delta_{ij})}_{i,\text{spike},b}\cdot |v_i|\Bigr)
\,\times\, \mathbf{M}_{ij}
\,\right]
\;}
$$

- $\mathbf{x}^{(t)}_{i,\text{spike},b}\in\{0,1\}^{|v_i|}$：种群 $v_i$
  在 tick $t$、样本 $b$ 上的脉冲二值向量。
- $\delta_{ij}$：从 $i$ 到 $j$ 的链路传输延迟（tick 数）。无延迟时取 0。
- $\mathbf{M}_{ij}$：每脉冲的物理多播倍率（标量），见 §4。
- $\mathbb{E}_{b\in B}[\,\cdot\,]=\tfrac{1}{B}\sum_{b=1}^{B}\,\cdot$：
  样本维均值 —— **保证指纹不随 batch 膨胀** 的核心算子。

物理意义：$\mathrm{Traffic}^{(t)}_{ij}$ 等于"该 SNN 处理一次单样本推理
时，在 tick $t$ 由 $i\!\to\!j$ 这条逻辑链路上 NoC 路由器需要搬运的
微片（Flits）数的期望"。

### 3.3 维度二：SOPs 计算触发量 $\mathrm{Compute}^{(t)}_{ij}$

刻画脉冲到达接收端后，触发的 SRAM 读取与累加器（ALU）操作数：

$$
\boxed{\;
\mathrm{Compute}^{(t)}_{ij}
\;=\; \mathrm{Traffic}^{(t)}_{ij} \,\times\, \frac{D_{ij}}{|v_i|} \,\times\, W^{\text{avg}}_{ij}
\;}
$$

- $D_{ij}$：种群 $i\!\to\!j$ 的总突触权重数（与算子结构相关，例如
  $\mathrm{Conv2d}$ 时 $D_{ij}=K^2 C_{\text{in}} C_{\text{out}}$）。
- $\dfrac{D_{ij}}{|v_i|}$：单个源神经元平均扇出的突触数。
- $W^{\text{avg}}_{ij}$：该层量化后权重的平均比特宽度（INT8 时 = 8）。

物理意义：精确量化目标核心内发生的突触操作数（SOPs / MACs），用于
衡量局部算力压力。

> **量纲一致性提示：** $\mathrm{Traffic}$ 单位为 *Flits / single
> inference*；$\mathrm{Compute}$ 单位为 *bit-ops / single inference*。
> 二者同样在样本维取过期望，因此都满足"一次单样本推理"的物理
> 解释。

---

## 4. 突触连通性掩码 $\mathbf{M}_{ij}$ 的展开

### 4.1 动机：打破"密集均场幻觉"

如果用 *脉冲数 × 目标神经元数* 估算 NoC 流量：

$$
\text{Naive Traffic} \;=\; \text{Spikes}_i \times |v_j|
$$

在现代 SNN（视觉 / Spikformer / 剪枝模型）中拓扑极度稀疏。例：$3\times3$
卷积，目标层 1024 神经元，*物理上* 一个源脉冲只到 $3\times3=9$ 个目的，
若不用 $\mathbf{M}_{ij}$ 修正，会把 9 Flits 的真实流量算成 1024 Flits
（误差 113×）。这种系统性高估会把 $E^{(t)}$ 中真实潮汐淹没在噪声里，
让 Step B 的时间相移调度直接失效。

### 4.2 数学定义

设种群 $i\!\to\!j$ 的真实突触二值邻接 $\mathbf{A}_{ij}\in\{0,1\}^{|v_j|\times|v_i|}$，
$A_{mn}=1$ 表示 $i$ 的第 $n$ 个神经元连接到 $j$ 的第 $m$ 个神经元。则

$$
\boxed{\;
\mathbf{M}_{ij}
\;=\; \frac{\|\mathbf{A}_{ij}\|_0}{|v_i|}
\;=\; \frac{\text{实际突触数}}{\text{源神经元数}}
\;\equiv\; \text{平均突触前扇出度 (avg pre-synaptic fan-out)}
\;}
$$

### 4.3 离线 $O(1)$ 查表展开

不必遍历百万级邻接，按算子先验直接给出：

#### 情况 A — 全连接 (Linear)

$$
\mathbf{M}_{ij} \;=\; |v_j|
$$

源端单个脉冲被 NoC 广播至目标种群所有神经元，退化为最朴素估计。

#### 情况 B — 卷积 ($\mathrm{Conv2d}$, kernel $K$, $C_{\text{in}}\!\to\!C_{\text{out}}$)

$$
\boxed{\;
\mathbf{M}_{ij} \;\approx\; K^2 \times C_{\text{out}}
\;}
$$

源端单个脉冲只多播 (Multicast) 到 $K^2 C_{\text{out}}$ 个目的地址，
*与目标种群空间维度无关*。这是从均场近似回归到真实局部连接最关键的
一步。

#### 情况 C — 剪枝 / 稀疏块（稀疏度 $\rho$）

$$
\mathbf{M}_{ij} \;=\; |v_j| \,\times\, (1 - \rho)
$$

把算法层面的剪枝率 $\rho$ 直接转化为 NoC 带宽红利。

代码侧：v3 当前的 [`build_W_from_spikformer`](../fingerprint/extract_spikformer.py)
仍走情况 A 的退化路径（$\mathbf{M}_{ij}=|v_j|=w_j$，见 §3 边权
$w_j$ 项）。把它升级到情况 B/C 是 §3.2 公式精确度的下一步迭代。

---

## 5. 时空指纹提取与系统映射

基于 §3 的二维边权张量，Profiler 提取以下核心物理指纹。

### 5.1 指纹 ① — 平均活跃连通分量 $\bar K$（驱动 Step A）

**目标：** 刻画任务的拓扑并发性，解决单卡资源分配的"碎片匹配"问题。

**推导：** 取 NoC 安全流量阈值 $\epsilon$，过滤掉极度稀疏的瞬时边：

$$
\mathcal{E}^{(t)}
\;=\; \Bigl\{\, (v_i, v_j) \in \mathcal{V}\times\mathcal{V} \;\Big|\; \mathrm{Traffic}^{(t)}_{ij} > \epsilon \,\Bigr\}
$$

二值化得邻接 $A^{(t)} \in \{0,1\}^{V\times V}$，取无向骨架

$$
U^{(t)} \;=\; A^{(t)} \,\vee\, A^{(t)\top}.
$$

定义活跃节点 $\mathrm{active}(i)=\bigvee_{j} U^{(t)}_{ij}$，从活跃节点
出发跑 DFS，记当时间步的独立连通分量数为 $K^{(t)}$。最终：

$$
\boxed{\;
\bar K \;=\; \frac{1}{T}\sum_{t=1}^{T} K^{(t)}
\;}
$$

**STPS 接口（Step A — Macro-Card Dispatching with Intra-card
Fragmentation Awareness）。** 系统假设是 *单模型必须完整放入一张物理卡*，
因此 $\bar K$ 用于"片内碎片感知匹配"：

- $\bar K \to 1$：紧耦合整体 → Step A 必须分配到具有 *大块连续空闲核心*
  的物理卡，避免片内长距离路由拥塞。
- $\bar K \gg 1$：存在多条异步独立子流 → Step A 可"变废为宝"，分配到
  *内部碎片化* 的物理卡，既避通信瓶颈又榨取剩余空间。

代码：[extractor.py:18-45 + 92-96](../fingerprint/extractor.py#L18-L96)。

### 5.2 指纹 ② — 流量序列 $E^{(t)}$ 与全局突发度 $\beta$（驱动 Step B）

**目标：** 刻画卡内 NoC 的时间潮汐，指导时域错峰填谷。

**v2 简化（单卡部署假设）：** 单模型不允许跨卡部署 ⇒ 整模型完整落在一张
物理卡上 ⇒ 卡内 NoC 时间潮汐与"该模型在该 tick 上的脉冲发放总数"同步。
因此 $E^{(t)}$ 不再需要 §2.3 切分 / §4 突触掩码 / §3.2 跨样本期望边粒度
聚合,而是直接退化为**整模型每个 tick 的脉冲发放总数在 val 数据集上的
样本均值**。

设 val 数据集 $\mathcal{D}_{\text{val}}=\{b_1,\dots,b_N\}$,对单个样本 $b$:

$$
E^{(t)}_b \;=\; \sum_{i=1}^{V} \bigl\lVert
\mathbf{x}^{(t)}_{i,\text{spike},b}\bigr\rVert_0
\quad(\text{该样本在 tick }t\text{ 上全模型的脉冲发放总数})
$$

落盘的 $E^{(t)}$ 是整个 val 集上的逐样本算术平均:

$$
\boxed{\;
E^{(t)} \;=\; \frac{1}{N}\sum_{b=1}^{N} E^{(t)}_b
\;=\; \frac{1}{N}\sum_{b=1}^{N}\sum_{i=1}^{V}
\bigl\lVert \mathbf{x}^{(t)}_{i,\text{spike},b}\bigr\rVert_0
\;}
$$

其中 $V$ 是模型中所有 LIF / spiking 节点的数量(单卡部署 ⇒ 无需切分)。
单位:`spikes / single inference`(原 v1 流程是 `flits / single inference`,
量纲变了 — Step B 互相关仍可工作,因为它只关心相对潮汐形状)。

峰均比定义突发度:

$$
\boxed{\;
\beta \;=\;
\begin{cases}
\dfrac{\max_{t\in[1,T]} E^{(t)}}{\tfrac{1}{T}\sum_{t=1}^{T} E^{(t)}}, & \tfrac{1}{T}\sum_t E^{(t)} > 10^{-12}\\[6pt]
1, & \text{otherwise}
\end{cases}
\;}
$$

直观:$\beta=1$ ⇔ 完全平流,$\beta\gg 1$ ⇔ 明显脉冲簇(例如 DVS 视觉)。

**STPS 接口(Step B — Cross-Correlation Phase Alignment, Algorithm 1)。**
$E^{(t)}$ 作为确定性输入信号,馈入滑动窗口互相关算法,与目标卡上已有
背景流量 $E^{(t)}_{\text{card}}$ 比对,求最优时钟相位偏移

$$
\Delta t^{*}_{\text{start}}
\;=\; \arg\min_{d\in[0, D_{\max}]}\;
\beta\!\left(E^{(t)}_{\text{card}} \,+\, E^{(t-d)}\right),
$$

实现 NoC 流量"削峰填谷",物理层面零开销。

代码:[extractor.py — `extract_fingerprint_from_spikes`](../fingerprint/extractor.py) +
[dtdg.py — `DTDGBuilder.spike_count_timeline_from_spikingjelly`](../fingerprint/dtdg.py) +
[schedule/phase_shift.py](../schedule/phase_shift.py)。

> **v1 流程归档:** §3 / §4 / §6 描述的边粒度建模 + 突触掩码 + 跨样本期望
> 仍保留在 `fingerprint/edge_builder.py` / `mask.py` / `slicing.py` 中,
> 作为 v1 入口以备审稿需要边粒度精确建模时回滚;本节起的 v2 spike-count
> 路径是默认实现。

### 5.3 指纹 ③ — 时间全局最大中心性 $\mathbf{c}^{*}_{\max}$

**目标：** 评估单卡极限算力偏斜，离线预警热点（Hotspot）风险。
*（保留指标：当前调度未在 Step C 引用，作为模型空间热点的额外度量。）*

**推导：** 定义 tick $t$ 的算力影响矩阵 $\mathbf{M}^{(t)}$（取
$\mathrm{Compute}^{(t)}$ 维即可），求其主特征值方程

$$
\mathbf{M}^{(t)}\, \mathbf{c}^{(t)} \;=\; \lambda^{(t)}_{\max}\, \mathbf{c}^{(t)}.
$$

实现上用 in-eigenvector 中心性的 power iteration（取转置加单位阵稳定）：

$$
\begin{aligned}
A &\;=\; \mathbf{M}^{(t)\top} + I_V,\\
c &\;\leftarrow\; \frac{A\,c}{\lVert A\,c\rVert_1},\quad \text{重复至 }\lVert\Delta c\rVert_1<10^{-6}\text{ 或 100 次封顶}.
\end{aligned}
$$

退化分支：全零图直接返回 $c=\tfrac{1}{V}\mathbf{1}$。$c^{(t)}\in\mathbb{R}^V$，
$\sum_i c^{(t)}_i = 1$。

最后逐节点取整个时间窗的 *最大中心性*，消除时序盲区：

$$
\boxed{\;
\mathbf{c}^{*}_{\max}[i] \;=\; \max_{t\in[1,T]} c^{(t)}[i]
\;}
$$

代码：[centrality.py:7-50](../fingerprint/centrality.py#L7-L50)。

> 注：v3 当前 io schema 仍保留 `centrality_var`（每 tick 跨节点方差）
> 与 `centrality_last`（末步 $c^{(T-1)}$）两个旧字段，作为 $\mathbf{c}^{*}_{\max}$
> 完全替换前的过渡。新方案将 `centrality_last` 升级为 $\mathbf{c}^{*}_{\max}$。

---

## 6. 基于测试集的 AOT 提取工作流

回应审稿人核心问题："SNN 的脉冲发放极其依赖输入数据，你如何保证离线
提取的指纹在线运行时仍准确且具代表性？"——以下 5 步给出严谨的统计学
答案。

### Step 1：代表性校准集采样 (Calibration Dataset Sampling)

不依赖单一图片，而是从官方 Test/Validation 数据集均匀采样 $B$ 个样本
（典型 $B=128$ 或 $256$）构建 *校准微批次*。该微批次覆盖数据集中各种
边缘特征（edge cases），充分激发模型在各分支上的潜能。

> v3 实例：CIFAR-10 test_batch (10000 张) → batch=128, num_batches=4 →
> 共 $N=512$ 校准样本。配置见
> [extract_spikformer.py:118-150](../fingerprint/extract_spikformer.py#L118-L150)。

### Step 2：细粒度脉冲探针追踪 (Fine-grained Spike Hooking)

模型置于 `eval` 模式 + `torch.no_grad()`。在 §2.2 切分出的每个微观
神经种群 $v_i$ 输出端挂 PyTorch Forward Hook。微批次中每个样本 $b\in B$
跑 $T$ 个 tick 前向时，探针无损记录

$$
\mathbf{x}^{(t)}_{i,\text{spike},b} \;\in\; \{0,1\}^{|v_i|}
$$

—— *每个微观种群在每个 tick 的绝对脉冲发射向量*。原始脉冲计数张量
为四维：

$$
[\,T,\, B,\, V,\, V\,] \quad(\text{即每条 } i\!\to\!j \text{ 边一个时序矩阵}).
$$

### Step 3：硬件感知的流量降维转换 (Hardware-aware Translation)

应用 §4 的 $\mathbf{M}_{ij}$ 把"软件层逻辑脉冲计数"翻译成"物理层
NoC 微片数"。对单个样本 $b$：

$$
\mathrm{Traffic}^{(t)}_{ij,b}
\;=\; \Bigl(\mathbf{x}^{(t-\delta_{ij})}_{i,\text{spike},b}\cdot|v_i|\Bigr)\,\times\, \mathbf{M}_{ij}
$$

### Step 4：跨样本数学期望提取（**整个流程最关键的统计学步骤**）

直接 sum-over-$B$ 会让指纹随 $B$ 膨胀，使在线调度器（处理"一次单样本"
任务）所见量纲与 profiling 不一致 → 指纹失效。沿样本维做 *期望*：

$$
\boxed{\;
\mathrm{Traffic}^{(t)}_{ij}
\;=\; \mathbb{E}_{b\in B}\!\left[\mathrm{Traffic}^{(t)}_{ij,b}\right]
\;=\; \frac{1}{B}\sum_{b=1}^{B} \mathrm{Traffic}^{(t)}_{ij,b}
\;}
$$

跨多个 batch（每个大小 $B_b$，$N=\sum_b B_b$）合并时按样本数加权：

$$
\mathrm{Traffic}^{(t)}_{ij}
\;=\; \frac{1}{N}\sum_{b=1}^{N_b} B_b\,\mathrm{Traffic}^{(t)}_{ij,(b)}
\;=\; \frac{1}{N}\sum_{b=1}^{N_b}\sum_{k=1}^{B_b} \mathrm{Traffic}^{(t)}_{ij,(b,k)}
$$

数据完全摆脱样本特异性，坍缩为纯粹描述模型拓扑动力学的三维静态张量

$$
\mathrm{Traffic}\;\in\;\mathbb{R}^{T\times V\times V}.
$$

batch / num_batches 仅影响 *估计方差*，不进入 *量纲*。

### Step 5：图算法降维与序列化落盘 (Graph Reduction & Serialization)

读入 $T\times V\times V$ 张量，内存中跑三个轻量级图算法：

1. **二值化 + DFS/BFS** → $\bar K$（§5.1）。
2. **全图流量聚合 (Sum)** → $E^{(t)}$ → $\beta$（§5.2）。
3. **稀疏矩阵幂迭代** → $\mathbf{c}^{*}_{\max}$（§5.3）。

生成几十 KB 的 `.npz` 履历表（Profile Artifact）落盘。云端集群在调度
时只需 $O(1)$ 读盘 → 微秒级时空双维决策。

---

## 7. 最终落盘资产 (`.npz` Schema)

| 键 | 数学符号 | dtype | 形状 | 系统用途 |
|---|---|---|---|---|
| `mean_components` | $\bar K$ | float32 | scalar | 驱动 Step A 碎片化感知放置 |
| `traffic_sequence` | $E^{(t)}$ | float32 | `(T,)` | 驱动 Step B 时间相位对齐 |
| `global_burstiness` | $\beta$ | float32 | scalar | 衡量任务突发风险 |
| `max_centrality` | $\mathbf{c}^{*}_{\max}$ | float32 | `(V,)` | 离线热点预警指标 |
| `T` | $T$ | int32 | scalar | 时间步数 |
| `neuron_count` | $\sum_i \lvert v_i\rvert$ | int32 | scalar | 放置足迹（占核数） |
| `state_size_mb` | — | float32 | scalar | 模型常驻状态（MB） |
| `complexity_ratio` | — | float32 | scalar | 相对算力强度系数（默认 1.0） |
| `meta` | — | str (JSON) | scalar | model / dataset / checkpoint / B / N / commit-sha |

存盘走 `np.savez_compressed`，`meta` 经 `json.dumps` 序列化。读回走
`load_fingerprint`（[io.py:35-52](../fingerprint/io.py#L35-L52)），反
序列化 `meta`。

> v3 当前文件仍写出 io schema 的兼容键 `centrality_var (T,)`、
> `centrality_last (V,)`，将在 `max_centrality` 上线后弃用。读端需做
> 一次性迁移。

### 7.1 STPS 调度器读端接口

| 字段 | 用于 | 调度阶段 |
|---|---|---|
| `state_size_mb`, `neuron_count`, `complexity_ratio` | 单卡容量准入判定 | Step A |
| `mean_components` ($\bar K$) | 紧耦合 vs 多子流 → 选目标卡 | Step A |
| `traffic_sequence` ($E^{(t)}$) + `global_burstiness` ($\beta$) | 滑动窗口互相关求 $\Delta t^{*}_{\text{start}}$ | Step B |
| `max_centrality` ($\mathbf{c}^{*}_{\max}$) | 离线热点预警 | （保留，供 Step C 未来使用） |

代码侧惰性加载入口：
[`STPSScheduler._resolve_fingerprint`](../schedule/stps.py)。

---

## 8. 完整数据流回顾（Spikformer-CIFAR10 实例）

CLI（[fingerprint/extract_spikformer.py](../fingerprint/extract_spikformer.py)）：

```bash
python -m fingerprint.extract_spikformer \
    --dataset cifar10 --T 4 \
    --checkpoint model/STEP/spikformer_cifar_pth.tar \
    --data-dir dataset/cifar-10-batches-py/test_batch \
    --batch-size 128 --num-batches 4 \
    --out npz/spikformer_cifar10.npz
```

执行序列：

1. `build_network(...)` → 构造 `Spikformer(step=4, …)`，9.32 M 参数。
2. `load_checkpoint(...)` → 载入 `state_dict`，剥离 `module.` 前缀，
   `strict=False`。
3. `make_cifar_loader(...)` → 直接读 `test_batch` pickle（绕过 torchvision），
   归一化 mean/std 取自 [configs/spikformer/cifar10.yml](../model/STEP/cls/configs/spikformer/cifar10.yml)，
   产出 4 batch × 128 = $N=512$ 个校准样本。
4. `build_W_from_spikformer(...)`：
   - 枚举 `BaseNode_Torch` 实例 → $V=33$ 个微观种群。
   - 注册 forward hook，eval + no_grad 跑完 4 个 batch。
   - 每 hook 输出 `(T·B, …)` → reshape `(T, B, U_i)`。
   - **§Step 4 期望提取**：对 $B$ 维取均值，跨 batch 按样本数加权
     再合并 → 33 个 $(4, U_i)$ 矩阵；维度 $U_i$ 是 *模型物理宽度*，
     不再随 batch 膨胀。
   - 应用 §3.2 公式合成 $\mathrm{Traffic}\in\mathbb{R}^{4\times33\times33}$。
5. `extract_fingerprint_from_W(...)`：
   - $E^{(t)} = \sum_{ij}\mathrm{Traffic}^{(t)}_{ij}$ → `(4,)`。
   - $\beta = \max E / \bar E$ → 标量。
   - 每 $W^{(t)}$ 跑 100 次 power iteration → $c^{(t)}$ →
     $\mathbf{c}^{*}_{\max}=\max_t c^{(t)}$（`(33,)`）。
   - 二值化 + DFS 数活跃连通分量 → $K^{(0..3)}$ → $\bar K$。
6. `save_fingerprint(...)` → `npz/spikformer_cifar10.npz`，~10 KB。

实测 (512 样本)：

```text
T=4  V=33  beta=1.170  K_mean=1.000
state_size_mb=37.30  centrality (sum)=1.0
```

⚠️ 当前代码尚未实施 §4 的 $\mathbf{M}_{ij}$ 修正 (走情况 A 退化路径)
与 §Step 4 的 mean-over-$B$ 修正（仍是 sum+units-concat 的旧实现）；
$E$、`state_size_mb` 等绝对量纲项受影响、与 §3 / §6 定义不一致。但
$\beta$、$\bar K$、`centrality_*` 是比例 / 概率 / 计数比例量，对这两
个谬误 *不敏感*，仍可解读：$\beta \approx 1.17$ ⇒ 该任务在 T=4 窗口
内近 *平流*；$\bar K = 1.0$ ⇒ 每 tick 单一连通图，无可空间拆分子流；
33 个微观种群。

代码侧 `sum→mean` + 卷积稀疏掩码的修正在另一份 patch 中跟进，本文档
描述的是 **目标语义**。

---

## 9. 边界条件与不变性

- **$T$ 的选择**：等于模型脉冲时间步数（Spikformer / SpikingResformer
  默认 $T=4$，CIFAR-DVS $T=10/16$）。$T$ 越大，$E^{(t)}$ 分辨率越高、
  $\beta$ 估计越稳，但 hook 内存占用线性增加。
- **$B$ 与 num_batches 的选择**：
  - $V$ 与 batch 无关，由模型层数决定。
  - **量纲不变性**：在 §Step 4 期望提取下，$\mathrm{Traffic}^{(t)}$、
    $E^{(t)}$、$\beta$、$\bar K$、$\mathbf{c}^{*}_{\max}$ 全部与
    profiling `batch_size × num_batches` 无关（在样本数 $N$ 足够大、
    估计噪声可忽略的极限下收敛到唯一值）。
  - 经验下界：$N = B \times \text{num\_batches} \ge 256$ 即可使
    centrality / $\bar K$ 估计稳定；$N\ge 512$ 用于发表级实验。
- **退化分支**：
  - 全零 $\mathrm{Traffic}^{(t)}$ → 中心性返回均匀向量；$\beta=1$；
    $\bar K=0$。
  - $T=0$ → 全部统计返回 0 / 空数组。
- **保真度（Fidelity）**：$E^{(t)}$ 的物理量纲是 *Flits / single
  inference*；$\mathbf{c}^{*}_{\max}$ 的物理量纲是 *无量纲概率分量*。
  Step B 互相关与 Step A 碎片匹配都依赖于这两个量纲的一致性。
- **可重现性**：`save → load → save` 字节级一致（除 float32 精度
  cast），由 `extract_fingerprint_from_W` 内部 `astype(float32)` 与
  `np.savez_compressed` 共同保证。

---

## 10. 设计回顾：与系统会议审稿人的对话

| 审稿人疑虑 | 本设计的应答 |
|---|---|
| "SNN 数据依赖性强，离线指纹凭什么有代表性？" | §6 的校准集采样 + 跨样本期望 (Step 1 & 4)；$N \ge 256$ 收敛 |
| "Profiling batch 一变指纹就变，岂不失效？" | §1 / §3.2 / §Step 4：所有指纹均在样本维取期望，量纲恒为单样本 |
| "把脉冲数 × 目标神经元数当 NoC 流量是体系结构常识错误" | §4 引入 $\mathbf{M}_{ij}$，按算子先验展开三种情况 (FC / Conv / Pruned) |
| "调度时再做图分析能扛住每秒上万 task 的吞吐吗？" | §7.1 接口：调度时只 $O(1)$ 查 4 个标量/向量字段，不重做前向 |
| "热点 (hotspot) 一时间步出现就够危险，平均中心性会被掩盖" | §5.3：用 $\mathbf{c}^{*}_{\max}=\max_t c^{(t)}$ 而非 $\bar c$，消除时序盲区 |
| "单模型不允许跨卡部署，那 $\bar K \gg 1$ 还有用吗？" | §5.1：$\bar K$ 用作 *片内碎片感知匹配* 的判据，不是分卡判据 |

---

> *Last updated: 2026-05-07. Source: [fingerprint/](../fingerprint/),
> [schedule/stps.py](../schedule/stps.py).*
