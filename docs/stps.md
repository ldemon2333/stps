# STPS：论文与仿真器对应关系

论文源文件是 [article/article.tex](../article/article.tex)。本文档说明当前代码中的实现边界；论文中的实验参数、图表和结论请继续以论文、相应实验脚本和 CSV 核对。

## 离线指纹

`fingerprint/` 从 DTDG / SNN 校准轨迹提取模型指纹，也能生成合成指纹。当前 [`Fingerprint`](../fingerprint/__init__.py) 包含平均注入轨迹 `mean_injection_trace`、全局突发性 `global_burstiness`、热点中心性 `max_centrality`、平均活跃连通分量 `mean_components`、时间窗 `T` 及放置资源字段。`traffic_sequence` 是平均注入轨迹的兼容属性；如果有单样本实测注入轨迹，`effective_traffic_trace()` 会优先返回该轨迹。完整格式见 [fingerprint.md](fingerprint.md) 和 [npz/README.md](../npz/README.md)。

```bash
python -m fingerprint.cli --synthetic --T 64 --beta 4 --K 2 --out npz/synthetic_bursty.npz
```

## 在线调度

[`schedule/stps.py`](../schedule/stps.py) 的 `STPSScheduler.select_card_for_task()` 主要完成两项在线决策：

1. **宏观卡片派发**：从可容纳任务的卡中，按空闲连续块与指纹连通分量的匹配程度、卡片与任务突发性评分。实验可选择额外负载、积压惩罚及候选裁剪。
2. **时间相位偏移**：[`schedule/phase_shift.py`](../schedule/phase_shift.py) 搜索 `0..D_max` 的偏移，选取预测峰值最低的卡与偏移。预测峰值超过 `bw_max` 时仍可放置，超额流量由仿真器的 NoC 队列处理。

调度器还调用 [`schedule/hotspot_split.py`](../schedule/hotspot_split.py)，把超过中心性阈值的热点索引记到任务 `split_plan`。当前仿真器没有执行物理拆分映射，不能把该记录解释为真实拆分收益。

`stps-spatial` 关闭时间偏移，`stps-temporal` 关闭宏观派发和热点标记。普通 RR、BestFit、DRF、P2C 及相位包装策略也可通过 `python main.py --list-schedulers` 查看。

## 仿真与结果

[`simulation/engine.py`](../simulation/engine.py) 从 `--fingerprint-dir` 索引 `.npz`，按任务编号选指纹，执行到达、放置、时间步推进、NoC 带宽与待处理队列、任务完成和指标记录。`start_offset` 控制任务何时开始产生流量。指标口径见 [metrics.md](metrics.md)。

```bash
python main.py --list-schedulers
python main.py --scheduler stps --cards 4 --tasks 128 --steps 128 --seed 21 --arrival-mode bursty --fingerprint-dir npz --bw-max 5e6 --d-max 16 --horizon 64
```

论文实验使用 [`script/`](../script/) 中的批量入口；本地汇总数据写入 `data/`，图表写入 `figures/` 和 `article/picture/`，这些结果文件不纳入 Git。`main.py` 与 Makefile 的便利默认值均不能替代论文实验配置。
