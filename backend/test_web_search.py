"""Tests for the pluggable web-search providers.

No network access and no credentials are used: httpx is stubbed and the
DeepSeek key is a temporary 600-permission file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))

import web_search as ws  # noqa: E402
from web_search import (  # noqa: E402
    DEEPSEEK_ANTHROPIC_URL,
    PROVIDER_BRAVE,
    PROVIDER_DEEPSEEK_HOSTED,
    PROVIDER_SEARXNG,
    PROVIDER_TAVILY,
    SearchQueryRewritten,
    WebSearchError,
    WebSearchNotConfigured,
    available_providers,
    check_url_safety,
    deepseek_hosted_search,
    host_within_domains,
    normalize_url,
    provider_ready,
    resolve_provider,
    run_search_loop,
    search_available,
    search_provider_config,
    url_domain,
    validate_results,
    web_search,
    web_search_tool_schema,
)

EXECUTED_QUERY = "Dota 2 7.41 Naga Siren"

ANTHROPIC_REPLY = {
    "id": "msg_1",
    "model": "deepseek-flash",
    "content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "server_tool_use", "id": "stu_1", "name": "web_search",
         "input": {"query": EXECUTED_QUERY}},
        {"type": "web_search_tool_result", "tool_use_id": "stu_1", "content": [
            {"type": "web_search_result", "title": "Naga Siren/Changelogs",
             "url": "https://liquipedia.net/dota2/Naga_Siren/Changelogs", "page_age": None},
            {"type": "web_search_result", "title": "《刀塔2》7.41游戏性更新",
             "url": "https://news.17173.com/content/03252026/140103023.shtml#6",
             "page_age": "2026-03-25"},
            {"type": "web_search_result", "title": "Naga Siren/Changelogs mirror",
             "url": "https://liquipedia.net/dota2/Naga_Siren/Changelogs#1"},
            {"type": "web_search_result", "title": "internal thing",
             "url": "https://127.0.0.1:8080/admin"},
            {"type": "web_search_result", "title": "file scheme",
             "url": "file:///etc/passwd"},
            {"type": "web_search_result", "title": "metadata",
             "url": "https://metadata.google.internal/computeMetadata/v1/"},
        ]},
        {"type": "text", "text": "已完成原样搜索。"},
    ],
    "usage": {"input_tokens": 11735, "cache_creation_input_tokens": 0,
              "cache_read_input_tokens": 384, "output_tokens": 637,
              "server_tool_use": {"web_search_requests": 1}},
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpx:
    calls = []
    payloads = {}
    status = 200

    @classmethod
    def post(cls, url, json=None, headers=None, timeout=None):
        cls.calls.append({"method": "POST", "url": url, "json": json,
                          "headers": headers, "timeout": timeout})
        return FakeResponse(cls.payloads.get("post", {}), cls.status)

    @classmethod
    def get(cls, url, params=None, headers=None, timeout=None):
        cls.calls.append({"method": "GET", "url": url, "params": params,
                          "headers": headers, "timeout": timeout})
        return FakeResponse(cls.payloads.get("get", {}), cls.status)


class SearchTestBase(unittest.TestCase):
    def setUp(self):
        FakeHttpx.calls = []
        FakeHttpx.payloads = {}
        FakeHttpx.status = 200
        self._saved = sys.modules.get("httpx")
        sys.modules["httpx"] = FakeHttpx

        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        key_path = self.root / "deepseek-api-key"
        key_path.write_text("sk-test-key", encoding="utf-8")
        key_path.chmod(0o600)
        self.key_path = key_path
        self.config = {
            "provider": PROVIDER_DEEPSEEK_HOSTED,
            "api_key": "",
            "api_key_file": "",
            "endpoint": "",
            "max_calls": 3,
            "results": 5,
            "allowed_domains": [],
            "deepseek_api_key_file": str(key_path),
            "deepseek_endpoint": DEEPSEEK_ANTHROPIC_URL,
            "deepseek_model": "deepseek-flash",
        }

    def tearDown(self):
        if self._saved is not None:
            sys.modules["httpx"] = self._saved
        else:
            sys.modules.pop("httpx", None)
        self._temporary.cleanup()


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------


class UrlSafetyTest(unittest.TestCase):
    def test_public_https_is_allowed(self):
        for url in ("https://liquipedia.net/dota2/Naga_Siren",
                    "https://news.17173.com/content/03252026/140103023.shtml",
                    "https://www.dota2.com/patches"):
            safe, reason = check_url_safety(url)
            self.assertTrue(safe, f"{url} -> {reason}")

    def test_non_https_schemes_are_rejected(self):
        for url in ("http://example.com/a", "file:///etc/passwd",
                    "javascript:alert(1)", "data:text/html,x", "ftp://example.com"):
            safe, reason = check_url_safety(url)
            self.assertFalse(safe, url)
            self.assertTrue(reason.startswith("scheme_not_allowed") or reason == "missing_scheme",
                            f"{url} -> {reason}")

    def test_internal_hosts_and_private_ips_are_rejected(self):
        cases = {
            "https://127.0.0.1:8080/admin": "private_ip",
            "https://10.0.0.5/x": "private_ip",
            "https://192.168.1.1/": "private_ip",
            "https://169.254.169.254/latest/meta-data/": "private_ip",
            "https://metadata.google.internal/x": "internal_host",
            "https://foo.local/x": "internal_host",
            "https://localhost/x": "internal_host",
            "https://something.onion/x": "tor_host",
        }
        for url, expected in cases.items():
            safe, reason = check_url_safety(url)
            self.assertFalse(safe, url)
            self.assertEqual(reason, expected, url)

    def test_missing_host_or_scheme(self):
        self.assertEqual(check_url_safety("")[1], "empty_url")
        self.assertEqual(check_url_safety("example.com/x")[1], "missing_scheme")

    def test_domain_and_normalization_helpers(self):
        self.assertEqual(url_domain("https://WWW.Example.com:443/a?b=1"), "www.example.com")
        self.assertTrue(host_within_domains("liquipedia.net", ["liquipedia.net"]))
        self.assertTrue(host_within_domains("dota2.liquipedia.net", ["liquipedia.net"]))
        self.assertFalse(host_within_domains("evilliquipedia.net", ["liquipedia.net"]))
        self.assertFalse(host_within_domains("example.com", []))
        self.assertEqual(
            normalize_url("http://www.Example.com/a/?utm_source=x&id=2#frag"),
            "https://example.com/a?id=2",
        )
        self.assertEqual(
            normalize_url("https://Example.com/a/"), "https://example.com/a"
        )


# ---------------------------------------------------------------------------
# Result validation: domains, duplicates, source independence
# ---------------------------------------------------------------------------


class ValidateResultsTest(unittest.TestCase):
    def test_unsafe_urls_are_kept_but_flagged_never_dropped_silently(self):
        results, dropped = validate_results(
            [{"title": "bad", "url": "https://127.0.0.1/x"},
             {"title": "good", "url": "https://example.com/a"}],
            "q",
        )
        self.assertEqual(dropped["unsafe_url"], 1)
        flagged = [r for r in results if r.unsafe_url]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].unsafe_reason, "private_ip")
        self.assertFalse(flagged[0].is_independent_source)

    def test_allowed_domains_filter_results(self):
        results, dropped = validate_results(
            [{"title": "a", "url": "https://liquipedia.net/x"},
             {"title": "b", "url": "https://random-blog.example/y"}],
            "q",
            allowed_domains=["liquipedia.net"],
        )
        self.assertEqual(dropped["domain_not_allowed"], 1)
        self.assertEqual([r.domain for r in results], ["liquipedia.net"])

    def test_duplicate_urls_are_marked_not_counted_as_independent(self):
        results, dropped = validate_results(
            [{"title": "first", "url": "https://example.com/a"},
             {"title": "copy", "url": "https://www.example.com/a#section"},
             {"title": "other", "url": "https://example.com/b"}],
            "q",
        )
        self.assertGreaterEqual(dropped["exact_duplicate"], 1)
        self.assertTrue(results[0].is_independent_source)
        self.assertFalse(results[1].is_independent_source)
        self.assertIsNotNone(results[1].duplicate_of)
        self.assertTrue(results[2].is_independent_source)

    def test_near_duplicate_titles_on_one_domain_are_clustered(self):
        results, dropped = validate_results(
            [{"title": "Naga Siren Changelogs 7.41", "url": "https://a.example/1"},
             {"title": "Naga Siren Changelogs 7.41", "url": "https://a.example/2"}],
            "q",
        )
        self.assertEqual(dropped["near_duplicate"], 1)
        self.assertFalse(results[1].is_independent_source)

    def test_reprint_on_independent_domain_stays_independent(self):
        results, _ = validate_results(
            [{"title": "Patch 7.41 notes", "url": "https://a.example/1"},
             {"title": "Patch 7.41 notes", "url": "https://b.example/2"}],
            "q",
        )
        self.assertTrue(all(r.is_independent_source for r in results))

    def test_invalid_entries_are_counted(self):
        results, dropped = validate_results(
            [{"title": "no url"}, "not a dict", {"title": "ok", "url": "https://e.com/a"}],
            "q",
        )
        self.assertEqual(dropped["invalid"], 2)
        self.assertEqual(len(results), 1)


# ---------------------------------------------------------------------------
# DeepSeek hosted search
# ---------------------------------------------------------------------------


class DeepSeekHostedSearchTest(SearchTestBase):
    def test_query_is_sent_verbatim_and_never_rewritten(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        query = "Dota 2 7.41 Naga Siren"
        outcome = deepseek_hosted_search(query, self.config)
        sent = FakeHttpx.calls[0]["json"]
        self.assertIn(f"<exact_query>{query}</exact_query>", sent["messages"][0]["content"])
        self.assertIn("不得翻译、改写、扩展或删减", sent["system"])
        self.assertEqual(sent["tools"][0]["type"], ws.DEEPSEEK_SEARCH_TOOL_TYPE)
        self.assertEqual(sent["tools"][0]["max_uses"], 1)
        self.assertEqual(outcome.queries_executed, [query])
        self.assertEqual(outcome.query, query)

    def test_uses_the_anthropic_transport_with_required_headers(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        deepseek_hosted_search(EXECUTED_QUERY, self.config)
        call = FakeHttpx.calls[0]
        self.assertEqual(call["url"], DEEPSEEK_ANTHROPIC_URL)
        self.assertEqual(call["headers"]["x-api-key"], "sk-test-key")
        self.assertEqual(call["headers"]["anthropic-version"], ws.DEEPSEEK_ANTHROPIC_VERSION)

    def test_real_results_are_extracted_with_all_required_fields(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        outcome = deepseek_hosted_search("Dota 2 7.41 Naga Siren", self.config)
        first = outcome.results[0]
        self.assertEqual(first.title, "Naga Siren/Changelogs")
        self.assertEqual(first.url, "https://liquipedia.net/dota2/Naga_Siren/Changelogs")
        self.assertEqual(first.query, "Dota 2 7.41 Naga Siren")
        self.assertEqual(first.source_service, PROVIDER_DEEPSEEK_HOSTED)
        self.assertEqual(first.domain, "liquipedia.net")
        self.assertEqual(first.published_at, None)
        second = outcome.results[1]
        self.assertEqual(second.published_at, "2026-03-25")
        self.assertEqual(outcome.provider_id, PROVIDER_DEEPSEEK_HOSTED)

    def test_costs_use_the_official_cny_table_and_count_search_requests(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        outcome = deepseek_hosted_search(EXECUTED_QUERY, self.config)
        cost = outcome.cost
        self.assertEqual(cost["currency"], "CNY")
        self.assertEqual(cost["web_search_requests"], 1)
        self.assertEqual(cost["cache_hit_input_tokens"], 384)
        self.assertEqual(cost["cache_miss_input_tokens"], 11735)
        # 384/1e6*0.02 + 11735/1e6*1 + 637/1e6*4 = 0.01429128 CNY
        self.assertAlmostEqual(cost["cost_cny"], 0.014291, places=6)

    def test_peak_billing_doubles_the_cost(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        import datetime as dt
        peak = int(dt.datetime(2026, 9, 21, 2, tzinfo=dt.timezone.utc).timestamp())
        off = deepseek_hosted_search(EXECUTED_QUERY, self.config, now=peak - 6 * 3600)
        on = deepseek_hosted_search(EXECUTED_QUERY, self.config, now=peak)
        self.assertEqual(on.cost["billing_window"], "peak")
        self.assertEqual(off.cost["billing_window"], "off_peak")
        self.assertAlmostEqual(on.cost["cost_cny"], off.cost["cost_cny"] * 2, places=5)

    def test_raw_response_is_retained(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        outcome = deepseek_hosted_search(EXECUTED_QUERY, self.config)
        self.assertEqual(outcome.raw_response["id"], "msg_1")
        self.assertIn("raw_response", outcome.to_dict(include_raw=True))
        self.assertNotIn("raw_response", outcome.to_dict(include_raw=False))

    def test_rewritten_query_is_rejected_rather_than_accepted(self):
        rewritten = json.loads(json.dumps(ANTHROPIC_REPLY))
        rewritten["content"][1]["input"]["query"] = "Dota 2 7.41 Naga Siren changes"
        FakeHttpx.payloads["post"] = rewritten
        with self.assertRaises(SearchQueryRewritten):
            deepseek_hosted_search("Dota 2 7.41 Naga Siren", self.config)

    def test_no_search_was_executed_is_rejected(self):
        no_search = json.loads(json.dumps(ANTHROPIC_REPLY))
        no_search["content"] = [{"type": "text", "text": "I cannot search."}]
        FakeHttpx.payloads["post"] = no_search
        with self.assertRaises(SearchQueryRewritten):
            deepseek_hosted_search(EXECUTED_QUERY, self.config)

    def test_http_error_is_reported_with_status(self):
        FakeHttpx.status = 402
        FakeHttpx.payloads["post"] = {"error": {"message": "Insufficient Balance"}}
        with self.assertRaises(WebSearchError) as caught:
            deepseek_hosted_search(EXECUTED_QUERY, self.config)
        self.assertIn("402", str(caught.exception))

    def test_missing_or_loose_key_is_refused_before_any_request(self):
        self.key_path.chmod(0o644)
        with self.assertRaises(WebSearchNotConfigured):
            deepseek_hosted_search(EXECUTED_QUERY, self.config)
        self.assertEqual(FakeHttpx.calls, [])

    def test_empty_and_oversized_queries_are_rejected(self):
        with self.assertRaises(WebSearchError):
            deepseek_hosted_search("   ", self.config)
        with self.assertRaises(WebSearchError):
            deepseek_hosted_search("x" * 1001, self.config)

    def test_allowed_domains_are_passed_to_the_tool_and_enforced(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        config = {**self.config, "allowed_domains": ["liquipedia.net"]}
        outcome = deepseek_hosted_search(EXECUTED_QUERY, config)
        self.assertEqual(
            FakeHttpx.calls[0]["json"]["tools"][0]["allowed_domains"], ["liquipedia.net"]
        )
        self.assertTrue(all(
            r.domain == "liquipedia.net" for r in outcome.results if not r.unsafe_url
        ))
        self.assertGreater(outcome.dropped["domain_not_allowed"], 0)
        self.assertTrue(any(r.unsafe_url for r in outcome.results))

    def test_unsafe_and_duplicate_results_are_filtered_from_the_live_reply(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        outcome = deepseek_hosted_search("Dota 2 7.41 Naga Siren", self.config)
        # Unsafe URLs are retained but flagged so the caller can never treat
        # them as usable sources; they are counted in `dropped`.
        self.assertEqual(outcome.dropped["unsafe_url"], 3)
        flagged = {r.url: r.unsafe_reason for r in outcome.results if r.unsafe_url}
        self.assertEqual(flagged.get("https://127.0.0.1:8080/admin"), "private_ip")
        self.assertEqual(flagged.get("file:///etc/passwd"), "scheme_not_allowed:file")
        self.assertEqual(
            flagged.get("https://metadata.google.internal/computeMetadata/v1/"),
            "internal_host",
        )
        self.assertTrue(all(not r.is_independent_source for r in outcome.results if r.unsafe_url))
        # The real duplicate is marked, and the clean remainder is independent.
        duplicates = [r for r in outcome.results if not r.is_independent_source and not r.unsafe_url]
        self.assertEqual(len(duplicates), 1)
        clean = [r for r in outcome.results if not r.unsafe_url and r.is_independent_source]
        self.assertEqual(len(clean), 2)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


class ProviderRegistryTest(SearchTestBase):
    def test_hosted_provider_is_the_default_and_needs_no_search_vendor(self):
        config = search_provider_config({})
        self.assertEqual(config["provider"], PROVIDER_DEEPSEEK_HOSTED)

    def test_registry_exposes_replaceable_providers(self):
        providers = available_providers()
        for expected in (PROVIDER_DEEPSEEK_HOSTED, PROVIDER_TAVILY, PROVIDER_BRAVE,
                         PROVIDER_SEARXNG, "custom"):
            self.assertIn(expected, providers)

    def test_hosted_provider_readiness_follows_the_deepseek_key(self):
        ready, note = provider_ready(PROVIDER_DEEPSEEK_HOSTED, self.config)
        self.assertTrue(ready, note)
        self.key_path.chmod(0o644)
        ready, note = provider_ready(PROVIDER_DEEPSEEK_HOSTED, self.config)
        self.assertFalse(ready)
        self.assertIn("600", note)

    def test_vendor_providers_need_their_own_key(self):
        ready, note = provider_ready(PROVIDER_TAVILY, {**self.config, "provider": PROVIDER_TAVILY})
        self.assertFalse(ready)
        self.assertIn("API Key", note)

    def test_searxng_requires_an_endpoint_even_with_a_key(self):
        ready, note = provider_ready(
            PROVIDER_SEARXNG,
            {**self.config, "provider": PROVIDER_SEARXNG, "api_key": "k"},
        )
        self.assertFalse(ready)
        self.assertIn("ENDPOINT", note)

    def test_unavailable_configured_provider_falls_back_to_a_ready_one(self):
        config = {**self.config, "provider": PROVIDER_TAVILY}
        provider, note = resolve_provider(config)
        self.assertEqual(provider, PROVIDER_DEEPSEEK_HOSTED)
        self.assertEqual(note, f"fallback_from:{PROVIDER_TAVILY}")
        available, detail = search_available(config)
        self.assertTrue(available)
        self.assertIn(PROVIDER_DEEPSEEK_HOSTED, detail)

    def test_no_provider_available_reports_a_concrete_reason(self):
        self.key_path.chmod(0o644)
        config = {**self.config, "provider": PROVIDER_TAVILY}
        available, reason = search_available(config)
        self.assertFalse(available)
        self.assertTrue(reason)
        self.assertNotIn("Brave", reason)

    def test_web_search_uses_the_resolved_provider(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        outcome = web_search(EXECUTED_QUERY, self.config)
        self.assertEqual(outcome.provider_id, PROVIDER_DEEPSEEK_HOSTED)
        self.assertEqual(outcome.query, EXECUTED_QUERY)

    def test_web_search_refuses_when_nothing_is_available(self):
        self.key_path.chmod(0o644)
        with self.assertRaises(WebSearchNotConfigured):
            web_search("q", {**self.config, "provider": PROVIDER_TAVILY})
        self.assertEqual(FakeHttpx.calls, [])

    def test_vendor_provider_results_are_validated_too(self):
        FakeHttpx.payloads["post"] = {"answer": "a", "results": [
            {"title": "t", "url": "https://example.com/a", "content": "c",
             "published_date": "2026-01-01"},
            {"title": "bad", "url": "http://169.254.169.254/", "content": "c"},
        ]}
        config = {**self.config, "provider": PROVIDER_TAVILY, "api_key": "k"}
        outcome = web_search("q", config)
        self.assertEqual(outcome.provider_id, PROVIDER_TAVILY)
        self.assertEqual(outcome.dropped["unsafe_url"], 1)
        self.assertEqual(outcome.results[0].published_at, "2026-01-01")
        self.assertEqual(outcome.queries_executed, ["q"])


# ---------------------------------------------------------------------------
# Tool schema and the model-driven loop
# ---------------------------------------------------------------------------


class ToolSchemaTest(unittest.TestCase):
    def test_tool_schema_is_a_function_tool(self):
        schema = web_search_tool_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "web_search")
        self.assertEqual(schema["function"]["parameters"]["required"], ["query"])

    def test_tool_schema_is_not_shared_mutable_state(self):
        first = web_search_tool_schema()
        first["function"]["name"] = "mutated"
        self.assertEqual(web_search_tool_schema()["function"]["name"], "web_search")


class SearchLoopTest(SearchTestBase):
    class StubClient:
        def __init__(self, responses):
            self.model = "deepseek-flash"
            self._responses = list(responses)
            self.requests = []

        def chat(self, messages, **kwargs):
            self.requests.append({"messages": list(messages), **kwargs})
            return self._responses.pop(0)

    @staticmethod
    def tool_call(query):
        return [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "web_search",
                         "arguments": json.dumps({"query": query, "reason": "版本机制"})},
        }]

    @staticmethod
    def usage(prompt=100, completion=10):
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": prompt}

    def test_loop_executes_every_query_verbatim_and_records_citations(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        query = "Dota 2 7.41 Naga Siren"
        client = self.StubClient([
            {"content": "", "reasoning_content": "", "tool_calls": self.tool_call(query),
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
            {"content": "# 结论\n\n幻象不继承攻击力 [联网]。", "reasoning_content": "",
             "tool_calls": [], "finish_reason": "stop", "usage": self.usage(),
             "model": "deepseek-flash", "response_id": "r2"},
        ])
        result = run_search_loop(client, [{"role": "user", "content": "复盘"}],
                                 max_tokens=1000, temperature=0.2, search_config=self.config)
        sent = FakeHttpx.calls[0]["json"]
        self.assertIn(query, sent["messages"][0]["content"])
        self.assertEqual(result["citations"][0]["url"],
                         "https://liquipedia.net/dota2/Naga_Siren/Changelogs")
        self.assertEqual(result["citations"][0]["query"], query)
        unsafe = [c for c in result["citations"] if c["unsafe_url"]]
        self.assertEqual(len(unsafe), 3)
        self.assertEqual(result["tool_call_count"], len(result["citations"]))
        self.assertEqual(result["searches"][0]["provider_id"], PROVIDER_DEEPSEEK_HOSTED)
        self.assertGreater(result["search_cost_cny"], 0)
        tool_message = [m for m in client.requests[1]["messages"] if m.get("role") == "tool"][0]
        self.assertIn("liquipedia.net", tool_message["content"])
        self.assertIn("is_independent_source", tool_message["content"])

    def test_loop_stops_at_the_iteration_cap(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        client = self.StubClient([
            {"content": "", "reasoning_content": "", "tool_calls": self.tool_call(f"q{i}"),
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": f"r{i}"}
            for i in range(4)
        ])
        result = run_search_loop(client, [{"role": "user", "content": "复盘"}],
                                 max_tokens=1000, temperature=0.2,
                                 search_config={**self.config, "max_calls": 2})
        self.assertEqual(len(client.requests), 3)
        self.assertIsNone(client.requests[-1]["tools"])
        self.assertEqual(len(result["searches"]), 2)

    def test_search_failure_is_reported_to_the_model_without_aborting(self):
        FakeHttpx.status = 500
        client = self.StubClient([
            {"content": "", "reasoning_content": "", "tool_calls": self.tool_call("q"),
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
            {"content": "用本地数据回答", "reasoning_content": "", "tool_calls": [],
             "finish_reason": "stop", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r2"},
        ])
        result = run_search_loop(client, [{"role": "user", "content": "复盘"}],
                                 max_tokens=1000, temperature=0.2, search_config=self.config)
        self.assertEqual(result["content"], "用本地数据回答")
        self.assertEqual(result["citations"], [])
        self.assertIn("error", result["searches"][0])

    def test_unconfigured_search_refuses_to_run_the_loop(self):
        self.key_path.chmod(0o644)
        client = self.StubClient([])
        with self.assertRaises(WebSearchNotConfigured):
            run_search_loop(client, [{"role": "user", "content": "复盘"}],
                            max_tokens=1000, temperature=0.2,
                            search_config={**self.config, "provider": PROVIDER_TAVILY})
        self.assertEqual(client.requests, [])

    def test_plain_answer_without_tool_calls_returns_immediately(self):
        client = self.StubClient([
            {"content": "直接回答", "reasoning_content": "", "tool_calls": [],
             "finish_reason": "stop", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
        ])
        result = run_search_loop(client, [{"role": "user", "content": "复盘"}],
                                 max_tokens=1000, temperature=0.2, search_config=self.config)
        self.assertEqual(result["content"], "直接回答")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(result["search_cost_cny"], 0.0)

    def test_unparseable_tool_arguments_do_not_crash_the_loop(self):
        FakeHttpx.payloads["post"] = ANTHROPIC_REPLY
        client = self.StubClient([
            {"content": "", "reasoning_content": "",
             "tool_calls": [{"id": "c", "type": "function",
                             "function": {"name": "web_search", "arguments": "{not json"}}],
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
            {"content": "done", "reasoning_content": "", "tool_calls": [],
             "finish_reason": "stop", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r2"},
        ])
        result = run_search_loop(client, [{"role": "user", "content": "复盘"}],
                                 max_tokens=1000, temperature=0.2, search_config=self.config)
        self.assertEqual(result["content"], "done")


if __name__ == "__main__":
    unittest.main()
