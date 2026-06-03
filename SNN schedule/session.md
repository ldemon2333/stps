# 会话历史记录 (Session Export)

> 工作区：`/root/v3`（STPS — Spatio-Temporal Proactive Scheduling）
> 解释器：`/root/miniconda3/envs/snn/bin/python`（conda env `snn`）

---

## 会话 1：STPS 长尾任务图（4 卡 + 16 卡）

**用户请求**：基于 `figures/q0/scale16_long_tail_bursty.pdf`，只要 STPS 算法的长尾任务图，单独生成，包括 4 卡和 16 卡、poisson + bursty，共 4 张图。

**完成内容**：
- 新建脚本 `script/q0_stps_long_tail.py`，复用 `q0_run.py` 的 `SEEDS / DEFAULT_TASKS / _build_mixed_fp_dir / _run_one`。
- 16 卡延迟：从既有 `data/q0/scale16_task_delays.csv`（过滤 `scheduler == "stps"`）读取。
- 4 卡延迟：现场重跑 STPS × {poisson, bursty} × 5 seeds（`cards=4, tasks=800, bw_cap=9e5, d_max=2, horizon=64`，mixed 指纹）。
- 单面板 log-y 直方图，红色 `#d62728`，横轴 `Task delay (ticks)`，纵轴 `# tasks (log)`，黑色虚线为 pooled P99，标题含 `(P99=…, tail=#>P99/N, …%)`，5 seeds 池化。

**产出文件**（PDF + PNG）：
| 文件 | 卡数 | Arrival |
|---|---|---|
| `figures/q0/stps_long_tail_4card_poisson.pdf` | 4 | poisson |
| `figures/q0/stps_long_tail_4card_bursty.pdf` | 4 | bursty |
| `figures/q0/stps_long_tail_16card_poisson.pdf` | 16 | poisson |
| `figures/q0/stps_long_tail_16card_bursty.pdf` | 16 | bursty |

---

## 会话 2：模型询问

**用户请求**：「你是什么模型」/「你是什么模型」

**回答**：GPT-5.3-Codex。

---

## 会话 3：Phase-Shift `D_MAX` 敏感性分析（轻拥塞档 C）

**用户请求**：在轻拥塞档 `bw9e5_t400`（`BW_MAX=9e5`、tasks=400、demand 折半、cap binding 弱、baseline cong≈0.08–0.10、reject≈0.14–0.16）工作点上，把 `D_MAX = 2, 4, 8, 16` 跑四组实验，重点关注吞吐量、负载均衡指标、p99 延迟、拥塞程度变化，分析结果 + 表格写入 `docs/article.md`。

**完成内容**：
- `D_MAX=16` 复用既有数据；新建 `script/_run_dmax_sweep_t400.sh` 重跑 `D_MAX∈{2,4,8}`（各 100 run，共 300 run）。
  - 命令：`script/q2_run.py main4 --bw-max 9e5 --tasks 400 --d-max {2,4,8} --name main4_bw9e5_d{D}_t400`。
- 新建 `script/_agg_dmax_sweep_t400.py` 从四个 summary CSV 聚合 markdown 表（数值直接来自 CSV）。
- 分析与表格写入 `docs/article.md`。

**核心发现**：
- **拥塞**：非-STPS 基线只要开 phase（任意 `D_MAX≥2`）`cong.ratio` 即从 0.08–0.10 归零 —— 是开关不是旋钮；STPS 残留 ~0.08，随 `D_MAX` 缓慢下行。
- **负载均衡**：CV/JFI/LIF 随 `D_MAX` 单调改善、`D=8` 附近见顶（RR poisson CV 0.676→0.44）；`Max/Min` 从 8–13 压到 3–6。例外 P2C：`D=2` 反而恶化 CV，需大预算才转正。
- **p99 延迟**：与 `D_MAX` 正相关（offset 等待随预算线性增长）；非-STPS 仍净降但收益缩水；STPS 始终被抬高。
- **吞吐**：开 phase 后掉到 0.59–0.64，随 `D_MAX` 增大因 `reject_bw` 下降而部分回收；`tput(xc)` 证明损失主因是 cap 拒绝 + 窗口边界而非冷启动。

**推荐工作点**：延迟敏感取 `D_MAX=2`，均衡敏感取 `D_MAX=8`，STPS 固定 `D_MAX=2`。

**数据文件**：`data/q2/main4_bw9e5_d{2,4,8,16}_t400_{raw,summary,percard_*}.csv`。

---

## 会话 4：完成 `SNN schedule/TODO.md` 的实验（进行中）

**用户请求**：完成 `SNN schedule/TODO.md` 列出的实验，结果保存到 `SNN schedule/experiment.md`。

**TODO.md 内容概要**：
- **§0（阻塞项）**：核实 `tab:q0_main` 主表 `max/min` 列的权威来源 CSV —— 表中 4-card Poisson `RR max/min = 7.42`，但 `data/q0/arrival_summary.csv` 同 cell 是 `5.01`，对不上。
- **§1**：给 `tab:q0_main` 补 p99 列（4-card 三 arrival 与 16-card P/B 数据多已现成，16-card Mixed 缺）。
- **§2（唯一真正缺数据）**：16-card × Mixed arrival cell —— `run_scale16()` 把 `modes` 硬编码为 `["poisson","bursty"]`，需新增 mixed，输出 `data/q0/scale16_mixed_summary.csv`（5 调度器 × 1 mode × 5 seed = 25 run，cards=16, tasks=3200, steps=512, bw_cap=9e5, d_max=2, horizon=64）。
- **§3（可选）**：确认 max/min 与 LIF 定义关系，是否合并列。

**已收集的上下文**：
- `arrival_summary.csv` 关键指标已 dump（cv / max_min / lif / tput / cong / p99）；其 `card_cv / throughput / cong` 与论文表吻合，仅 `max/min` 对不上。
- `tab:q0_main`（`SNN schedule/article.tex` L383–435）完整表格已读取：4 卡 P/B/M + 16 卡 P/B，列为 card-CV / JFI / max/min / throughput / cong.ratio / cong.wait。
- `q0_run.py` 关键函数定位：`_aggregate`(L241)、`run_main`(L439)、`run_arrival`(L456，含 mixed)、`run_scale16`(L492，modes 硬编码 P/B)。
- summary CSV 列结构已确认（`arrival_summary.csv` 33 列、`scale16_summary.csv` 74 列）。

**状态**：上下文收集完成，尚未开始跑 16-card Mixed 实验或写 `experiment.md`。

---

*（本文件为会话历史导出，按用户请求生成于 `/root/v3/SNN schedule/session.md`）*
