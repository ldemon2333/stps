# STPS 论文实验仿真代码

本仓库是论文 [STPS: Spatio-Temporal Proactive Scheduling for Spiking Neural Networks on Compute-in-Memory Clusters](article/article.tex) 的实验代码。核心是离散时间仿真器：从 SNN 工作负载指纹生成任务，在多张 CIM 卡上比较 STPS、RR、BestFit、DRF、P2C 的放置与相位偏移策略，记录负载均衡、吞吐量和片上网络（NoC）拥塞指标。这里模拟调度行为，不运行真实 CIM 硬件。

论文以 [article/article.tex](article/article.tex) 为源文件。实验 CSV、指纹文件和生成图只保存在本地，不纳入 Git。论文描述的实验规模与快速运行示例不同，具体参数以相应实验脚本和文档为准。

## 目录

| 路径 | 内容 |
| --- | --- |
| [article/](article/) | 论文 LaTeX、`tables/` 表格和实验记录；`picture/` 为本地图文件 |
| [main.py](main.py)、[Makefile](Makefile) | 单次仿真入口和常用命令 |
| [fingerprint/](fingerprint/) | SNN 轨迹提取、合成与存取指纹 |
| [schedule/](schedule/) | STPS、消融版本和对照调度器 |
| [simulation/](simulation/)、[util/](util/) | 仿真循环、卡片与任务模型、指标 |
| [script/](script/) | Q0/Q1/Q2、拥塞、开销、鲁棒性、扩展性实验与绘图 |
| [npz/](npz/) | 本地 `.npz` 指纹输出；格式见 [npz/README.md](npz/README.md) |
| `data/`、`figures/`、`log/` | 本地 CSV、图表与运行日志，均被 Git 忽略 |
| [docs/](docs/) | 方法、指标和各组实验的补充说明 |
| [tests/](tests/) | 仿真器与指纹流水线测试 |
| `extras/`、`archive/` | 本地资料，Git 忽略 |

旧路径 `SNN schedule/` 是指向 `article/` 的兼容符号链接。现有部分绘图脚本仍使用旧路径写入 `picture/`，因此运行这些脚本时会直接更新 `article/picture/`。

## 环境与快速运行

项目使用 Python。仓库现有环境可用 `/root/miniconda3/envs/snn/bin/python`；在其他机器上可自行创建环境。`requirements.txt` 包含模型指纹提取及其他实验所需的扩展依赖，基本仿真主要使用 NumPy。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make PYTHON=python fingerprints
python main.py --list-schedulers
python main.py --scheduler stps --cards 4 --tasks 128 --steps 128 --seed 21 --arrival-mode bursty --fingerprint-dir npz --bw-max 5e6 --d-max 16 --horizon 64
```

命令在 `data/` 写 CSV，在 `log/` 写运行日志。`main.py` 的默认值与 Makefile 的默认值不同；复现实验时应显式传参，或直接使用对应脚本。

## 论文实验入口

| 实验 | 入口 | 主要输出 |
| --- | --- | --- |
| 端到端对比与 16 卡扩展（Q0） | `python script/q0_run.py main`、`python script/q0_run.py scale16` | `data/q0/`、`figures/q0/` |
| 空间负载均衡（Q1） | `python script/q1_run.py main` | `data/q1/`、`figures/q1/` |
| 相位偏移与消融（Q2） | `python script/q2_run.py main4` | `data/q2/`、`figures/q2/` |
| 论文表格核对 | `python script/gen_tables.py` | `article/experiment_regen.md` |
| 其他实验 | `script/exp1_motivation.py` 至 `script/exp4_scalability.py` | `data/`、`article/picture/` |

完整实验可能运行较久并覆盖同名结果文件。运行前可先看 [article/experiment.md](article/experiment.md)、[article/experiment_regen.md](article/experiment_regen.md) 及 [docs/](docs/) 对参数、数据来源和指标口径的记录。这些记录引用的 CSV 和图未随仓库发布；论文使用的真实模型指纹也需另行准备。

常用 Make 目标：`make list-schedulers`、`make fingerprints`、`make stps`、`make compare-stps`、`make q0`、`make q1`。Q2 请使用上表的 `python script/q2_run.py main4`；当前 Makefile 的 `q2-scale16` 传入脚本未支持的 `scale16` 参数。Makefile 默认使用 `/root/miniconda3/envs/snn/bin/python`，可用 `PYTHON=python make stps` 覆盖。`make clean` 会删除 `data/` 顶层 CSV 和 `figures/` 顶层文件，使用前先检查其中的数据。

## 论文与验证

在 `article/` 内编译论文，保持 `picture/...` 与 `references.bib` 的相对路径。论文使用的六张图已被 Git 忽略；首次克隆后须先用相应绘图脚本生成或从本地备份恢复 `article/picture/`，才能完整编译：

```bash
cd article
latexmk -pdf -interaction=nonstopmode -outdir=build article.tex
```

运行代码测试：

```bash
python -m pytest tests/
```

设计到代码的对应关系见 [docs/stps.md](docs/stps.md) 和 [docs/fingerprint.md](docs/fingerprint.md)；代理与维护约定见 [CLAUDE.md](CLAUDE.md)。
