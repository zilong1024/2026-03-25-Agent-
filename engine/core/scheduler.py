import json
import re
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Set

from ..dsl.models import Pipeline, Step
from .context import ExecutionContext


class PipelineScheduler:
    def __init__(self, registry: Any) -> None:
        self.registry = registry

    async def execute(self, pipeline: Pipeline) -> Dict[str, Any]:
        runtime = ExecutionContext(pipeline.pipeline_id or "unknown")
        execution = _ExecutionGraph.from_pipeline(pipeline)

        while execution.has_waiting_steps():
            ready_batch = execution.pop_ready_batch()
            if not ready_batch:
                raise ValueError("Deadlock detected. Pending: {0}".format(execution.list_waiting()))
            for step_id in ready_batch:
                step = execution.lookup(step_id)
                result = await self._execute_step(step, runtime)
                runtime.set_output(step_id, result)
                execution.mark_complete(step_id)

        return {
            "pipeline_id": pipeline.pipeline_id,
            "status": "success",
            "outputs": runtime.step_outputs,
        }

    async def _execute_step(self, step: Step, context: ExecutionContext) -> Any:
        implementation = self.registry.create(step.kind)
        prepared_config = self._resolve(step.config, context)
        return await implementation.execute(prepared_config, context)

    def _resolve(self, value: Any, context: ExecutionContext) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, context) for item in value]
        if isinstance(value, str):
            return self._resolve_string(value, context)
        return value

    def _resolve_string(self, text: str, context: ExecutionContext) -> Any:
        if text.startswith("$"):
            try:
                return context.resolve_reference(text)
            except Exception:
                return text

        pattern = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?:\[['\"][^'\"]+['\"]\]|\[\d+\])*")
        did_replace = False

        def replace(match: Any) -> str:
            nonlocal did_replace
            reference = match.group(0)
            try:
                resolved = context.resolve_reference(reference)
            except Exception:
                return reference
            did_replace = True
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved, ensure_ascii=False)
            return str(resolved)

        rendered = pattern.sub(replace, text)
        return rendered if did_replace else text

    def _get_refs(self, step: Step) -> Set[str]:
        refs: Set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if isinstance(value, str) and value.startswith("$"):
                refs.add(value[1:].split("[")[0].split(".")[0])

        walk(step.config)
        return refs

    def _build_dependency_map(self, pipeline: Pipeline) -> Dict[str, Set[str]]:
        deps = {step.id: set() for step in pipeline.steps}
        known_ids = {step.id for step in pipeline.steps}
        for step in pipeline.steps:
            for downstream_id in step.next or []:
                if downstream_id in deps:
                    deps[downstream_id].add(step.id)
            for source_id in self._get_refs(step):
                if source_id in known_ids:
                    deps[step.id].add(source_id)
        return deps


class _ExecutionGraph:
    def __init__(
        self,
        steps_by_id: Dict[str, Step],
        waiting: Deque[str],
        dependencies: Dict[str, Set[str]],
    ) -> None:
        self._steps_by_id = steps_by_id
        self._waiting = waiting
        self._dependencies = dependencies
        self._completed: Set[str] = set()
        self._queued: Set[str] = set(waiting)

    @classmethod
    def from_pipeline(cls, pipeline: Pipeline) -> "_ExecutionGraph":
        steps_by_id = {step.id: step for step in pipeline.steps}
        dependencies = cls._collect_dependencies(pipeline)
        starters = deque(step.id for step in pipeline.steps if not dependencies.get(step.id))
        return cls(steps_by_id=steps_by_id, waiting=starters, dependencies=dependencies)

    def has_waiting_steps(self) -> bool:
        return bool(self._waiting)

    def pop_ready_batch(self) -> List[str]:
        ready: List[str] = []
        remaining = len(self._waiting)
        while remaining > 0:
            step_id = self._waiting.popleft()
            self._queued.discard(step_id)
            remaining -= 1
            if self._is_ready(step_id):
                ready.append(step_id)
            else:
                self._waiting.append(step_id)
                self._queued.add(step_id)
        return ready

    def lookup(self, step_id: str) -> Step:
        return self._steps_by_id[step_id]

    def mark_complete(self, step_id: str) -> None:
        self._completed.add(step_id)
        for candidate in self._discover_dependents(step_id):
            if candidate not in self._completed and candidate not in self._queued:
                self._waiting.append(candidate)
                self._queued.add(candidate)

    def list_waiting(self) -> List[str]:
        return list(self._waiting)

    def _is_ready(self, step_id: str) -> bool:
        return all(parent in self._completed for parent in self._dependencies.get(step_id, set()))

    def _discover_dependents(self, step_id: str) -> Iterable[str]:
        for candidate, parents in self._dependencies.items():
            if step_id in parents:
                yield candidate

    @classmethod
    def _collect_dependencies(cls, pipeline: Pipeline) -> Dict[str, Set[str]]:
        dependency_map = {step.id: set() for step in pipeline.steps}
        known_ids = {step.id for step in pipeline.steps}
        for step in pipeline.steps:
            for downstream_id in step.next or []:
                if downstream_id in dependency_map:
                    dependency_map[downstream_id].add(step.id)
            for source_id in cls._extract_refs(step.config):
                if source_id in known_ids:
                    dependency_map[step.id].add(source_id)
        return dependency_map

    @classmethod
    def _extract_refs(cls, value: Any) -> Set[str]:
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
            if isinstance(item, str) and item.startswith("$"):
                refs.add(item[1:].split("[")[0].split(".")[0])

        walk(value)
        return refs
