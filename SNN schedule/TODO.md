# Evaluation — 待补实验 (TODO)

> 这些是 RQ1 (`tab:q0_main`) 主表为达到 "six out of six" 完整口径还缺的数据。
> 论文当前用 "every arrival/scale cell tested" 的口径绕过缺口，补完后可恢复
> "six out of six / all arrival modes / both scales"。
> 项目解释器：`/root/miniconda3/envs/snn/bin/python`

---

## 0. 先决：核实现有 `tab:q0_main` 数值的真实出处 ⚠️ 阻塞项

**问题**：现有 RQ1 主表 (`SNN schedule/article.tex`, `tab:q0_main`) 的 `max/min`
列与仓库里**任何** CSV 都对不上。例如表里 4-card Poisson `RR max/min = 7.42`，
但 `data/q0/arrival_summary.csv` 同 cell 是 `5.01`；其余 CSV 也无一行能同时
匹配该表的 `card-CV` 与 `max/min`。

**影响**：在出处确认前，不能从 `arrival_summary.csv` 取 p99 拼进主表 —— 会造成
"同一行的不同列来自不同 run" 的不一致，正是 reviewer 会抓的。

**待办**：

- [ ] 找出/重新生成 `tab:q0_main` 当前数值的权威来源 CSV（哪个脚本、哪次 run、
      什么 tasks/seed 配置）。

- [ ] 确认来源后，要么 (a) 以该来源统一补 p99 列，要么 (b) 以
      `data/q0/arrival_summary.csv` 为唯一权威源**整表重写**（card-CV / JFI /
      max/min / throughput / cong-ratio / cong-wait + 新增 p99），保证同源。

> 备注：`arrival_summary.csv` 的 `card_cv / throughput / cong` 列**确实**与论文表
> 吻合（如 Poisson RR card-CV 0.267、STPS mixed cong 0.245），只有 `max/min` 列
> 对不上。优先排查 max/min 是否取自 `time_card_max_min_ratio_*` 等其它字段。

---

## 1. p99 列（数据多数现成，待出处确认后即可补）

主表 Metrics 段承诺报告 p99，但 `tab:q0_main` 无 p99 列（当前已把 p99 措辞
scope 到 RQ4 长尾分析以避免悬空承诺）。

**现状**：

- 4-card 三 arrival (P/B/M)：`data/q0/arrival_summary.csv` 已有 `p99_delay_mean`。
- 16-card P/B：`data/q0/scale16_summary.csv` 已有 `p99_delay_mean`。
- 16-card Mixed：**无**（见 §2）。

**待办**：

- [ ] §0 出处确认后，给 `tab:q0_main` 增加 `p99 down` 列。
- [ ] 注意主表已是 9 列 `table*`（双栏宽），加第 10 列需检查是否溢出；必要时
      `\setlength{\tabcolsep}` 或缩短表头。

- [ ] 补列后把 Metrics 段 (iv) 的措辞从 "use to characterise the tail trade in
      Section RQ4" 改回 "report ... p99 ... in the main table"。

---

## 2. 16-card × Mixed arrival cell（需重新跑） ⚠️ 唯一真正缺数据的实验

**缺口**：`tab:q0_main` 的 16-card 区只有 Poisson / Bursty 两行，缺 Mixed。
`script/q0_run.py` 的 `run_scale16()` 把 `modes = ["poisson", "bursty"]` 硬编码，
所以现有 `scale16_summary.csv` 跑不出 Mixed。

**跑法**（两选一）：

- [ ] 改 `script/q0_run.py:run_scale16()` 的 `modes` 加入 `"mixed"`，重跑 scale16；或
- [ ] 写独立补数脚本，复用 `_run_one` + `_aggregate`，只跑：
      - 调度器：`rr, bestfit, drf, p2c, stps`（`SCHEDULERS`）
      - 配置：`cards=16, tasks=3200, steps=512, arrival=mixed`
      - seeds：`[21, 42, 99, 123, 2024]`（`SEEDS`）
      - bw_cap=9e5, d_max=2, horizon=64, centrality_split_threshold=0.2
      - fingerprint-dir = `npz/`（7 个 npz 已就绪：flat / pulse_t8 / pulse_t16 /
        bursty / spikformer_cifar10 / qkformer_cifar10 / spikingresformer_ti_imagenet）

      - 输出：`data/q0/scale16_mixed_summary.csv`（勿覆盖现有 scale16）
- 规模：5 调度器 × 1 mode × 5 seed = 25 run（× 3200 tasks × 512 steps）。

**补完后**：

- [ ] 把 16-card Mixed 行填入 `tab:q0_main`。
- [ ] 恢复 "six out of six" 口径：
      - `tab:q0_main` caption："every {arrival, scale} cell tested" → "six out of six"
      - "The headline" 段："every arrival/scale cell" → "six out of six"
      - "The cost" 段："Across the cells tested" → "Across the six cells"
      - Abstract："in every arrival/scale cell we test" → "across all arrival modes
        and at both 4-card and 16-card scales"

---

## 3. （可选）确认 max/min 指标定义与 LIF 的关系

`tab:q0_main` 同时有 `max/min` 列，正文 Metrics 段也定义了 `LIF = max/mean`。
两者高度相关（都测尾端 outlier）。若审稿压版面，可考虑二选一，或在 caption
注明二者差异（max/min 是双端，LIF 是相对均值的单端）。

- [ ] 决定是否保留两列，或合并。

---

## 完成判定

- [ ] §0 出处确认 → §1 p99 列补全 → §2 16-card Mixed 补全 → 恢复 six-out-of-six
- [ ] 本地 `latexmk` 编译：appendix 显示 "Appendix A"，§7 多表区无 overfull hbox
