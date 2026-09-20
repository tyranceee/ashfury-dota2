"""Web search exposed to DeepSeek through a hosted function-calling loop.

DeepSeek has no server-side search tool. Verified against the official API
reference:

* Chat Completions: "Currently, only functions are supported as a tool."
* Responses API: `web_search` / `file_search` / `code_interpreter` / `mcp` are
  listed under Tools as **Ignored**.

So searching is implemented the only way the API supports it: the server
declares a `web_search` function, DeepSeek decides when to call it, the server
executes the real search against a configured provider, and the results are fed
back as a `tool` message until DeepSeek answers. The loop is bounded and never
runs when the Owner has not both enabled search and configured a provider.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

WEB_SEARCH_TOOL_NAME = "web_search"
DEFAULT_MAX_SEARCH_CALLS = 5
DEFAULT_RESULTS_PER_SEARCH = 5
DEFAULT_SEARCH_TIMEOUT_SECONDS = 20
MAX_SNIPPET_CHARS = 1200

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": WEB_SEARCH_TOOL_NAME,
        "description": (
            "联网搜索最新信息。用于查询当前版本的技能机制、物品改动、英雄胜率、"
            "官方公告或任何本地比赛数据里没有、且需要最新事实支撑的问题。"
            "不要用它复述本地 JSON 里已有的数据。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，尽量具体，例如“Dota 2 7.xx 幻影长矛手 幻象 继承 攻击力”。",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么需要联网确认这一条。",
                },
            },
            "required": ["query"],
        },
    },
}


class WebSearchNotConfigured(Exception):
    pass


class WebSearchError(Exception):
    pass


def web_search_tool_schema() -> dict:
    return json.loads(json.dumps(WEB_SEARCH_TOOL))


def search_provider_config(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "provider": (env.get("DOTA2_SEARCH_PROVIDER") or "tavily").strip().lower(),
        "api_key": (env.get("DOTA2_SEARCH_API_KEY") or "").strip(),
        "api_key_file": (env.get("DOTA2_SEARCH_API_KEY_FILE") or "").strip(),
        "endpoint": (env.get("DOTA2_SEARCH_ENDPOINT") or "").strip(),
        "max_calls": int(env.get("DOTA2_SEARCH_MAX_CALLS") or DEFAULT_MAX_SEARCH_CALLS),
        "results": int(env.get("DOTA2_SEARCH_RESULTS") or DEFAULT_RESULTS_PER_SEARCH),
    }


def resolve_api_key(config: dict) -> str:
    if config.get("api_key"):
        return config["api_key"]
    key_file = config.get("api_key_file")
    if not key_file:
        return ""
    path = Path(key_file)
    try:
        return path.read_text("utf-8").strip()
    except OSError:
        return ""


def search_available(config: dict | None = None) -> tuple[bool, str]:
    config = config or search_provider_config()
    if not config.get("api_key") and not resolve_api_key(config):
        return False, "未配置搜索 API Key（DOTA2_SEARCH_API_KEY 或 DOTA2_SEARCH_API_KEY_FILE）"
    if config.get("provider") not in {"tavily", "brave", "searxng", "custom"}:
        return False, f"不支持的搜索提供方：{config.get('provider')}"
    return True, "ready"


def web_search(query: str, config: dict | None = None) -> dict:
    """Execute one real web search and return a normalized result set."""
    import httpx

    config = config or search_provider_config()
    query = (query or "").strip()
    if not query:
        raise WebSearchError("Empty search query")
    ok, reason = search_available(config)
    if not ok:
        raise WebSearchNotConfigured(reason)

    provider = config["provider"]
    api_key = resolve_api_key(config)
    limit = max(1, min(int(config.get("results") or DEFAULT_RESULTS_PER_SEARCH), 10))

    try:
        if provider == "tavily":
            response = httpx.post(
                config.get("endpoint") or "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                    "include_answer": True,
                },
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": (item.get("content") or "")[:MAX_SNIPPET_CHARS],
                }
                for item in (body.get("results") or [])
            ]
            answer = body.get("answer")

        elif provider == "brave":
            response = httpx.get(
                config.get("endpoint") or "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": (item.get("description") or "")[:MAX_SNIPPET_CHARS],
                }
                for item in ((body.get("web") or {}).get("results") or [])
            ]
            answer = None

        elif provider == "searxng":
            endpoint = config.get("endpoint")
            if not endpoint:
                raise WebSearchError("searxng 需要配置 DOTA2_SEARCH_ENDPOINT")
            response = httpx.get(
                endpoint,
                params={"q": query, "format": "json"},
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": (item.get("content") or "")[:MAX_SNIPPET_CHARS],
                }
                for item in (body.get("results") or [])[:limit]
            ]
            answer = None

        else:  # custom
            endpoint = config.get("endpoint")
            if not endpoint:
                raise WebSearchError("custom 提供方需要配置 DOTA2_SEARCH_ENDPOINT")
            response = httpx.post(
                endpoint,
                json={"query": query, "limit": limit},
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            raw = body.get("results") if isinstance(body, dict) else body
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": (item.get("snippet") or item.get("content") or "")[:MAX_SNIPPET_CHARS],
                }
                for item in (raw or [])[:limit]
            ]
            answer = body.get("answer") if isinstance(body, dict) else None

    except WebSearchError:
        raise
    except Exception as error:
        raise WebSearchError(f"{provider} 搜索失败：{error}") from error

    return {
        "query": query,
        "provider": provider,
        "answer": answer,
        "result_count": len(results),
        "results": results,
    }


def run_search_loop(
    client,
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float,
    thinking: str | None = None,
    search_config: dict | None = None,
    max_iterations: int | None = None,
) -> dict:
    """Drive the DeepSeek tool-calling loop until a final answer is produced.

    Returns the final assistant message plus the citation list and the combined
    token usage across every round trip.
    """
    config = search_config or search_provider_config()
    ok, reason = search_available(config)
    if not ok:
        raise WebSearchNotConfigured(reason)

    iteration_cap = int(max_iterations or config.get("max_calls") or DEFAULT_MAX_SEARCH_CALLS)
    conversation = list(messages)
    citations: list[dict] = []
    rounds: list[dict] = []
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }

    for iteration in range(iteration_cap + 1):
        allow_tools = iteration < iteration_cap
        result = client.chat(
            conversation,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=[web_search_tool_schema()] if allow_tools else None,
            thinking=thinking,
        )
        for key in total_usage:
            total_usage[key] += int((result.get("usage") or {}).get(key) or 0)
        rounds.append({
            "iteration": iteration,
            "finish_reason": result.get("finish_reason"),
            "usage": result.get("usage"),
        })

        tool_calls = result.get("tool_calls") or []
        if not tool_calls or not allow_tools:
            return {
                "content": result.get("content") or "",
                "reasoning_content": result.get("reasoning_content") or "",
                "finish_reason": result.get("finish_reason"),
                "model": result.get("model"),
                "response_id": result.get("response_id"),
                "usage": total_usage,
                "citations": citations,
                "search_rounds": rounds,
                "tool_call_count": len(citations),
            }

        conversation.append({
            "role": "assistant",
            "content": result.get("content") or "",
            "tool_calls": tool_calls,
        })

        for call in tool_calls:
            function = call.get("function") or {}
            call_id = call.get("id") or f"call_{iteration}"
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"query": str(raw_arguments)[:200]}
            query = str(arguments.get("query") or "").strip()
            try:
                outcome = web_search(query, config)
                for item in outcome["results"]:
                    citations.append({
                        **item,
                        "query": query,
                        "iteration": iteration,
                        "reason": arguments.get("reason"),
                    })
                payload = {
                    "query": query,
                    "answer": outcome.get("answer"),
                    "results": outcome["results"],
                }
            except (WebSearchError, WebSearchNotConfigured) as error:
                payload = {"query": query, "error": str(error), "results": []}
            conversation.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(payload, ensure_ascii=False),
            })

    # Defensive: the loop always returns from inside, but keep a valid shape.
    return {
        "content": "",
        "reasoning_content": "",
        "finish_reason": "tool_call_limit",
        "model": getattr(client, "model", None),
        "response_id": None,
        "usage": total_usage,
        "citations": citations,
        "search_rounds": rounds,
        "tool_call_count": len(citations),
    }
