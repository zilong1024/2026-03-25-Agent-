import os
from typing import Any, Dict, Optional

import httpx

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

        base_url = self._normalize_base_url(os.getenv("OPENAI_BASE_URL"))
        endpoint = "{0}/chat/completions".format(base_url.rstrip("/"))
        temperature = self._parse_temperature(config.get("temperature", 0.2))

        request_payload = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a concise quant research assistant.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        headers = {
            "Authorization": "Bearer {0}".format(api_key),
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(endpoint, headers=headers, json=request_payload)
        except Exception as exc:
            raise RuntimeError("research_chat API call failed: {0}".format(exc))

        payload = self._decode_response_payload(response)
        choice = payload["choices"][0]
        message = choice.get("message", {})
        if not isinstance(message, dict):
            raise RuntimeError("research_chat API call failed: malformed message payload")

        content = self._normalize_content(message.get("content"))
        if not content:
            raise RuntimeError("research_chat API call failed: empty content returned")

        result: Dict[str, Any] = {
            "content": content,
            "model": payload.get("model", model),
            "finish_reason": choice.get("finish_reason"),
        }

        usage = payload.get("usage", {})
        if isinstance(usage, dict):
            result["usage"] = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        return result

    def _decode_response_payload(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("research_chat API call failed: non-JSON response ({0})".format(exc))

        if response.status_code >= 400:
            error_block = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error_block, dict):
                error_text = error_block.get("message") or str(error_block)
            else:
                error_text = str(payload)
            raise RuntimeError(
                "research_chat API call failed ({0}): {1}".format(response.status_code, error_text)
            )

        if not isinstance(payload, dict):
            raise RuntimeError("research_chat API call failed: response payload is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("research_chat API call failed: empty choices returned")
        return payload

    def _parse_temperature(self, raw_value: Any) -> float:
        try:
            value = float(raw_value)
        except Exception:
            raise ValueError("research_chat temperature must be a number between 0 and 2")
        if value < 0 or value > 2:
            raise ValueError("research_chat temperature must be between 0 and 2")
        return value

    def _normalize_base_url(self, raw_base: Optional[str]) -> str:
        if raw_base is None or not raw_base.strip():
            return "https://api.openai.com/v1"
        cleaned = raw_base.strip().rstrip("/")
        if cleaned.endswith("/v1"):
            return cleaned
        return "{0}/v1".format(cleaned)

    def _normalize_content(self, raw_content: Any) -> str:
        if isinstance(raw_content, str):
            return raw_content.strip()
        if isinstance(raw_content, list):
            parts = []
            for chunk in raw_content:
                if not isinstance(chunk, dict):
                    continue
                text = chunk.get("text")
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts).strip()
        return ""
