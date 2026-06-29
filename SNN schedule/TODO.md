# Evaluation — 待补实验 (TODO)

> 这些是 RQ1 (`tab:q0_main`) 主表为达到 "six out of six" 完整口径还缺的数据。
> 论文当前用 "every arrival/scale cell tested" 的口径绕过缺口，补完后可恢复
> "six out of six / all arrival modes / both scales"。
> 项目解释器：`/root/miniconda3/envs/snn/bin/python`

---

## A. 表格级实验设计重写（先改叙事，再补数据）

### A.0 总体实验主线

当前表格最大的问题是：`tab:q0_main` 同时放了 CV/JFI、max/min、throughput、
congestion、p99，caption 又把 STPS 写得像“全面负载均衡更好”。但表内数据并不支持
这个强口径：STPS 在 CV/JFI 上通常不是最优，真正稳定的优势是 NoC congestion，
以及 16-card 下 worst-card tail skew 没有失控。

重写后的 Evaluation 应围绕一个更可信的主张展开：

> STPS 不是传统平均负载均衡器，而是一个 congestion-bounded admission scheduler。
> 它用少量 throughput 和 tail-delay 代价，稳定降低 NoC 拥塞；在规模变大时，它还能
> 控制 worst-card tail skew，但不保证 CV/JFI 这类平均离散度指标处处最优。

因此所有表格都要按三类指标分开解释：

- **NoC 效果**：congestion ratio、cong. wait。这里应是 STPS 的主胜利点。
- **负载均衡效果**：CV/JFI/LIF/max-min。这里要区分 average dispersion 与
  tail skew，不能笼统说“负载均衡更好”。
- **代价**：throughput、mean offset、p99/tail delay。这里要说明 STPS 花了什么。

建议新的 RQ 组织：

- **RQ1 End-to-end Pareto tradeoff**：STPS 是否在真实混合 workload 上形成更好的
  congestion-vs-cost 折中？
- **RQ2 Stage contribution**：Stage A 和 Stage B 分别解决什么问题？二者为什么必须组合？
- **RQ3 Operating regimes**：什么时候 Stage A 有效，什么时候 Stage B 有效，什么时候指标会反转？
- **RQ4 Tail cost**：STPS 的代价是否集中在最坏 1% 请求上，而不是拖慢整体 body？

---

### A.1 `tab:q0_main`：主表应证明“端到端 Pareto 折中”，不是证明全面负载均衡

**当前问题**：主表 caption 说 STPS 在 16 cards 上控制 max/min，但 CV/JFI 多数
cell 并不是 STPS 最好。Reviewer 会问：如果这是负载均衡实验，为什么 JFI 更低、
CV 更高？

**应该比较什么**：

- 比较对象：RR、BestFit、DRF、P2C、STPS。
- 规模：4 cards 与 16 cards。
- 到达模式：Poisson、Bursty、Mixed，补齐 6 个 `{arrival, scale}` cell。
- 指标分组：
  - congestion：cong. ratio、cong. wait；
  - balance：建议保留 CV/JFI，再增加或替换为 `LIF = max/mean`，弱化 max/min；
  - cost：throughput、p99。

**预期效果**：

- STPS 在所有 6 个 cell 上降低 cong. ratio 和 cong. wait，这是主结论。
- STPS 在 throughput 上低 1--2%，p99 更高，这是明确代价。
- STPS 不应被描述为 average load balance 最优；CV/JFI 允许不是最优。
- 在 16-card 上，STPS 应体现 tail skew 更稳：`LIF` 或 max/min 的最坏值不爆炸，
  baseline 至少有一个 arrival 下出现明显 worst-card tail。

**表格/文字改法**：

- Caption 第一层只说 congestion win 和 cost。
- 第二层说 scale 下的 tail-skew containment，不要说“load balance overall”。
- 正文增加一句：`CV/JFI measure average dispersion; max/min or LIF measures tail risk.`
- 如果版面紧张，优先保留 `LIF` 而不是 `max/min`。`max/min` 对最小负载卡过度敏感，
  `LIF=max/mean` 更像“最坏卡是否压垮集群”。

**成功判定**：读者看完 Table 1 后应得出：“STPS 主要赢在 NoC 拥塞；传统均衡指标
不是处处赢，但 16-card worst-card tail 更受控。”

---

### A.2 `tab:q0_cold`：解释 throughput cost，不承担负载均衡结论

**当前作用**：说明 STPS throughput 下降不是简单由 start offset 造成，而是 congestion
avoidance 的结果。

**应该比较什么**：

- 比较对象：16-card 下每个 arrival 的最佳 throughput baseline vs STPS。
- 至少包含 Poisson、Bursty；如果主表补 Mixed，这里也应补 Mixed。
- 指标：mean cold-start offset、raw throughput、cold-start-excluded throughput、gap closed。

**预期效果**：

- 去掉 cold-start 后，只能弥合很小一部分 throughput gap（当前约 0.16--0.17%）。
- 说明 STPS 的主要成本不是“等了几个 tick”，而是它主动避开 NoC peak，导致单位时间注入更保守。

**表格/文字改法**：

- 不要让该表看起来在解释 load balance。
- Caption 改成：`Throughput cost decomposition`。
- 正文连接 Table 1 的 throughput cost：STPS 花 throughput 买 congestion reduction。

**成功判定**：读者能接受 1--2% throughput loss 是机制性代价，而不是实现 artefact。

---

### A.3 `tab:q2_phase`：Stage A / Stage B 消融表，要改成“两个轴”的证据

**当前问题**：表里 baseline + phase-shift 的 congestion 几乎清零，但 CV 暴涨；
full STPS 的 congestion 反而不是最低。这个表如果只看单列，会让 STPS 显得不强。

**应该比较什么**：

- 比较对象：
  - spatial-only baseline：RR/BestFit/DRF/P2C base；
  - baseline + Stage B：RR+ps/BestFit+ps/DRF+ps/P2C+ps；
  - Stage A only：stps-spatial；
  - full STPS：stps-spatial+ps。
- 指标分组：
  - Stage B 效果：cong. ratio、cong. wait、mean offset、p99；
  - Stage A 效果：CV/JFI/LIF 或 max/min；
  - full tradeoff：congestion 下降但不把 CV 推到 baseline+ps 那么高。

**预期效果**：

- Stage B 对所有 spatial policy 都能显著降低 congestion。
- 单独 Stage B 可能恶化 CV，这是 de-synchronization 在 binding cap 下的指标反转。
- Stage A 单独应保持或改善 spatial distribution，但不能解决 congestion。
- Full STPS 不追求单列最优，而是在 congestion、CV、p99、throughput 之间形成 deployable compromise。

**表格/文字改法**：

- Caption 不要说 full STPS congestion drop 小是“Stage A 吸收了 slack”，这个解释容易被质疑。
  更稳的说法：baseline+ps 是激进 temporal smoothing，代价是 CV/p99/throughput；full STPS 是受控折中。
- 增加一列或脚注标明 `objective`：base / +ps / Stage A / full，帮助读者按行组阅读。
- 如果数据允许，加入 LIF，让“Stage A 控 worst-card tail”更直观。

**成功判定**：读者应理解：Stage B 买 NoC，Stage A 买 placement discipline，full STPS 买可部署折中。

---

### A.4 `tab:q1_util`：Stage A 的 utilisation sweep，要回答“什么时候拓扑信号有用”

**当前作用**：解释 Stage A 不是全负载区间都赢，而是在有空间可调、且拓扑信号有效时有用。

**应该比较什么**：

- 比较对象：RR、BestFit、DRF、P2C、stps-A。
- 横轴：utilisation 从低到高，例如 0.30/0.50/0.70/0.85/0.95。
- 指标：CV 为主；最好补 JFI 或 LIF 作为稳健性验证。

**预期效果**：

- 低 utilisation：BestFit/DRF 可能更好，因为空闲资源多，简单容量信号足够。
- 中高 utilisation：Stage A 接近 leader band，说明 topology-aware score 开始有意义。
- 接近饱和：所有策略收敛，因为没有 placement freedom。

**表格/文字改法**：

- 不要说 Stage A 全面优于 baseline；要说它有 operating window。
- 如果结果显示 Stage A 只是在 0.85/0.95 接近而非超过 baseline，也要诚实写成“converges into leader band”。

**成功判定**：该表回答 reviewer 的问题：“为什么需要 Stage A，而不是 BestFit/DRF 就够了？”

---

### A.5 `tab:q1_mix`：fingerprint-composition sweep，要回答“什么时候 fingerprint 有预测力”

**当前作用**：说明 Stage A 依赖 workload fingerprint 的可预测性；steady-flat 时好，bursty 占比高时弱。

**应该比较什么**：

- 比较对象：RR、BestFit、DRF、P2C、stps-A。
- 横轴：Flat/Bursty 组成比例，如 100/0 到 0/100。
- 指标：CV；建议补 LIF 或 max/min，避免只靠平均离散度。

**预期效果**：

- Baseline 不读取 fingerprint，因此各比例下基本不变。
- Stage A 随 workload composition 改变；steady-flat 占比高时更好。
- Bursty 占比高时 Stage A 可能变差，因为短周期 burst 填满 forecast horizon，topology score 不能分辨 quiet card。

**表格/文字改法**：

- 该表不要承担 full STPS 的结论，只解释 Stage A 的 boundary。
- Caption 中明确：这是 Stage A-only、无 Stage B、无 cap-binding 的 isolated test。

**成功判定**：读者应理解：fingerprint 不是魔法，只有在 fingerprint 对未来 traffic 有预测力时才帮助 placement。

---

### A.6 `tab:q2_dmax`：offset budget sweep，当前太像“定性总结”，建议改成数值表

**当前问题**：表里是方向性描述，不是实验结果表。ATC reviewer 可能认为这是文字总结，
不是可复现实验数据。

**应该比较什么**：

- 横轴：`D_max = 0, 2, 4, 8, 16`。其中 `0` 是 no phase-shift baseline。
- 至少报告 full STPS；最好再报告一个代表性 baseline+ps（如 P2C+ps 或 BestFit+ps）。
- 指标：cong. ratio、cong. wait、CV、LIF/max-min、throughput、p99、mean offset。

**预期效果**：

- 从 0 到 2：congestion 大幅下降，是主要收益。
- 2 到 8：balance/tail-skew 可能继续改善但收益递减。
- 8 到 16：p99 和 mean offset 单调增大，throughput 继续受损或恢复有限。
- 默认 `D_max=2` 应被解释为最小有效预算，而不是性能最优预算。

**表格/文字改法**：

- 把当前方向表改成真实数值表，或保留方向表但新增一个数值表/appendix 表。
- Caption 写清楚：`D_max` 是 policy knob，决定 congestion relief 与 tail-delay cost 的 tradeoff。

**成功判定**：读者能看到为什么选 `D_max=2`，而不是觉得参数是拍脑袋定的。

---

### A.7 `tab:q2_regime_profile`：cap-binding profile，是解释实验分区的“地图”

**当前作用**：定义 A/B/C 三个 regime，让后面 CV 反转不显得偶然。

**应该比较什么**：

- Regime A：binding，baseline congestion 高。
- Regime B：non-binding，baseline congestion 为 0。
- Regime C：lightly binding，baseline congestion 低但非零。
- 指标：baseline congestion、baseline CV、baseline p99、offset-infeasible rate。

**预期效果**：

- A：cap 本身已经 clipping peaks，served-load 看起来较平，但 queue 很重。
- B：没有 queue，CV 反映真实 burst 分布。
- C：介于二者之间，是 STPS 更合理的 operating point。

**表格/文字改法**：

- 该表应放在 RQ3 主文或 appendix 开头，用来定义后续所有 regime 结果。
- 明确 A 不是 STPS 的 sweet spot，而是 under-provisioned stress case。

**成功判定**：读者先理解工作点差异，再看后续 metric sign flip。

---

### A.8 `tab:q2_regime_cv`：解释 CV 为什么会反转，保护 Table 1 的负载均衡叙事

**当前作用**：证明 CV 的方向由 cap-binding regime 决定，不是 STPS 特有失败。

**应该比较什么**：

- 比较对象：RR、BestFit/DRF、P2C、stps-spatial，各自 off vs on phase-shift。
- 到达模式：Poisson、Bursty。
- 三个 regime：A/B/C。
- 指标：`Delta CV = (off - on) / off`，正值表示 phase-shift 改善 CV。

**预期效果**：

- A 中所有 policy 都为负：phase-shift 提高 CV。
- B/C 中多数或所有 policy 为正：phase-shift 降低 CV。
- 这说明 Table 1 的 CV 不优不是 STPS 独有问题，而是 binding cap 下 served-load metric 的定义问题。

**表格/文字改法**：

- 正文要把它和 Table 1 连起来：Table 1 所在 cap 是 binding，因此 CV 不是唯一 load-balance 读数。
- 不要用该表声称 STPS 在 A 下改善负载均衡；应该说 A 下没有 scheduler 改善 CV。

**成功判定**：读者不会再用 Table 1 的 CV/JFI 直接否定 STPS，而会理解指标和 regime 的关系。

---

### A.9 `tab:q2_regime_multi`：方向总结表可以保留，但必须服务于机制解释

**当前问题**：方向表信息密度高，但没有具体数值，容易显得像作者主观归纳。

**应该比较什么**：

- 三个 regime 下，phase-shift 对 CV、JFI、LIF、max/min、congestion、throughput、p99 的方向。
- 该表应作为 `tab:q2_regime_cv` 的 companion，而不是单独支撑结论。

**预期效果**：

- congestion：A/C 下降，B 无变化。
- CV/JFI/LIF：A 方向变差，B/C 方向变好。
- p99：A 中 baseline queue 被消掉时可能下降；B/C 中 offset 成本主导，可能上升。
- throughput cost：由 offset budget 和 run window 决定，不完全由 cap 决定。

**表格/文字改法**：

- 保留方向表，但在 appendix 中给出完整数值或 CSV 路径。
- 正文只引用方向结论，不要让它替代主实验数据。

**成功判定**：该表帮助 reviewer 快速理解 regime-dependent metric behavior，而不是制造“缺少数值”的疑问。

---

### A.10 全表统一修改清单

- [ ] 将所有 caption 中的 “load-balancing gains” 改为更精确的说法：
      `tail-skew containment`、`worst-card control`、或 `congestion-balance tradeoff`。
- [ ] 在 Metrics 段明确区分：
      `CV/JFI = average dispersion`，`LIF/max-min = tail skew`。
- [ ] 主表不要声称 STPS 是 best load balancer；只声称 STPS 是 best congestion reducer，
      并在 16-card 上控制 worst-card tail。
- [ ] 所有表格尽量使用同一组 seeds、tasks、steps、BW、D_max，不能同一行混用不同来源 CSV。
- [ ] 对方向性表格（尤其 `tab:q2_dmax`, `tab:q2_regime_multi`）补充数值表或 CSV 出处。
- [ ] 对 `max/min` 做指标替换评估：优先考虑 `LIF=max/mean`，因为它更稳定地表达 worst-card risk。
- [ ] 每个 RQ 段落第一句话先说“这张表回答什么问题”，不要先报数字。
- [ ] 每张表后面的正文按固定顺序写：主比较结果 → 代价 → 不能过度解读的边界。

---

## 0. 先决：核实现有 `tab:q0_main` 数值的真实出处 ⚠️ 阻塞项

**问题**：现有 RQ1 主表 (`SNN schedule/article.tex`, `tab:q0_main`) 的 `max/min`
列与仓库里**任何** CSV 都对不上。例如表里 4-card Poisson `RR max/min = 7.42`，
但 `data/q0/arrival_summary.csv` 同 cell 是 `5.01`；其余 CSV 也无一行能同时
匹配该表的 `CV` 与 `max/min`。

**影响**：在出处确认前，不能从 `arrival_summary.csv` 取 p99 拼进主表 —— 会造成
"同一行的不同列来自不同 run" 的不一致，正是 reviewer 会抓的。

**待办**：

- [ ] 找出/重新生成 `tab:q0_main` 当前数值的权威来源 CSV（哪个脚本、哪次 run、
      什么 tasks/seed 配置）。

- [ ] 确认来源后，要么 (a) 以该来源统一补 p99 列，要么 (b) 以
      `data/q0/arrival_summary.csv` 为唯一权威源**整表重写**（CV / JFI /
      max/min / throughput / cong-ratio / cong-wait + 新增 p99），保证同源。

> 备注：`arrival_summary.csv` 的 `card_cv / throughput / cong` 列**确实**与论文表
> 吻合（如 Poisson RR CV 0.267、STPS mixed cong 0.245），只有 `max/min` 列
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
