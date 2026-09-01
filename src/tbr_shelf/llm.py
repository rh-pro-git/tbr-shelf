"""Chat completions against any OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama, a hosted API)."""

from __future__ import annotations

from . import net
from .context import AppContext


class LLMUnavailable(RuntimeError):
    pass


async def chat(ctx: AppContext, messages: list[dict], *, temperature: float, max_tokens: int) -> str:
    settings = ctx.settings
    if not settings.has_llm:
        raise LLMUnavailable("no language model configured (set TBR_LLM_URL)")
    body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **settings.llm_extra_body,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    try:
        response = await net.request("POST", settings.llm_url, headers=headers, json=body)
        return response.json()["choices"][0]["message"].get("content", "").strip()
    except Exception as exc:
        raise LLMUnavailable(net.describe_error(exc)) from exc
