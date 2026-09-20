"""Web search for the preliminary review, as replaceable provider adapters.

Why providers exist here
------------------------
DeepSeek's *native* transports do not execute search:

* ``POST /chat/completions`` rejects ``tools[].type`` other than ``function``
  (``unknown variant 'web_search', expected 'function'``).
* ``POST /responses`` accepts ``tools:[{"type":"web_search"}]`` but silently
  ignores it; the model then reports it has no browsing ability.

DeepSeek's *Anthropic-compatible* transport does execute search server-side:

* ``POST /anthropic/v1/messages`` with ``tools:[{"type":"web_search_20250305"}]``
  runs a real hosted search and returns ``web_search_tool_result`` blocks plus
  ``usage.server_tool_use.web_search_requests``.

So the hosted provider is the default, and no third-party search vendor is
required. The other providers stay available so the capability is swappable
rather than hard-wired.

Contract
--------
Every provider returns the same :class:`SearchResultSet`: the exact query, per
result title/url/snippet/published_at, the source service, the raw provider
response, and the actual cost. Executors never rewrite a query: the query comes
from the upstream task, is sent verbatim, and a provider that echoes back a
different query has its result rejected rather than accepted.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

SEARCH_SCHEMA_VERSION = "ashfury.web-search.v1"
DEFAULT_RESULTS_PER_SEARCH = 5
DEFAULT_SEARCH_TIMEOUT_SECONDS = 90
MAX_SNIPPET_CHARS = 1200
MAX_QUERY_CHARS = 1000

PROVIDER_DEEPSEEK_HOSTED = "deepseek-hosted-search"
PROVIDER_TAVILY = "tavily"
PROVIDER_BRAVE = "brave"
PROVIDER_SEARXNG = "searxng"
PROVIDER_CUSTOM = "custom"

# The default provider needs no third-party credentials at all.
DEFAULT_PROVIDER = PROVIDER_DEEPSEEK_HOSTED

DEEPSEEK_ANTHROPIC_URL = "https://api.deepseek.com/anthropic/v1/messages"
DEEPSEEK_ANTHROPIC_VERSION = "2023-06-01"
DEEPSEEK_SEARCH_MODEL = "deepseek-flash"
DEEPSEEK_SEARCH_TOOL_TYPE = "web_search_20250305"

# Official CNY price table for deepseek-flash, per 1M tokens.
# Peak is 09:00-12:00 and 14:00-18:00 Beijing time on weekdays excluding Chinese
# public holidays; off-peak is exactly half.
DEEPSEEK_CNY_PRICING = {
    "snapshot_id": "deepseek-cny-flash",
    "currency": "CNY",
    "cache_hit_input_per_million": {"off_peak": 0.02, "peak": 0.04},
    "cache_miss_input_per_million": {"off_peak": 1.0, "peak": 2.0},
    "output_per_million": {"off_peak": 4.0, "peak": 8.0},
    "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
}

SEARCH_EXECUTOR_SYSTEM_PROMPT = (
    "你是确定性搜索执行器。必须原样使用用户提供的搜索词调用一次 web_search，"
    "不得翻译、改写、扩展或删减。不要形成面向用户的最终结论。"
)

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
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
                    "description": "搜索关键词，尽量具体，例如“Dota 2 7.41 幻影长矛手 幻象 继承 攻击力”。",
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
    """No provider in the environment can execute a search."""


class WebSearchError(Exception):
    """A provider was selected but the search failed."""


class SearchQueryRewritten(WebSearchError):
    """The service returned results for a query we did not send."""


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str = ""
    published_at: str | None = None
    source_service: str = ""
    rank: int = 0
    matched_query: str = ""
    is_independent_source: bool = True
    duplicate_of: str | None = None
    unsafe_url: bool = False
    unsafe_reason: str | None = None
    domain: str = ""

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "published_at": self.published_at,
            "source_service": self.source_service,
            "rank": self.rank,
            "matched_query": self.matched_query,
            "domain": self.domain,
            "is_independent_source": self.is_independent_source,
            "duplicate_of": self.duplicate_of,
            "unsafe_url": self.unsafe_url,
            "unsafe_reason": self.unsafe_reason,
        }


@dataclass
class SearchResultSet:
    query: str
    provider_id: str
    results: list[SearchResult] = field(default_factory=list)
    cost: dict = field(default_factory=dict)
    raw_response: dict | None = None
    queries_executed: list[str] = field(default_factory=list)
    duration_ms: int = 0
    dropped: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SEARCH_SCHEMA_VERSION

    @property
    def result_count(self) -> int:
        return len(self.results)

    def to_dict(self, include_raw: bool = False) -> dict:
        payload = {
            "schema_version": self.schema_version,
            "query": self.query,
            "provider_id": self.provider_id,
            "queries_executed": self.queries_executed,
            "result_count": self.result_count,
            "cost": self.cost,
            "duration_ms": self.duration_ms,
            "dropped": self.dropped,
            "warnings": self.warnings,
            "results": [result.to_dict() for result in self.results],
        }
        if include_raw:
            payload["raw_response"] = self.raw_response
        return payload


# ---------------------------------------------------------------------------
# URL safety, authorization scope, and duplicate/source-independence checks
# ---------------------------------------------------------------------------

_BLOCKED_HOST_SUFFIXES = (
    ".local", ".internal", ".localhost", ".home.arpa", ".lan",
)
_BLOCKED_HOSTS = {
    "localhost", "metadata", "metadata.google.internal", "instance-data",
}
_ALLOWED_SCHEMES = ("https",)


def normalize_url(url: str) -> str:
    """Normalize for duplicate detection: drop fragment, tracking, trailing slash."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    value = (url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    netloc = parts.netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    netloc = netloc.lower()
    try:
        netloc = netloc.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass
    kept = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=False)
        if not _is_tracking_param(key)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, urlencode(kept), ""))


_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {
    "ref", "referrer", "referer", "source", "requestid", "spm", "spm_id_from",
    "from", "share_source", "share_medium", "fbclid", "gclid", "yclid", "igshid",
}


def _is_tracking_param(key: str) -> bool:
    key = (key or "").strip().lower()
    return key in _TRACKING_PARAMS or key.startswith(_TRACKING_PARAM_PREFIXES)


def url_domain(url: str) -> str:
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", (url or "").strip())
    if not match:
        return ""
    host = match.group(1).split("@")[-1].split(":")[0].strip().lower()
    return host


def check_url_safety(url: str) -> tuple[bool, str | None]:
    """Reject anything that must never be fetched from the review pipeline."""
    value = (url or "").strip()
    if not value:
        return False, "empty_url"
    scheme_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://", value)
    if not scheme_match:
        return False, "missing_scheme"
    scheme = scheme_match.group(1).lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"scheme_not_allowed:{scheme}"
    host = url_domain(value)
    if not host:
        return False, "missing_host"
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False, "internal_host"
    if host.endswith(".onion"):
        return False, "tor_host"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False, "private_ip"
    if ":" in host and not re.match(r"^[0-9a-fA-F:.]+$", host):
        return False, "malformed_host"
    return True, None


def host_within_domains(host: str, allowed_domains) -> bool:
    host = (host or "").lower()
    for domain in allowed_domains or ():
        domain = str(domain).strip().lower().lstrip(".")
        if not domain:
            continue
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _title_shingles(title: str) -> set[str]:
    tokens = [token for token in re.split(r"[^0-9a-z\u4e00-\u9fff]+", (title or "").lower()) if token]
    if len(tokens) < 3:
        return set(tokens)
    return {" ".join(tokens[i:i + 2]) for i in range(len(tokens) - 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def validate_results(
    raw_results: list[dict],
    query: str,
    allowed_domains=(),
    near_duplicate_threshold: float = 0.9,
) -> tuple[list[SearchResult], dict]:
    """Apply URL safety, domain authorization, and duplicate detection.

    Keeps duplicate hits in the payload but marks them, so a repeated
    publication is never mistaken for an independent corroboration.
    """
    dropped = {
        "unsafe_url": 0,
        "domain_not_allowed": 0,
        "exact_duplicate": 0,
        "near_duplicate": 0,
        "invalid": 0,
    }
    results: list[SearchResult] = []
    seen_urls: dict[str, str] = {}
    representative_titles: list[tuple[str, str, set[str]]] = []

    for index, item in enumerate(raw_results):
        if not isinstance(item, dict):
            dropped["invalid"] += 1
            continue
        url = str(item.get("url") or "").strip()
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        if not url:
            dropped["invalid"] += 1
            continue

        safe, reason = check_url_safety(url)
        domain = url_domain(url)
        if not safe:
            dropped["unsafe_url"] += 1
            results.append(SearchResult(
                query=query, title=title, url=url, snippet="", source_service="",
                rank=index, matched_query=query, domain=domain,
                unsafe_url=True, unsafe_reason=reason, is_independent_source=False,
            ))
            continue
        if allowed_domains and not host_within_domains(domain, allowed_domains):
            dropped["domain_not_allowed"] += 1
            continue

        normalized = normalize_url(url)
        duplicate_of = seen_urls.get(normalized)
        if duplicate_of is None:
            shingles = _title_shingles(title)
            for source_id, rep_domain, rep_shingles in representative_titles:
                if rep_domain == domain and _jaccard(rep_shingles, shingles) >= near_duplicate_threshold:
                    duplicate_of = source_id
                    dropped["near_duplicate"] += 1
                    break
        else:
            dropped["exact_duplicate"] += 1

        snippet = re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip()[:MAX_SNIPPET_CHARS]
        result = SearchResult(
            query=query,
            title=title,
            url=url,
            snippet=snippet,
            published_at=item.get("published_at"),
            source_service=str(item.get("source_service") or ""),
            rank=index,
            matched_query=query,
            domain=domain,
            is_independent_source=duplicate_of is None,
            duplicate_of=duplicate_of,
        )
        results.append(result)
        if duplicate_of is None:
            seen_urls[normalized] = f"RES-{index + 1:03d}"
            representative_titles.append((f"RES-{index + 1:03d}", domain, _title_shingles(title)))

    return results, dropped


# ---------------------------------------------------------------------------
# Cost accounting (CNY, official DeepSeek price table)
# ---------------------------------------------------------------------------


def estimate_hosted_search_cost_cny(usage: dict, off_peak: bool = True) -> dict:
    """Cost in CNY from DeepSeek's official table.

    Accepts both usage shapes because two transports are in play: the review
    completion uses OpenAI-style keys (``prompt_tokens``/``completion_tokens``)
    while the hosted-search call returns Anthropic-style keys
    (``input_tokens``/``output_tokens``/``cache_read_input_tokens``).
    """
    usage = usage or {}
    window = "off_peak" if off_peak else "peak"
    cache_hit = int(
        usage.get("cache_read_input_tokens")
        if usage.get("cache_read_input_tokens") is not None
        else usage.get("prompt_cache_hit_tokens") or 0
    )
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    input_tokens = int(
        usage.get("input_tokens")
        if usage.get("input_tokens") is not None
        else usage.get("prompt_tokens") or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        if usage.get("output_tokens") is not None
        else usage.get("completion_tokens") or 0
    )
    # OpenAI-style payloads already separate hit from miss; Anthropic-style ones
    # report the miss portion as input_tokens plus cache writes.
    if usage.get("prompt_cache_miss_tokens") is not None and usage.get("input_tokens") is None:
        cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    else:
        cache_miss = input_tokens + cache_write
    rates = DEEPSEEK_CNY_PRICING
    hit_cost = cache_hit / 1_000_000 * rates["cache_hit_input_per_million"][window]
    miss_cost = cache_miss / 1_000_000 * rates["cache_miss_input_per_million"][window]
    output_cost = output_tokens / 1_000_000 * rates["output_per_million"][window]
    return {
        "currency": "CNY",
        "billing_window": window,
        "cache_hit_input_tokens": cache_hit,
        "cache_miss_input_tokens": cache_miss,
        "output_tokens": output_tokens,
        "web_search_requests": int(
            ((usage.get("server_tool_use") or {}).get("web_search_requests")) or 0
        ),
        "cost_cny": round(hit_cost + miss_cost + output_cost, 6),
        "rate_table_cny_per_million": {
            "cache_hit_input": rates["cache_hit_input_per_million"][window],
            "cache_miss_input": rates["cache_miss_input_per_million"][window],
            "output": rates["output_per_million"][window],
        },
        "pricing_snapshot": rates["snapshot_id"],
        "pricing_source": rates["pricing_source"],
    }


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


def search_provider_config(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "provider": (env.get("DOTA2_SEARCH_PROVIDER") or DEFAULT_PROVIDER).strip().lower(),
        "api_key": (env.get("DOTA2_SEARCH_API_KEY") or "").strip(),
        "api_key_file": (env.get("DOTA2_SEARCH_API_KEY_FILE") or "").strip(),
        "endpoint": (env.get("DOTA2_SEARCH_ENDPOINT") or "").strip(),
        "max_calls": int(env.get("DOTA2_SEARCH_MAX_CALLS") or 5),
        "results": int(env.get("DOTA2_SEARCH_RESULTS") or DEFAULT_RESULTS_PER_SEARCH),
        "allowed_domains": [
            item.strip() for item in (env.get("DOTA2_SEARCH_ALLOWED_DOMAINS") or "").split(",")
            if item.strip()
        ],
        # The hosted provider bills through the DeepSeek key, not a search key.
        "deepseek_api_key_file": (
            env.get("DOTA2_DEEPSEEK_API_KEY_FILE")
            or str(Path(env.get("DOTA2_BASE_DIR", "/opt/dota2-mcp")) / "deepseek-api-key")
        ),
        "deepseek_endpoint": (
            env.get("DEEPSEEK_ANTHROPIC_URL") or DEEPSEEK_ANTHROPIC_URL
        ),
        "deepseek_model": env.get("DEEPSEEK_SEARCH_MODEL") or DEEPSEEK_SEARCH_MODEL,
    }


def resolve_deepseek_key(config: dict) -> str:
    path = Path(config.get("deepseek_api_key_file") or "")
    try:
        value = path.read_text("utf-8").strip()
    except OSError:
        return ""
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            return ""
    except OSError:
        return ""
    return value


# ---------------------------------------------------------------------------
# Provider: DeepSeek hosted search (Anthropic-compatible transport)
# ---------------------------------------------------------------------------


def deepseek_hosted_search(
    query: str,
    config: dict,
    max_results: int = DEFAULT_RESULTS_PER_SEARCH,
    timeout: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
    now: int | None = None,
    holidays=None,
) -> SearchResultSet:
    """Execute one verbatim query through DeepSeek's server-side search tool."""
    import httpx

    from deepseek_review import is_off_peak

    query = (query or "").strip()
    if not query:
        raise WebSearchError("Empty search query")
    if len(query) > MAX_QUERY_CHARS:
        raise WebSearchError(f"Search query exceeds {MAX_QUERY_CHARS} characters")

    key = resolve_deepseek_key(config)
    if not key:
        raise WebSearchNotConfigured(
            f"DeepSeek API key is missing or not 600: {config.get('deepseek_api_key_file')}"
        )

    allowed_domains = [str(item) for item in (config.get("allowed_domains") or [])]
    body = {
        "model": config.get("deepseek_model") or DEEPSEEK_SEARCH_MODEL,
        "max_tokens": 1024,
        "system": SEARCH_EXECUTOR_SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": (
                "原样搜索以下文本：\n"
                f"<exact_query>{query}</exact_query>\n"
                f"目标返回 {max(1, int(max_results))} 条可核验网页结果；"
                "服务能够提供更多合格结果时不要人为截断。"
            ),
        }],
        "tools": [{
            "type": DEEPSEEK_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": 1,
            **({"allowed_domains": allowed_domains} if allowed_domains else {}),
        }],
    }

    started = time.time()
    try:
        response = httpx.post(
            config.get("deepseek_endpoint") or DEEPSEEK_ANTHROPIC_URL,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": DEEPSEEK_ANTHROPIC_VERSION,
                "User-Agent": "AshfuryDota2/1.0 (+preliminary-review)",
            },
            json=body,
            timeout=timeout,
        )
    except Exception as error:
        raise WebSearchError(f"{PROVIDER_DEEPSEEK_HOSTED} 请求失败：{error}") from error

    if response.status_code != 200:
        raise WebSearchError(
            f"{PROVIDER_DEEPSEEK_HOSTED} HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise WebSearchError(f"{PROVIDER_DEEPSEEK_HOSTED} 返回非 JSON") from error

    duration_ms = int((time.time() - started) * 1000)
    blocks = payload.get("content") or []
    executed = [
        (block.get("input") or {}).get("query")
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "server_tool_use"
        and block.get("name") == "web_search"
    ]
    executed = [value for value in executed if isinstance(value, str)]

    warnings: list[str] = []
    if query not in executed:
        # Never accept results that belong to a different query.
        raise SearchQueryRewritten(
            "搜索服务改写或未执行原始搜索词："
            f"发送={query!r} 实际={executed!r}"
        )

    raw_results: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        for item in (block.get("content") or []):
            if not isinstance(item, dict):
                continue
            raw_results.append({
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "snippet": item.get("encrypted_content") or item.get("snippet") or item.get("description") or "",
                "published_at": item.get("page_age"),
                "source_service": PROVIDER_DEEPSEEK_HOSTED,
            })

    if not raw_results:
        warnings.append("托管搜索未返回任何结果")

    results, dropped = validate_results(raw_results, query, allowed_domains)
    usage = payload.get("usage") or {}
    cost = estimate_hosted_search_cost_cny(
        usage, off_peak=is_off_peak(now, holidays)
    )

    return SearchResultSet(
        query=query,
        provider_id=PROVIDER_DEEPSEEK_HOSTED,
        results=results,
        cost=cost,
        raw_response=payload,
        queries_executed=executed,
        duration_ms=duration_ms,
        dropped=dropped,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Providers: standalone search services (kept for swappability)
# ---------------------------------------------------------------------------


def _standalone_search(query: str, config: dict, provider: str) -> SearchResultSet:
    import httpx

    query = (query or "").strip()
    if not query:
        raise WebSearchError("Empty search query")
    max_results = max(1, min(int(config.get("results") or DEFAULT_RESULTS_PER_SEARCH), 10))
    key = config.get("api_key") or ""
    if not key and config.get("api_key_file"):
        try:
            key = Path(config["api_key_file"]).read_text("utf-8").strip()
        except OSError:
            key = ""
    if not key:
        raise WebSearchNotConfigured(
            f"未配置搜索 API Key（DOTA2_SEARCH_API_KEY 或 DOTA2_SEARCH_API_KEY_FILE）"
        )

    started = time.time()
    try:
        if provider == PROVIDER_TAVILY:
            response = httpx.post(
                config.get("endpoint") or "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": max_results,
                      "search_depth": "basic", "include_answer": True},
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            raw_results = [
                {"title": item.get("title"), "url": item.get("url"),
                 "snippet": item.get("content"), "published_at": item.get("published_date"),
                 "source_service": PROVIDER_TAVILY}
                for item in (body.get("results") or [])
            ]
        elif provider == PROVIDER_BRAVE:
            response = httpx.get(
                config.get("endpoint") or "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": key},
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            raw_results = [
                {"title": item.get("title"), "url": item.get("url"),
                 "snippet": item.get("description"), "published_at": item.get("age"),
                 "source_service": PROVIDER_BRAVE}
                for item in ((body.get("web") or {}).get("results") or [])
            ]
        elif provider == PROVIDER_SEARXNG:
            endpoint = config.get("endpoint")
            if not endpoint:
                raise WebSearchError("searxng 需要配置 DOTA2_SEARCH_ENDPOINT")
            response = httpx.get(
                endpoint, params={"q": query, "format": "json"},
                headers={"Authorization": f"Bearer {key}"} if key else {},
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            raw_results = [
                {"title": item.get("title"), "url": item.get("url"),
                 "snippet": item.get("content"), "published_at": item.get("publishedDate"),
                 "source_service": PROVIDER_SEARXNG}
                for item in (body.get("results") or [])[:max_results]
            ]
        else:  # custom
            endpoint = config.get("endpoint")
            if not endpoint:
                raise WebSearchError("custom 提供方需要配置 DOTA2_SEARCH_ENDPOINT")
            response = httpx.post(
                endpoint, json={"query": query, "limit": max_results},
                headers={"Authorization": f"Bearer {key}"} if key else {},
                timeout=DEFAULT_SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            raw = body.get("results") if isinstance(body, dict) else body
            raw_results = [
                {"title": item.get("title"), "url": item.get("url"),
                 "snippet": item.get("snippet") or item.get("content"),
                 "published_at": item.get("published_at"),
                 "source_service": PROVIDER_CUSTOM}
                for item in (raw or [])[:max_results]
            ]
    except WebSearchError:
        raise
    except Exception as error:
        raise WebSearchError(f"{provider} 搜索失败：{error}") from error

    results, dropped = validate_results(
        raw_results, query, config.get("allowed_domains") or []
    )
    return SearchResultSet(
        query=query,
        provider_id=provider,
        results=results,
        cost={"currency": "CNY", "cost_cny": 0.0,
              "note": "该提供方按自身套餐计费，未接入本地价目表"},
        raw_response=body if isinstance(body, dict) else {"results": body},
        queries_executed=[query],
        duration_ms=int((time.time() - started) * 1000),
        dropped=dropped,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PROVIDERS = {
    PROVIDER_DEEPSEEK_HOSTED: lambda query, config, **kw: deepseek_hosted_search(
        query, config,
        max_results=kw.get("max_results", DEFAULT_RESULTS_PER_SEARCH),
        timeout=kw.get("timeout", DEFAULT_SEARCH_TIMEOUT_SECONDS),
        now=kw.get("now"),
        holidays=kw.get("holidays"),
    ),
    PROVIDER_TAVILY: lambda query, config, **kw: _standalone_search(query, config, PROVIDER_TAVILY),
    PROVIDER_BRAVE: lambda query, config, **kw: _standalone_search(query, config, PROVIDER_BRAVE),
    PROVIDER_SEARXNG: lambda query, config, **kw: _standalone_search(query, config, PROVIDER_SEARXNG),
    PROVIDER_CUSTOM: lambda query, config, **kw: _standalone_search(query, config, PROVIDER_CUSTOM),
}


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def provider_ready(provider: str, config: dict | None = None) -> tuple[bool, str]:
    """Whether one provider can execute right now, and why not if it cannot."""
    config = config or search_provider_config()
    if provider not in _PROVIDERS:
        return False, f"未知搜索提供方：{provider}"
    if provider == PROVIDER_DEEPSEEK_HOSTED:
        if resolve_deepseek_key(config):
            return True, "ready"
        return False, (
            "DeepSeek API Key 缺失或权限不是 600："
            f"{config.get('deepseek_api_key_file')}"
        )
    key = config.get("api_key") or ""
    if not key and config.get("api_key_file"):
        try:
            key = Path(config["api_key_file"]).read_text("utf-8").strip()
        except OSError:
            key = ""
    if not key:
        return False, "未配置搜索 API Key（DOTA2_SEARCH_API_KEY 或 DOTA2_SEARCH_API_KEY_FILE）"
    if provider in {PROVIDER_SEARXNG, PROVIDER_CUSTOM} and not config.get("endpoint"):
        return False, f"{provider} 需要配置 DOTA2_SEARCH_ENDPOINT"
    return True, "ready"


def resolve_provider(config: dict | None = None) -> tuple[str, str]:
    """Pick the configured provider, else the first ready one."""
    config = config or search_provider_config()
    configured = config.get("provider") or DEFAULT_PROVIDER
    ready, reason = provider_ready(configured, config)
    if ready:
        return configured, "configured"
    for candidate in (PROVIDER_DEEPSEEK_HOSTED, PROVIDER_TAVILY, PROVIDER_BRAVE,
                      PROVIDER_SEARXNG, PROVIDER_CUSTOM):
        if candidate == configured:
            continue
        candidate_ready, _ = provider_ready(candidate, config)
        if candidate_ready:
            return candidate, f"fallback_from:{configured}"
    return configured, reason


def search_available(config: dict | None = None) -> tuple[bool, str]:
    """Kept for the worker's enabled/available check."""
    config = config or search_provider_config()
    provider, note = resolve_provider(config)
    ready, reason = provider_ready(provider, config)
    if ready:
        suffix = "" if note == "configured" else f"（{note}）"
        return True, f"{provider}{suffix}"
    return False, reason


def web_search(
    query: str,
    config: dict | None = None,
    **kwargs,
) -> SearchResultSet:
    """Execute one search with the resolved provider, verbatim query only."""
    config = config or search_provider_config()
    provider, note = resolve_provider(config)
    ready, reason = provider_ready(provider, config)
    if not ready:
        raise WebSearchNotConfigured(reason)
    result = _PROVIDERS[provider](query, config, **kwargs)
    if note.startswith("fallback_from:"):
        result.warnings.append(f"配置的提供方不可用，已回退到 {provider}")
    return result


def web_search_tool_schema() -> dict:
    return json.loads(json.dumps(WEB_SEARCH_TOOL))


# ---------------------------------------------------------------------------
# Tool-calling loop driven by the model
# ---------------------------------------------------------------------------


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
    """Let the model decide what to search, then execute each query verbatim."""
    config = search_config or search_provider_config()
    ready, reason = search_available(config)
    if not ready:
        raise WebSearchNotConfigured(reason)

    iteration_cap = int(max_iterations or config.get("max_calls") or 5)
    conversation = list(messages)
    citations: list[dict] = []
    searches: list[dict] = []
    rounds: list[dict] = []
    total_usage = {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0,
    }
    total_cost_cny = 0.0

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
                "searches": searches,
                "search_cost_cny": round(total_cost_cny, 6),
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
                total_cost_cny += float((outcome.cost or {}).get("cost_cny") or 0)
                for item in outcome.results:
                    citations.append({
                        **item.to_dict(),
                        "iteration": iteration,
                        "reason": arguments.get("reason"),
                        "provider_id": outcome.provider_id,
                    })
                searches.append(outcome.to_dict(include_raw=False))
                payload = {
                    "query": outcome.query,
                    "provider_id": outcome.provider_id,
                    "results": [item.to_dict() for item in outcome.results],
                    "dropped": outcome.dropped,
                    "warnings": outcome.warnings,
                }
            except (WebSearchError, WebSearchNotConfigured) as error:
                searches.append({"query": query, "error": str(error), "results": []})
                payload = {"query": query, "error": str(error), "results": []}
            conversation.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(payload, ensure_ascii=False),
            })

    return {
        "content": "", "reasoning_content": "", "finish_reason": "tool_call_limit",
        "model": getattr(client, "model", None), "response_id": None,
        "usage": total_usage, "citations": citations, "searches": searches,
        "search_cost_cny": round(total_cost_cny, 6),
        "search_rounds": rounds, "tool_call_count": len(citations),
    }
