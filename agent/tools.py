import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from ..engine.core.builder import PipelineBuilder
from .catalog import get_catalog as catalog_list
from .catalog import get_details as catalog_details

ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]

_active_builder: Optional[PipelineBuilder] = None


def bind_builder(builder: PipelineBuilder) -> None:
    global _active_builder
    _active_builder = builder


def get_tool_specs() -> List[Dict[str, Any]]:
    known_kinds = [entry.get("kind") for entry in catalog_list() if isinstance(entry, dict)]
    config_schema = {
        "type": "object",
        "description": (
            "Step config object. Values may be literals or references such as "
            "$step_id['field'] from upstream outputs."
        ),
        "additionalProperties": True,
    }
    return [
        _function_spec(
            name="add_step",
            description=(
                "Create one draft step. Preferred flow: call get_catalog/get_details first, "
                "then add_step with a concrete config payload."
            ),
            properties={
                "kind": {
                    "type": "string",
                    "enum": [kind for kind in known_kinds if isinstance(kind, str)],
                    "description": "Step kind from get_catalog.",
                },
                "step_id": {
                    "type": "string",
                    "description": "Optional explicit id (letters, numbers, underscore).",
                },
                "config": config_schema,
            },
            required=["kind", "config"],
        ),
        _function_spec(
            name="update_step",
            description=(
                "Modify an existing step config and immediately re-evaluate that step."
            ),
            properties={
                "step_id": {
                    "type": "string",
                    "description": "Existing step id returned by add_step/get_pipeline.",
                },
                "config": config_schema,
            },
            required=["step_id", "config"],
        ),
        _function_spec(
            name="connect_steps",
            description=(
                "Declare execution order so source step runs before target step."
            ),
            properties={
                "source_id": {"type": "string", "description": "Upstream step id."},
                "target_id": {"type": "string", "description": "Downstream step id."},
            },
            required=["source_id", "target_id"],
        ),
        _function_spec(
            name="get_catalog",
            description=(
                "List all available step kinds with required fields and starter examples."
            ),
            properties={},
            required=[],
        ),
        _function_spec(
            name="get_details",
            description=(
                "Inspect one step kind in detail, including field guidance and troubleshooting notes."
            ),
            properties={
                "kind": {
                    "type": "string",
                    "enum": [kind for kind in known_kinds if isinstance(kind, str)],
                }
            },
            required=["kind"],
        ),
        _function_spec(
            name="get_pipeline",
            description=(
                "Export the current draft when it has valid references and at least one dependency link."
            ),
            properties={},
            required=[],
        ),
    ]


async def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if _active_builder is None:
        return {"success": False, "error": "Builder not bound", "stage": "tooling"}

    handlers = _tool_handlers(_active_builder)
    if name not in handlers:
        return {"success": False, "error": "Unknown tool: {0}".format(name)}

    try:
        return await handlers[name](arguments)
    except Exception as exc:
        return {"success": False, "error": str(exc), "stage": "tooling"}


def _function_spec(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: List[str],
) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _tool_handlers(builder: PipelineBuilder) -> Dict[str, ToolHandler]:
    return {
        "add_step": lambda payload: _add_step(builder, payload),
        "update_step": lambda payload: _update_step(builder, payload),
        "connect_steps": lambda payload: _connect_steps(builder, payload),
        "get_catalog": lambda payload: _get_catalog(payload),
        "get_details": lambda payload: _get_details(payload),
        "get_pipeline": lambda payload: _get_pipeline(builder, payload),
    }


async def _add_step(builder: PipelineBuilder, payload: Dict[str, Any]) -> Dict[str, Any]:
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind:
        return {"success": False, "action": "add_step", "error": "kind must be a non-empty string"}

    config = payload.get("config", {})
    if not isinstance(config, dict):
        return {"success": False, "action": "add_step", "error": "config must be an object"}

    step_id = payload.get("step_id")
    if step_id is not None and (not isinstance(step_id, str) or not step_id):
        return {"success": False, "action": "add_step", "error": "step_id must be a non-empty string"}

    created_id = builder.add_step(
        kind=kind,
        config=config,
        step_id=step_id,
    )
    result = await _run_step(builder, created_id)
    result["action"] = "add_step"
    return result


async def _update_step(builder: PipelineBuilder, payload: Dict[str, Any]) -> Dict[str, Any]:
    step_id = payload.get("step_id")
    if not isinstance(step_id, str) or not step_id:
        return {"success": False, "action": "update_step", "error": "step_id must be a non-empty string"}

    config = payload.get("config", {})
    if not isinstance(config, dict):
        return {"success": False, "action": "update_step", "error": "config must be an object"}

    builder.update_step(step_id, config)
    result = await _run_step(builder, step_id)
    result["action"] = "update_step"
    return result


async def _connect_steps(builder: PipelineBuilder, payload: Dict[str, Any]) -> Dict[str, Any]:
    source_id = payload.get("source_id")
    target_id = payload.get("target_id")
    if not isinstance(source_id, str) or not source_id:
        return {"success": False, "action": "connect_steps", "error": "source_id must be a non-empty string"}
    if not isinstance(target_id, str) or not target_id:
        return {"success": False, "action": "connect_steps", "error": "target_id must be a non-empty string"}

    builder.connect_steps(source_id, target_id)
    return {
        "success": True,
        "action": "connect_steps",
        "source_id": source_id,
        "target_id": target_id,
    }


async def _get_catalog(_: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, "action": "get_catalog", "catalog": catalog_list()}


async def _get_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    details = catalog_details(payload["kind"])
    if "error" in details:
        return {"success": False, "action": "get_details", "error": details["error"]}
    return {"success": True, "action": "get_details", "details": details}


async def _get_pipeline(builder: PipelineBuilder, _: Dict[str, Any]) -> Dict[str, Any]:
    pipeline = builder.get_pipeline()
    issues = _collect_pipeline_issues(pipeline)
    if issues:
        return {
            "success": False,
            "action": "get_pipeline",
            "error": "; ".join(issues),
            "issues": issues,
            "pipeline": pipeline,
        }
    return {"success": True, "action": "get_pipeline", "pipeline": pipeline}


async def _run_step(builder: PipelineBuilder, step_id: str) -> Dict[str, Any]:
    try:
        output = await builder.execute_step(step_id)
    except Exception as exc:
        return {"success": False, "step_id": step_id, "error": str(exc), "stage": "execution"}
    return {"success": True, "step_id": step_id, "output": output, "stage": "execution"}


def _collect_pipeline_issues(pipeline: Dict[str, Any]) -> List[str]:
    steps = pipeline.get("steps", [])
    if not isinstance(steps, list) or not steps:
        return ["Pipeline is empty"]

    issues: List[str] = []
    seen: Set[str] = set()
    for step in steps:
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id:
            issues.append("Each step must have a non-empty id.")
            continue
        if step_id in seen:
            issues.append("Duplicate step id: {0}".format(step_id))
        seen.add(step_id)

    known_ids = seen
    dependency_links = 0
    for step in steps:
        step_id = step.get("id", "<unknown>")
        next_steps = step.get("next", [])
        if next_steps is None:
            next_steps = []
        if not isinstance(next_steps, list):
            issues.append("Step {0} has invalid next field (must be list).".format(step_id))
            continue

        for target_id in next_steps:
            if not isinstance(target_id, str):
                issues.append("Step {0} has non-string next target.".format(step_id))
                continue
            dependency_links += 1
            if target_id not in known_ids:
                issues.append("Step {0} points to unknown step {1}.".format(step_id, target_id))

        for reference_root in _extract_reference_roots(step.get("config", {})):
            if reference_root in known_ids:
                dependency_links += 1

    if len(known_ids) > 1 and dependency_links == 0:
        issues.append("Pipeline has multiple steps but no dependency links.")

    return issues


def _extract_reference_roots(value: Any) -> Set[str]:
    roots: Set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                walk(nested)
            return
        if isinstance(item, list):
            for nested in item:
                walk(nested)
            return
        if isinstance(item, str) and item.startswith("$"):
            match = re.match(r"^\$([A-Za-z_][A-Za-z0-9_]*)", item)
            if match is not None:
                roots.add(match.group(1))

    walk(value)
    return roots
