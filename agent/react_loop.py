import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..engine.core.builder import PipelineBuilder
from .tools import bind_builder, execute_tool, get_tool_specs

SYSTEM_PROMPT = """You operate a draft builder for quant research plans.

Available actions:
- add_step
- update_step
- connect_steps
- get_catalog
- get_details
- get_pipeline

Operating rules:
- Build the plan through tool calls, not plain-text answers.
- `add_step` and `update_step` evaluate a step immediately and return either output or an error.
- Use catalog inspection before guessing a config shape.
- If a tool reports an error, repair the affected step instead of abandoning the draft.
- Use `connect_steps` and references so the draft has a coherent execution path.
- Only call `get_pipeline` after the draft contains a coherent ordered path.
- If arguments are rejected, call `get_details` and then retry with corrected payloads.

For a simple momentum-ranking request, a sensible draft usually includes:
trigger.manual -> data.market_bars -> factor.momentum -> factor.rank -> research_chat
"""


class ReactLoopAgent:
    def __init__(self, builder: Optional[PipelineBuilder] = None):
        self.builder = builder or PipelineBuilder()
        bind_builder(self.builder)
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = _normalize_base_url(os.getenv("OPENAI_BASE_URL"))
        self.model = os.getenv("REACT_MODEL", "gpt-4o-mini")
        self.max_iters = int(os.getenv("REACT_MAX_ITERS", "20"))
        self.max_no_tool_turns = int(os.getenv("REACT_MAX_NO_TOOL_TURNS", "4"))
        self.max_tool_error_turns = int(os.getenv("REACT_MAX_TOOL_ERROR_TURNS", "6"))

    async def run(self, prompt: str) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("ReactLoopAgent requires OPENAI_API_KEY before running planning loop")

        coordinator = _LoopCoordinator(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            prompt=prompt,
            iteration_limit=self.max_iters,
            no_tool_limit=self.max_no_tool_turns,
            tool_error_limit=self.max_tool_error_turns,
        )
        transcript = await coordinator.run()
        return {"pipeline": self.builder.get_pipeline(), "messages": transcript}


class _LoopCoordinator:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        iteration_limit: int,
        no_tool_limit: int,
        tool_error_limit: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.prompt_text = prompt
        self.prompt_lower = prompt.lower()
        self.iteration_limit = iteration_limit
        self.no_tool_limit = max(1, no_tool_limit)
        self.tool_error_limit = max(1, tool_error_limit)
        self.messages: List[Dict[str, Any]] = self._starting_transcript(prompt)
        self._tool_specs = get_tool_specs()

    async def run(self) -> List[Dict[str, Any]]:
        turn_count = 0
        no_tool_turns = 0
        tool_error_turns = 0

        while turn_count < self.iteration_limit:
            turn_count += 1
            try:
                reply = await self._next_model_message()
            except Exception as exc:
                self.messages.append(
                    self._loop_note(
                        "Stopping after model call failure: {0}".format(exc),
                    )
                )
                break

            self.messages.append(self._format_assistant_turn(reply))

            if not reply.tool_calls:
                no_tool_turns += 1
                if no_tool_turns >= self.no_tool_limit:
                    self.messages.append(
                        self._loop_note(
                            "Stopping after {0} consecutive assistant turns without tool calls.".format(
                                no_tool_turns
                            )
                        )
                    )
                    break
                self.messages.append(self._nudge_message(no_tool_turns))
                continue

            no_tool_turns = 0
            outcome = await self._apply_requested_actions(reply.tool_calls)

            if outcome["should_finish"]:
                break

            if outcome["all_failed"]:
                tool_error_turns += 1
            else:
                tool_error_turns = 0

            if outcome["had_error"]:
                self.messages.append(self._repair_hint_message(outcome["errors"]))

            if tool_error_turns >= self.tool_error_limit:
                self.messages.append(
                    self._loop_note(
                        "Stopping after {0} consecutive tool-failure turns.".format(tool_error_turns),
                    )
                )
                break

        if turn_count >= self.iteration_limit:
            self.messages.append(
                self._loop_note(
                    "Stopping at iteration limit ({0}).".format(self.iteration_limit),
                )
            )

        return self.messages

    async def _next_model_message(self) -> "_AssistantReply":
        last_error: Optional[Exception] = None
        for _ in range(2):
            try:
                return await self._request_model_message()
            except Exception as exc:
                last_error = exc
        raise RuntimeError("chat completion failed: {0}".format(last_error))

    async def _request_model_message(self) -> "_AssistantReply":
        endpoint = "{0}/chat/completions".format(self.base_url.rstrip("/"))
        request_payload = {
            "model": self.model,
            "messages": self.messages,
            "tools": self._tool_specs,
            "tool_choice": "auto",
            "temperature": 0,
        }
        headers = {
            "Authorization": "Bearer {0}".format(self.api_key),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(endpoint, headers=headers, json=request_payload)

        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("chat completion returned non-JSON response: {0}".format(exc))

        if response.status_code >= 400:
            error_block = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error_block, dict):
                error_text = error_block.get("message") or str(error_block)
            else:
                error_text = str(payload)
            raise RuntimeError(
                "chat completion request failed ({0}): {1}".format(response.status_code, error_text)
            )

        if not isinstance(payload, dict):
            raise RuntimeError("chat completion response is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("chat completion returned empty choices")

        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {})
        if not isinstance(message, dict):
            raise RuntimeError("chat completion missing message payload")
        return self._decode_assistant_message(message)

    def _decode_assistant_message(self, message: Dict[str, Any]) -> "_AssistantReply":
        raw_content = message.get("content")
        content = self._normalize_content(raw_content)
        tool_calls = self._decode_tool_calls(message.get("tool_calls"))
        return _AssistantReply(content=content, tool_calls=tool_calls)

    def _normalize_content(self, raw_content: Any) -> str:
        if isinstance(raw_content, str):
            return raw_content
        if isinstance(raw_content, list):
            parts: List[str] = []
            for chunk in raw_content:
                if not isinstance(chunk, dict):
                    continue
                text = chunk.get("text")
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
        return ""

    def _decode_tool_calls(self, raw_calls: Any) -> List["_ToolCall"]:
        if not isinstance(raw_calls, list):
            return []

        decoded: List[_ToolCall] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function", {})
            if not isinstance(function, dict):
                continue

            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue

            arguments = function.get("arguments", "{}")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=True)
            if not isinstance(arguments, str):
                arguments = "{}"

            call_id = item.get("id")
            if not isinstance(call_id, str) or not call_id:
                call_id = "call_{0}".format(uuid.uuid4().hex[:12])

            decoded.append(_ToolCall(call_id, name, arguments))
        return decoded

    async def _apply_requested_actions(self, tool_calls: List["_ToolCall"]) -> Dict[str, Any]:
        should_finish = False
        all_failed = True
        had_error = False
        errors: List[str] = []

        for tool_call in tool_calls:
            tool_message, result = await self._run_one_tool(tool_call)
            self.messages.append(tool_message)
            is_success = bool(result.get("success"))
            all_failed = all_failed and not is_success

            if not is_success:
                had_error = True
                errors.append(
                    "{0}: {1}".format(
                        tool_call.function.name,
                        str(result.get("error", "tool call failed")),
                    )
                )

            if tool_call.function.name == "get_pipeline" and is_success:
                pipeline = result.get("pipeline", {})
                missing_kinds = self._missing_kinds_for_prompt(pipeline)
                if missing_kinds:
                    had_error = True
                    errors.append(
                        "Pipeline exported too early; missing step kinds: {0}".format(
                            ", ".join(missing_kinds)
                        )
                    )
                else:
                    should_finish = True

        return {
            "should_finish": should_finish,
            "all_failed": all_failed,
            "had_error": had_error,
            "errors": errors,
        }

    async def _run_one_tool(self, tool_call: "_ToolCall") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        raw_arguments = tool_call.function.arguments or "{}"
        parsed_arguments, parse_error = self._parse_arguments(raw_arguments)

        if parse_error is not None:
            result: Dict[str, Any] = {
                "success": False,
                "error": parse_error,
                "stage": "tooling",
            }
        else:
            result = await execute_tool(tool_call.function.name, parsed_arguments)

        tool_message = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.function.name,
            "content": json.dumps(result, ensure_ascii=True),
        }
        return tool_message, result

    def _starting_transcript(self, prompt: str) -> List[Dict[str, Any]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    def _format_assistant_turn(self, message: "_AssistantReply") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if message.tool_calls:
            payload["tool_calls"] = [self._encode_tool_call(tool_call) for tool_call in message.tool_calls]
        return payload

    def _encode_tool_call(self, tool_call: "_ToolCall") -> Dict[str, Any]:
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            },
        }

    def _parse_arguments(self, raw_arguments: str) -> Tuple[Dict[str, Any], Optional[str]]:
        try:
            payload = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return {}, "Tool arguments must be valid JSON object: {0}".format(exc.msg)
        if not isinstance(payload, dict):
            return {}, "Tool arguments must decode to a JSON object."
        return payload, None

    def _nudge_message(self, no_tool_turns: int) -> Dict[str, str]:
        if no_tool_turns == 1:
            message = (
                "Continue with tool calls. If config shape is unclear, call get_catalog/get_details first."
            )
        else:
            message = (
                "Still waiting for tool calls. Repair any invalid payloads, then continue using tools "
                "until get_pipeline succeeds."
            )
        return {"role": "user", "content": message}

    def _repair_hint_message(self, errors: List[str]) -> Dict[str, str]:
        joined = "; ".join(errors[:3]) if errors else "Unknown tool failure."
        return {
            "role": "user",
            "content": (
                "Tool errors were reported: {0}. Repair the relevant step configs and continue."
            ).format(joined),
        }

    def _loop_note(self, reason: str) -> Dict[str, str]:
        return {
            "role": "assistant",
            "content": (
                "Loop terminated by controller: {0}".format(reason)
            ),
        }

    def _missing_kinds_for_prompt(self, pipeline: Any) -> List[str]:
        if not isinstance(pipeline, dict):
            return []
        steps = pipeline.get("steps", [])
        if not isinstance(steps, list):
            return []

        present_kinds = {
            step.get("kind") for step in steps if isinstance(step, dict) and isinstance(step.get("kind"), str)
        }

        required: List[str] = []
        if any(token in self.prompt_lower for token in ["market bars", "bars", "bar data", "行情"]):
            required.append("data.market_bars")
        if "momentum" in self.prompt_lower:
            required.append("factor.momentum")
        if any(token in self.prompt_lower for token in ["rank", "ranking", "排序"]):
            required.append("factor.rank")
        if any(token in self.prompt_lower for token in ["explain", "explanation", "解释", "说明"]):
            required.append("research_chat")

        if required:
            required.insert(0, "trigger.manual")

        seen = set()
        ordered_required: List[str] = []
        for kind in required:
            if kind in seen:
                continue
            seen.add(kind)
            ordered_required.append(kind)

        return [kind for kind in ordered_required if kind not in present_kinds]


class _ToolFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _ToolFunction(name=name, arguments=arguments)


class _AssistantReply:
    def __init__(self, content: str, tool_calls: List[_ToolCall]) -> None:
        self.content = content
        self.tool_calls = tool_calls


def _normalize_base_url(raw_base: Optional[str]) -> str:
    if raw_base is None or not raw_base.strip():
        return "https://api.openai.com/v1"
    cleaned = raw_base.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return "{0}/v1".format(cleaned)
