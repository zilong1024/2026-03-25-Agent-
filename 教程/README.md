# 教程目录

这个目录用于手把手理解 `quant_react_interview` 的完整执行流程。

## 建议学习顺序

1. 先读 `01_一步一步调试.md`：理解从 Prompt 到输出的全链路。
2. 打开 `流程图.html`：看可视化流程图，建立整体脑图。
3. 运行 `step_by_step_debug.py`：按步骤打印每个节点的输入/输出。

## 你会得到什么

- 明确 Prompt 在哪里输入。
- 明确 Pipeline 是如何被 Agent 组装出来的。
- 明确 Runtime 如何按依赖顺序执行节点。
- 明确最终 Output 在哪里查看。

## 快速运行

在仓库根目录执行：

```bash
cd /home/h3c/zilong1024/260324-Agent笔试题/quant_react_interview
python3 tests/run_tests.py
python3 教程/step_by_step_debug.py examples/momentum_pipeline.yaml
```

如果要跑完整 Agent + LLM（`test.py`），需要设置：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `REACT_MODEL`
- `RESEARCH_CHAT_MODEL`

