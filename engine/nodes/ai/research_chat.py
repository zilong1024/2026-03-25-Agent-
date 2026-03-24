import os
from typing import Any, Dict

from openai import AsyncOpenAI
from ..base import BaseStep


class ResearchChatStep(BaseStep):
    async def execute(self, config: Dict[str, Any], context: Any) -> Dict[str, Any]:
        prompt = str(config.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("research_chat requires a non-empty prompt")

        model = str(
            config.get("model")
            or os.getenv("RESEARCH_CHAT_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4o-mini"
        )
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("research_chat requires OPENAI_API_KEY to call chat completions")

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL"),
        )

        temperature = self._parse_temperature(config.get("temperature", 0.2))
        try:
            response = await client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a concise quant research assistant.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise RuntimeError("research_chat API call failed: {0}".format(exc))

        if not response.choices:
            raise RuntimeError("research_chat API call failed: empty choices returned")

        message = response.choices[0].message
        content = (message.content or "").strip()
        if not content:
            raise RuntimeError("research_chat API call failed: empty content returned")

        payload: Dict[str, Any] = {
            "content": content,
            "model": response.model or model,
            "finish_reason": response.choices[0].finish_reason,
        }

        if response.usage is not None:
            payload["usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return payload

    def _parse_temperature(self, raw_value: Any) -> float:
        try:
            value = float(raw_value)
        except Exception:
            raise ValueError("research_chat temperature must be a number between 0 and 2")
        if value < 0 or value > 2:
            raise ValueError("research_chat temperature must be between 0 and 2")
        return value
