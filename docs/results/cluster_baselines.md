# 四卡baseline验收：Poisson与bursty

日期：2026-10-07（Asia/Shanghai）。当前在线实现RR、WorstFit、DRU、BestFit、P2C-Mean；本页是人工小图单种子演示，算法定义见[baselines.md](../baselines.md)。

## 1. 固定输入与参数

- 4张独立卡，每卡4×4 mesh、16核，每核64神经元，内存8 MB；无卡间通信。
- 每物理Tick固定K=8 cycle，Router/源NI/目的NI各深度2，消费周期1；卡内未Rx包顺延并阻止该卡下一逻辑步，卡间互不阻塞。
- 共用row-major-free-v1 mapper：MicroPopulation索引依次对应当前最小空闲核ID，不作mapping搜索。不同放置历史会得到不同核位，路径影响保留在记录中。
- 每模式24任务，四种workload各6次，8逻辑步、4/4/6/8个MicroPopulation。完整工作量44,880 SOP、978跨核flit和60本地flit。
- Poisson指数间隔分箱，rate=1.2 tasks/Tick、seed23；bursty在Tick1/7/13/19各6任务。不同策略直接读取同一显式到达文件，不重新生成随机输入。
- P2C独立seed17，声明均值评分预算500 SOP/40端点事件，所有基线主动相位d=0；固定稳态窗口[5,40]，max_ticks=400。

任务profile和均值均为人工已知输入；策略只收到均值与容量，不读取未来逐步发包、队列或完成时刻。这不验证真实数据预测准确性。计算只有SOP记账，执行延长来自通信/屏障。

## 2. 命令与产物

```bash
make cluster-validate
python script/validate_cluster.py --output-root data/cluster
python main.py --cluster-scenario examples/cluster/poisson.json --policy P2C-Mean --output-dir data/new_cluster_run --trace
```

本机产物：[十组比较索引](../../data/cluster/20261007T073917_030626Z/index.html)、[完整comparison.csv](../../data/cluster/20261007T073917_030626Z/comparison.csv)。每运行有顶层report、manifest、合并core/task/card/step/queue/link/events、decisions、card_resources、cluster_tick和balance_windows；cards/card_i含独立明细及报告。工作负载路径/hash和每任务实际mapping均保留，可核对在线分配。

## 3. 完整执行结果

全部10组均完成24/24、无拒绝，跨核生成=Tx=Rx=978，SOP=44,880、本地=60；守恒残差全0。端到端从arrival到completion计量，含资源和卡轮次边界等待，均值/p95只对完整任务计算。

| 到达 | 策略 | 集群完工Tick | 平均端到端Tick | p95端到端Tick | 平均执行Tick | 平均通信延长Tick | Router等待flit-cycle |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Poisson | RR | 42 | 17.375 | 23 | 14.250 | 6.250 | 1888 |
| Poisson | WorstFit | 44 | 17.292 | 28 | 14.125 | 6.125 | 1913 |
| Poisson | DRU | 44 | 17.042 | 28 | 14.125 | 6.125 | 1912 |
| Poisson | BestFit | 41 | 18.667 | 25 | 14.583 | 6.583 | 1936 |
| Poisson | P2C-Mean | 45 | 17.000 | 26 | 13.750 | 5.750 | 1878 |
| Bursty | RR | 45 | 16.125 | 25 | 13.708 | 5.708 | 1883 |
| Bursty | WorstFit | 45 | 16.250 | 27 | 13.708 | 5.708 | 1906 |
| Bursty | DRU | 45 | 16.792 | 27 | 14.042 | 6.042 | 1871 |
| Bursty | BestFit | 40 | 15.833 | 22 | 13.625 | 5.625 | 1953 |
| Bursty | P2C-Mean | 40 | 15.875 | 22 | 13.667 | 5.667 | 1942 |

演示可见不同决策产生不同资源排队和共卡逻辑步干扰。Poisson的P2C平均端到端较低但整体最后完成较晚；Bursty的DRU Router等待较低但任务均值较高。因此不能只看一项指标排总优劣。

## 4. 独立计算与通信均衡

下表为全程[1,各自完工Tick]累计CV，通信按卡内Tx+Rx，计算按SOP；不是逐TickCV平均。

| 到达 | 策略 | 计算CV | 通信CV | 固定[5,40]通信CV |
| --- | --- | ---: | ---: | ---: |
| Poisson | RR | 0.354 | 0.191 | 0.180 |
| Poisson | WorstFit | 0.170 | 0.161 | 0.134 |
| Poisson | DRU | 0.352 | 0.224 | 0.207 |
| Poisson | BestFit | 0.220 | 0.205 | 0.190 |
| Poisson | P2C-Mean | 0.258 | 0.151 | 0.125 |
| Bursty | RR | 0.323 | 0.180 | 0.166 |
| Bursty | WorstFit | 0.192 | 0.248 | 0.222 |
| Bursty | DRU | 0.132 | 0.251 | 0.238 |
| Bursty | BestFit | 0.222 | 0.238 | 0.225 |
| Bursty | P2C-Mean | 0.237 | 0.219 | 0.215 |

JFI/LIF、各卡原始负载、比率分子/分母和zero标记保留在balance_windows.json与报告。空闲卡也进入分母。所有演示稳态窗口都已完整观察；全程窗口各运行长度不同，因此同时给固定窗和完整工作量。没有加入STPS、MeanLoad-Dual或相位搜索，不以这些数值预言未来STPS效果。

## 5. 验证与限制

该产物生成时，两个Python环境的阶段性完整测试均通过169项；本次文档审计时，当前完整套件在两个环境均为 **274 passed**。验证覆盖五策略精确打分/平局、RR指针、P2C独立随机、Poisson/bursty可重复生成、多卡独立屏障、实际释放、预留未启动、arrival锚定端到端、核复用旧sink反压、单卡在线与固定场景逐事件等价、截断/拒绝/尚未到达、保存场景重放、core/card/cluster求和、窗口补零与完整标志、精确分位、HTML安全和空卡零流量。

十组执行已断言相同完整工作量、无包丢失/重复。该场景仅4卡、小图、一个调度seed，无大模型/真实数据/硅片时延证据。row-major共用不消除物理路径敏感性。当前多种子、同一二进制的多维指标对比见[STPS热点结果](stps_hotspots.md)；本页不用于判断STPS优劣。
