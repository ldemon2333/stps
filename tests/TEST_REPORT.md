# STPS 功能测试报告

生成时间：2026-05-05  
测试对象：`/root/v3` 当前 STPS 重构实现，包括离线指纹提取、在线 STPS 调度、CLI 与仿真引擎集成。

## 1. 测试目标

从三个视角验证已有功能：

- 用户视角：命令行入口可用，调度器可枚举，小规模仿真能生成负载与 summary CSV。
- 开发视角：核心 API 的输入输出稳定，包括 `Fingerprint` I/O、中心性计算、DTDG 指纹提取、阶段调度函数。
- 极端边界视角：空图、非法张量、无可行带宽 offset、卡资源不足、缺失指纹目录、STPS phase delay 等场景行为明确。

## 2. 新增测试文件

| 文件 | 覆盖重点 |
|---|---|
| `tests/conftest.py` | 固定随机种子，确保 pytest 可从仓库根目录导入本地包。 |
| `tests/test_fingerprint_pipeline.py` | 指纹保存/加载、中心性、空图、提取器 burst 检测、非法输入、合成指纹、`fingerprint.cli`。 |
| `tests/test_stps_scheduler.py` | `phase_shift`、`hotspot_split`、完整 STPS、spatial/temporal 消融、forecast 滚动、容量与带宽边界。 |
| `tests/test_simulation_integration.py` | `main.py --list-schedulers`、BestFit/STPS 小规模仿真、缺失指纹目录 fallback、phase offset 执行门控、非法调度器错误提示。 |

## 3. 已执行验证

```bash
pytest -q
```

结果：`22 passed in 0.53s`

测试数量：22 个 pytest 用例。

## 4. 发现并修复的问题

### 问题：汇点型 DAG 的 in-eigenvector centrality 退化为均匀分布

复现场景：多个源节点只指向同一个 receiver hub。原实现对 `W.T @ c` 做迭代时，在 sink-heavy / DAG 快照中会进入零向量分支，最终返回均匀向量，热点节点无法被识别。

修复位置：`fingerprint/centrality.py`

修复方式：

- 空图仍直接返回均匀向量。
- 非空图在迭代矩阵上加入单位自环，使 receiver hub 的中心性质量不会在 DAG 中被抹掉。

对应测试：`test_in_eigenvector_centrality_concentrates_on_receiver_hub`、`test_centrality_returns_finite_uniform_vector_for_empty_graph`。

## 5. 覆盖矩阵

| 功能模块 | 用户路径 | 开发接口 | 边界场景 |
|---|---:|---:|---:|
| 指纹 I/O | CLI 生成 `.npz` 后加载 | `save_fingerprint` / `load_fingerprint` round-trip | metadata、dtype、schema 保持 |
| 中心性 | 通过提取器间接覆盖 | hub 图中心性 | 空图有限值、归一化 |
| 指纹提取器 | burst workload 识别 | `extract_fingerprint_from_W` | 非 `(T,V,V)` / 非方阵输入 |
| STPS Stage 2 | STPS 小规模仿真 | `find_optimal_offset` | 无可行 offset fallback、BW 拒绝 |
| STPS Stage 3 | STPS 任务 split plan | `split_population` | 空 centrality vector |
| STPS 调度器 | `stps` / ablation 小规模可运行 | `select_card_for_task` | 资源不足、forecast 截断与滚动 |
| 仿真引擎 | BestFit/STPS 输出 CSV | `run_simulation` / `SimulationEngine` | 缺失指纹目录 fallback、phase delay gate |
| CLI | `main.py --list-schedulers` | 错误退出码 | 非法 scheduler 提示 |

## 6. 尚未覆盖的风险

- 真实 SpikingJelly / torch 模型 trace 没有在本轮执行；当前测试只覆盖无重依赖的 synthetic 指纹路径。
- 大规模 `make compare-stps` 没有执行；本轮采用轻量端到端 smoke，适合快速回归。
- STPS Stage 3 当前只记录 `split_plan`，物理映射仍未真正拆分 population；测试按当前实现验证“计划被记录”。
- `SimulationEngine` 支持 `ticks_per_step`，但 `main.py` 当前没有公开 `--ticks-per-step` CLI 参数；测试通过 Python API 覆盖了该参数。
- `README.md` 仍保留部分旧 GLaSS 文档内容，与当前 STPS Makefile/CLAUDE 指南不完全一致；本轮未改文档主线。

## 7. 建议后续验证

```bash
python -m fingerprint.cli --synthetic --T 32 --beta 4 --K 2 --out npz/test_bursty.npz
python main.py --scheduler stps --cards 2 --tasks 8 --steps 8 --arrival-mode bursty --fingerprint-dir npz --horizon 32 --d-max 4 --bw-max 1e6
make compare-stps TASKS=32 STEPS=32 CARDS=4
```

上述命令用于补充人工 smoke 与较完整的调度器对比验证。
