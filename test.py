import asyncio
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_react_interview.agent.react_loop import ReactLoopAgent
from quant_react_interview.engine.core.engine import PipelineEngine


PROMPT = "Use the market bars to compute momentum, rank the symbols by momentum, and then explain the ranking."


async def main():
    print("Prompt:")
    print(PROMPT)
    print()

    print("Environment:")
    print("  REACT_MODEL =", os.getenv("REACT_MODEL", ""))
    print("  OPENAI_BASE_URL =", os.getenv("OPENAI_BASE_URL", ""))
    print("  OPENAI_API_KEY set =", bool(os.getenv("OPENAI_API_KEY")))
    print()

    agent = ReactLoopAgent()
    result = await agent.run(PROMPT)
    pipeline = result["pipeline"]

    print("Generated Pipeline:")
    print(json.dumps(pipeline, indent=2, ensure_ascii=False))
    print()

    engine = PipelineEngine()
    run_result = await engine.run_pipeline(pipeline)

    print("Execution Result:")
    print("  RUN_STATUS =", run_result["status"])
    print("  RUN_OUTPUT_KEYS =", sorted(run_result["outputs"].keys()))
    print()
    print("Outputs:")
    print(json.dumps(run_result["outputs"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
