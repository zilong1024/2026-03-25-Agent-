import json
import os
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

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
        api_key = os.getenv("OPENAI_API_KEY")
        self.client: Optional[AsyncOpenAI]
        if api_key:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv("OPENAI_BASE_URL"),
            )
        else:
            self.client = None
        self.model = os.getenv("REACT_MODEL", "gpt-4o-mini")
        self.max_iters = int(os.getenv("REACT_MAX_ITERS", "12"))
        self.max_no_tool_turns = int(os.getenv("REACT_MAX_NO_TOOL_TURNS", "3"))
        self.max_tool_error_turns = int(os.getenv("REACT_MAX_TOOL_ERROR_TURNS", "4"))

    async def run(self, prompt: str) -> Dict[str, Any]:
        if self.client is None:
            raise RuntimeError("ReactLoopAgent requires OPENAI_API_KEY before running planning loop")

        coordinator = _LoopCoordinator(
            client=self.client,
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
        client: AsyncOpenAI,
        model: str,
        prompt: str,
        iteration_limit: int,
        no_tool_limit: int,
        tool_error_limit: int,
    ) -> None:
        self.client = client
        self.model = model
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

    async def _next_model_message(self) -> Any:
        last_error: Optional[Exception] = None
        for _ in range(2):
            try:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=self._tool_specs,
                    tool_choice="auto",
                    temperature=0,
                )
                return completion.choices[0].message
            except Exception as exc:
                last_error = exc
        raise RuntimeError("chat completion failed: {0}".format(last_error))

    async def _apply_requested_actions(self, tool_calls: List[Any]) -> Dict[str, Any]:
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
                should_finish = True

        return {
            "should_finish": should_finish,
            "all_failed": all_failed,
            "had_error": had_error,
            "errors": errors,
        }

    async def _run_one_tool(self, tool_call: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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

    def _format_assistant_turn(self, message: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if message.tool_calls:
            payload["tool_calls"] = [self._encode_tool_call(tool_call) for tool_call in message.tool_calls]
        return payload

    def _encode_tool_call(self, tool_call: Any) -> Dict[str, Any]:
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
