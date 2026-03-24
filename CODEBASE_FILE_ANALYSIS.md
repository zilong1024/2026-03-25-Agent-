# quant_react_interview 代码库全面分析（逐文件）

## 1. 先看整体：这个仓库是两层系统

这个项目由两层组成：

1. `agent/`：规划层  
把自然语言需求转成结构化 pipeline（步骤 + 依赖）。

2. `engine/`：执行层  
按 pipeline 执行每个 step，处理依赖、引用和输出。

一句话串起来：  
`Prompt -> Agent 组装 Pipeline -> Engine 执行 Pipeline -> 产出研究结果`

---

## 2. 建议的阅读与验证命令

## 2.1 看文件清单

```bash
cd /home/h3c/zilong1024/260324-Agent笔试题/quant_react_interview
rg --files | sort
```

## 2.2 跑公开测试

```bash
python3 tests/run_tests.py
```

## 2.3 跑端到端 smoke（Agent + Runtime）

```bash
python3 test.py
```

如果要接真实模型（例如 DeepSeek OpenAI-Compatible）：

```bash
OPENAI_API_KEY=你的Key \
OPENAI_BASE_URL=https://api.deepseek.com \
REACT_MODEL=deepseek-reasoner \
python3 test.py
```

## 2.4 快速定位关键实现

```bash
sed -n '1,240p' agent/react_loop.py
sed -n '1,240p' agent/tools.py
sed -n '1,260p' engine/core/scheduler.py
sed -n '1,260p' engine/nodes/data/market_bars.py
sed -n '1,260p' engine/nodes/ai/research_chat.py
```

---

## 3. 执行链路（理解文件关系最重要）

1. `test.py` 调 `ReactLoopAgent.run(prompt)`  
2. `agent/react_loop.py` 通过工具调用构建 draft pipeline  
3. 工具实现在 `agent/tools.py`，底层调用 `engine/core/builder.py`  
4. `builder` 会即时执行单步（校验 config 是否可跑）  
5. 最终 `PipelineEngine`（`engine/core/engine.py`）加载 pipeline  
6. `engine/dsl/*` 负责解析和校验 pipeline schema  
7. `engine/core/scheduler.py` 根据依赖拓扑执行 steps  
8. `engine/core/registry.py` 把 `kind` 映射到具体节点实现（`engine/nodes/*`）  
9. 结果输出到 runtime context 并汇总返回

---

## 4. 逐文件说明（每个文件）

说明：以下按“当前仓库代码”逐文件列出。  
其中 `GITHUB_SYNC.md`、`WORKLOG.md`、`PROJECT_BACKGROUND_AND_REQUIREMENTS.md` 属于协作/说明文档，不是原始笔试题核心代码。

| 文件 | 作用 | 关键点/你会在什么场景改它 |
| --- | --- | --- |
| `.gitignore` | 忽略缓存与虚拟环境目录 | 防止 `__pycache__/`、`venv/` 被提交。 |
| `README.md` | 项目总览与运行说明 | 第一次进仓库先读，理解“规划层+执行层”双结构。 |
| `TASKS.md` | 笔试主要求（权威任务清单） | 真正的题目定义：3 个 workstream +评审点。 |
| `CANDIDATE_README.md` | 候选人快速上手引导 | 给阅读顺序和最短执行命令。 |
| `RUBRIC.md` | 评估标准 | 面试官关注点：loop 稳定性、工具设计、真实接入。 |
| `requirements.txt` | Python 依赖清单 | 包含 `pydantic/yaml/openai/baostock/httpx`。 |
| `__init__.py` | 根包声明 | 让仓库可作为 Python 包被导入。 |
| `test.py` | 端到端 smoke 脚本 | 一次跑通“Prompt -> Pipeline -> 执行结果”；调试最常用。 |
| `examples/momentum_pipeline.yaml` | 参考 pipeline 样例 | 不走 Agent，直接给执行层喂标准流程。 |
| `datasets/daily_bars.json` | 离线示例行情数据 | `market_bars` fallback 数据源（AAPL/MSFT/NVDA）。 |
| `tests/run_tests.py` | 公开回归测试入口 | 跑 `tests/public/cases/*.yaml` 并比对输出。 |
| `tests/__init__.py` | tests 包声明 | 语义占位，保持包结构一致。 |
| `tests/public/cases/01_manual.yaml` | 用例1：manual trigger | 验证 `trigger.manual` 能按配置原样输出。 |
| `tests/public/cases/02_bars.yaml` | 用例2：market bars | 验证数据节点输出结构（至少拿到 symbol 分组）。 |
| `agent/__init__.py` | agent 包声明 | 语义占位。 |
| `agent/catalog.py` | 节点元数据目录 | 向模型说明每种 step 的必填字段、示例、字段解释。 |
| `agent/tools.py` | Agent 工具层与校验层 | 定义 `add/update/connect/get_*`；执行参数校验、step 即时试跑。 |
| `agent/react_loop.py` | 手写 ReAct 控制循环 | 最核心 Agent 逻辑：消息序列、tool call、恢复策略、终止条件。 |
| `engine/__init__.py` | engine 包声明 | 语义占位。 |
| `engine/core/__init__.py` | core 子包声明 | 语义占位。 |
| `engine/core/engine.py` | 执行层入口协调器 | 将输入（dict/yaml path）解析后交给 scheduler 执行。 |
| `engine/core/registry.py` | 节点注册中心 | `kind -> step class` 映射；新增节点要在这里注册。 |
| `engine/core/context.py` | 运行时上下文与引用解析 | 管理 step 输出，支持 `$step['field']` 引用语法。 |
| `engine/core/builder.py` | Agent 草稿构建器 | Agent 构建 pipeline 时的状态容器；支持 add/update/connect/单步执行。 |
| `engine/core/scheduler.py` | pipeline 调度执行器 | 处理依赖图、拓扑执行、config 引用物化、汇总 outputs。 |
| `engine/dsl/__init__.py` | DSL 子包声明 | 语义占位。 |
| `engine/dsl/models.py` | pipeline/step 的 Pydantic 模型 | 定义 schema、默认值、字段校验（如 steps 至少 1 条）。 |
| `engine/dsl/parser.py` | YAML/Dict 解析器 | 兼容 `pipeline:` 包裹结构并调用 validator。 |
| `engine/dsl/validator.py` | 结构校验器 | 校验 step id 唯一、边必须指向已存在 step。 |
| `engine/nodes/__init__.py` | nodes 子包声明 | 语义占位。 |
| `engine/nodes/base.py` | step 抽象基类 | 统一 `execute/run` 接口。 |
| `engine/nodes/triggers/__init__.py` | triggers 子包声明 | 语义占位。 |
| `engine/nodes/triggers/manual.py` | 手动输入触发节点 | 将 config 原样作为输出，常用于 pipeline 起点。 |
| `engine/nodes/data/__init__.py` | data 子包声明 | 语义占位。 |
| `engine/nodes/data/market_bars.py` | 市场数据节点 | 真实查询 BaoStock；支持 symbol 规范化、lookback、错误聚合、fallback。 |
| `engine/nodes/factors/__init__.py` | factors 子包声明 | 语义占位。 |
| `engine/nodes/factors/momentum.py` | 动量因子节点 | 从 grouped bars 计算每个 symbol 的 momentum score。 |
| `engine/nodes/factors/rank.py` | 排序因子节点 | 按 score map 排序，输出 `ordered` 和 `top`。 |
| `engine/nodes/ai/__init__.py` | ai 子包声明 | 语义占位。 |
| `engine/nodes/ai/research_chat.py` | 文本解释节点 | 调 OpenAI-Compatible chat completions，返回内容/模型/usage。 |
| `engine/nodes/output/__init__.py` | output 子包声明 | 语义占位。 |
| `engine/nodes/output/report.py` | 报告汇总节点 | 返回 sections 占位结构（扩展点）。 |
| `GITHUB_SYNC.md` | GitHub 同步操作文档 | 团队协作文档：如何 remote add / commit / push。 |
| `WORKLOG.md` | 工作过程日志 | 记录每个阶段的问题、方法和结果（过程追踪）。 |
| `PROJECT_BACKGROUND_AND_REQUIREMENTS.md` | 面向非 Agent 的背景说明 | 解释题目背景、概念定义、需求拆解。 |

---

## 5. 重点文件的“改动优先级”

如果你要继续强化这个项目，优先看：

1. `agent/react_loop.py`  
原因：决定规划稳定性，是“能不能产出完整 pipeline”的关键。

2. `agent/tools.py` + `agent/catalog.py`  
原因：决定模型对 step 结构理解质量（尤其 config 字段）。

3. `engine/nodes/data/market_bars.py` + `engine/nodes/ai/research_chat.py`  
原因：这是“mock -> real integration”的核心交付。

4. `engine/core/scheduler.py` + `engine/core/context.py`  
原因：决定引用解析、依赖执行、最终输出正确性。

---

## 6. 常见问题与定位命令（实用）

## 6.1 Agent 输出空 pipeline

```bash
python3 test.py
sed -n '1,260p' agent/react_loop.py
sed -n '1,260p' agent/tools.py
```

关注：终止条件是否过早触发、`get_pipeline` 校验是否太宽/太严。

## 6.2 数据节点报错

```bash
python3 tests/run_tests.py
sed -n '1,320p' engine/nodes/data/market_bars.py
python3 -m pip show baostock
```

关注：symbol 格式（`sh.600000/sz.000001`）、BaoStock 登录、fallback 是否命中。

## 6.3 研究解释节点报错

```bash
sed -n '1,320p' engine/nodes/ai/research_chat.py
echo $OPENAI_BASE_URL
echo $REACT_MODEL
```

关注：API key、base_url 是否为 OpenAI-Compatible、响应 payload 结构兼容性。

---

## 7. 总结

这个仓库的本质不是“做一个模型调用脚本”，而是：

- 用 Agent 规划流程（`agent/`）
- 用 Runtime 执行流程（`engine/`）
- 用 tests 保证回归（`tests/`）

你可以把它当成一个“小型可编排研究系统”的最小实现。  
理解每个文件职责后，后续改造会很清晰：先改规划稳定性，再改节点真实性，最后补测试与文档。
