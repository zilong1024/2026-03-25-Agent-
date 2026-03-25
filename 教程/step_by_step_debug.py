import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT.parent))

from quant_react_interview.engine.core.context import ExecutionContext
from quant_react_interview.engine.core.registry import get_registry
from quant_react_interview.engine.dsl.models import Pipeline
from quant_react_interview.engine.dsl.parser import PipelineParser

_REF_PATTERN = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?:\[['\"][^'\"]+['\"]\]|\[\d+\])*")


def _extract_ref_roots(value: Any) -> Set[str]:
    refs: Set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                walk(nested)
            return
        if isinstance(item, list):
            for nested in item:
                walk(nested)
            return
        if not isinstance(item, str):
            return

        for match in _REF_PATTERN.finditer(item):
            expression = match.group(0)
            root = expression[1:].split("[")[0].split(".")[0]
            if root:
                refs.add(root)

    walk(value)
    return refs


def _build_dependency_map(pipeline: Pipeline) -> Dict[str, Set[str]]:
    dependency_map: Dict[str, Set[str]] = {step.id: set() for step in pipeline.steps}
    known_ids = set(dependency_map.keys())

    for step in pipeline.steps:
        for downstream_id in step.next or []:
            if downstream_id in dependency_map:
                dependency_map[downstream_id].add(step.id)

    for step in pipeline.steps:
        for source_id in _extract_ref_roots(step.config):
            if source_id in known_ids:
                dependency_map[step.id].add(source_id)

    return dependency_map


def _resolve_value(value: Any, context: ExecutionContext) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, context) for item in value]
    if isinstance(value, str):
        return _resolve_string(value, context)
    return value


def _resolve_string(text: str, context: ExecutionContext) -> Any:
    if text.startswith("$"):
        try:
            return context.resolve_reference(text)
        except Exception:
            return text

    replaced = False

    def substitute(match: Any) -> str:
        nonlocal replaced
        reference = match.group(0)
        try:
            value = context.resolve_reference(reference)
        except Exception:
            return reference

        replaced = True
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    rendered = _REF_PATTERN.sub(substitute, text)
    return rendered if replaced else text


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


async def run_debug(pipeline_path: Path) -> Dict[str, Any]:
    parser = PipelineParser()
    pipeline = parser.parse_file(pipeline_path)

    print("[INFO] Pipeline file:", str(pipeline_path))
    print("[INFO] Pipeline id:", pipeline.pipeline_id)
    print("[INFO] Pipeline name:", pipeline.name)
    print("[INFO] Total steps:", len(pipeline.steps))
    print()

    registry = get_registry()
    runtime = ExecutionContext(pipeline.pipeline_id or "debug_pipeline")

    step_order: List[str] = [step.id for step in pipeline.steps]
    step_by_id = {step.id: step for step in pipeline.steps}
    dependency_map = _build_dependency_map(pipeline)

    completed: Set[str] = set()
    round_index = 0

    while len(completed) < len(step_order):
        round_index += 1
        ready = [
            step_id
            for step_id in step_order
            if step_id not in completed and dependency_map.get(step_id, set()).issubset(completed)
        ]

        if not ready:
            pending = [step_id for step_id in step_order if step_id not in completed]
            raise RuntimeError("Deadlock detected. Pending: {0}".format(pending))

        print("=" * 88)
        print("[ROUND {0}] ready steps: {1}".format(round_index, ready))

        for step_id in ready:
            step = step_by_id[step_id]
            dependencies = sorted(dependency_map.get(step_id, set()))
            prepared_config = _resolve_value(step.config, runtime)

            print("-" * 88)
            print("[STEP] {0} ({1})".format(step.id, step.kind))
            print("[DEPS] {0}".format(dependencies if dependencies else []))
            print("[CONFIG]")
            print(_pretty(prepared_config))

            implementation = registry.create(step.kind)
            output = await implementation.execute(prepared_config, runtime)

            runtime.set_output(step_id, output)
            completed.add(step_id)

            print("[OUTPUT]")
            print(_pretty(output))
            print()

    print("=" * 88)
    print("[DONE] Completed steps:", step_order)
    print("[DONE] Output keys:", sorted(runtime.step_outputs.keys()))
    return runtime.step_outputs


def _resolve_pipeline_path(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return candidate.resolve()


async def main() -> None:
    raw_path = sys.argv[1] if len(sys.argv) > 1 else "examples/momentum_pipeline.yaml"
    pipeline_path = _resolve_pipeline_path(raw_path)
    if not pipeline_path.exists():
        raise FileNotFoundError("Pipeline file not found: {0}".format(pipeline_path))

    await run_debug(pipeline_path)


if __name__ == "__main__":
    asyncio.run(main())
