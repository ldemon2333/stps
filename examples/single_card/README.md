# 单卡固定场景

三个工作负载各含 2 个 MicroPopulation、8 个逻辑 Tick，大小均为 64，state_size_mb 均为 1。源数据明确标为 synthetic，SOP 与发包独立设置。物理核按 core_id=y×4+x 编号。

| Task | mapping | 每逻辑步跨核 flit（s=0…7） | 总 SOP |
| --- | --- | --- | ---: |
| bursty | [0,7] | [0,1,0,0,8,0,0,2] | 692 |
| fan_in | [1,3] | [0,3,0,0,6,0,1,0] | 89 |
| compute_heavy | [4,6] | [0,0,0,0,1,0,0,0] | 2950 |

compute_heavy 另在 s=3 有 2 个本地自环 flit。全卡远程 22 flit、本地 2 flit、3731 SOP。bursty 与 fan_in 的 XY 路径共享上排链路，会出现同输出争用；fan_in 是此测试流的名称，任务自身只有一条活跃跨核边，不表示真实模型拓扑。

- positive.json：K=40，缓冲深度 2，全部任务 start_tick=1；预期成功。
- carry_over.json：K=6，其它同上；跨 Tick 继续，预期实际执行12 Tick。
- manual_stagger.json：K=40，fan_in 从 Tick 3、compute_heavy 从 Tick 2 开始；预期成功，起点是场景输入。
- buffer1 / carry_over_buffer1 由脚本生成，把三类缓冲都改为1，K分别40/6。
- truncated.json：K=6、max_ticks=2；停止于上限，保留未完成包和步。

```bash
python main.py --scenario examples/single_card/positive.json --output-dir data/my_run --trace
python script/validate_single_card.py --output-root data/phase1
```

查看 report.html 中各 Task 的计算与通信曲线、MicroPopulation 热图和 Router 等待，再用 events.csv 定位具体争用位置。字段与统计边界见 [实验说明](../../docs/experiment.md)。修改 mapping 会改变路径与拥塞；更改K会改变一个物理Tick的传输预算，未Rx包跨Tick继续，逻辑步变长；实际时间见step_timing和task_summary。
