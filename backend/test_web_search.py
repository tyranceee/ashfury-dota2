"""Unit tests for the hosted web_search function-calling loop.

The DeepSeek API has no server-side search tool, so the server owns the loop.
These tests stub httpx, so no network access and no search credentials are used.
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))

import web_search as ws  # noqa: E402
from web_search import (  # noqa: E402
    WEB_SEARCH_TOOL_NAME,
    WebSearchError,
    WebSearchNotConfigured,
    run_search_loop,
    search_available,
    search_provider_config,
    web_search,
    web_search_tool_schema,
)


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
    """Records calls and returns scripted payloads per provider."""

    calls = []
    payloads = {}

    @classmethod
    def post(cls, url, json=None, headers=None, timeout=None):
        cls.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return FakeResponse(cls.payloads.get("post", {}))

    @classmethod
    def get(cls, url, params=None, headers=None, timeout=None):
        cls.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return FakeResponse(cls.payloads.get("get", {}))


class SearchProviderTest(unittest.TestCase):
    def setUp(self):
        FakeHttpx.calls = []
        FakeHttpx.payloads = {}
        self._saved = sys.modules.get("httpx")
        sys.modules["httpx"] = FakeHttpx

    def tearDown(self):
        if self._saved is not None:
            sys.modules["httpx"] = self._saved
        else:
            sys.modules.pop("httpx", None)

    def test_tool_schema_is_a_function_tool_named_web_search(self):
        schema = web_search_tool_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], WEB_SEARCH_TOOL_NAME)
        self.assertIn("query", schema["function"]["parameters"]["properties"])
        self.assertEqual(schema["function"]["parameters"]["required"], ["query"])

    def test_tool_schema_is_not_shared_mutable_state(self):
        first = web_search_tool_schema()
        first["function"]["name"] = "mutated"
        self.assertEqual(web_search_tool_schema()["function"]["name"], WEB_SEARCH_TOOL_NAME)

    def test_config_reads_environment(self):
        config = search_provider_config({
            "DOTA2_SEARCH_PROVIDER": "Brave",
            "DOTA2_SEARCH_API_KEY": "k",
            "DOTA2_SEARCH_MAX_CALLS": "3",
            "DOTA2_SEARCH_RESULTS": "7",
        })
        self.assertEqual(config["provider"], "brave")
        self.assertEqual(config["max_calls"], 3)
        self.assertEqual(config["results"], 7)

    def test_missing_key_is_reported_not_guessed(self):
        available, reason = search_available({"provider": "tavily", "api_key": ""})
        self.assertFalse(available)
        self.assertIn("API Key", reason)

    def test_unknown_provider_is_rejected(self):
        available, reason = search_available({"provider": "not-a-provider", "api_key": "k"})
        self.assertFalse(available)
        self.assertIn("不支持的搜索提供方", reason)

    def test_unconfigured_search_raises_instead_of_silently_returning_nothing(self):
        with self.assertRaises(WebSearchNotConfigured):
            web_search("dota", {"provider": "tavily", "api_key": ""})

    def test_empty_query_is_rejected(self):
        with self.assertRaises(WebSearchError):
            web_search("  ", {"provider": "tavily", "api_key": "k"})

    def test_tavily_results_are_normalized(self):
        FakeHttpx.payloads["post"] = {
            "answer": "幻象不继承攻击力。",
            "results": [
                {"title": "机制说明", "url": "https://example.com/a", "content": "正文 A"},
                {"title": "补丁", "url": "https://example.com/b", "content": "正文 B"},
            ],
        }
        outcome = web_search("幻象 继承", {"provider": "tavily", "api_key": "k", "results": 5})
        self.assertEqual(outcome["provider"], "tavily")
        self.assertEqual(outcome["result_count"], 2)
        self.assertEqual(outcome["answer"], "幻象不继承攻击力。")
        self.assertEqual(outcome["results"][0]["url"], "https://example.com/a")
        self.assertEqual(FakeHttpx.calls[0]["json"]["api_key"], "k")

    def test_brave_uses_header_auth_and_normalizes(self):
        FakeHttpx.payloads["get"] = {
            "web": {"results": [{"title": "t", "url": "https://example.com", "description": "d"}]}
        }
        outcome = web_search("x", {"provider": "brave", "api_key": "secret", "results": 5})
        self.assertEqual(outcome["result_count"], 1)
        headers = FakeHttpx.calls[0]["headers"]
        self.assertEqual(headers["X-Subscription-Token"], "secret")

    def test_searxng_requires_an_endpoint(self):
        with self.assertRaises(WebSearchError):
            web_search("x", {"provider": "searxng", "api_key": "k"})

    def test_provider_failure_becomes_a_web_search_error(self):
        class Exploding:
            @staticmethod
            def post(*args, **kwargs):
                raise RuntimeError("connection reset")

        sys.modules["httpx"] = Exploding
        with self.assertRaises(WebSearchError):
            web_search("x", {"provider": "tavily", "api_key": "k"})

    def test_long_snippets_are_truncated(self):
        FakeHttpx.payloads["post"] = {
            "results": [{"title": "t", "url": "u", "content": "x" * 5000}]
        }
        outcome = web_search("q", {"provider": "tavily", "api_key": "k"})
        self.assertEqual(len(outcome["results"][0]["snippet"]), ws.MAX_SNIPPET_CHARS)


class SearchLoopTest(unittest.TestCase):
    class StubClient:
        def __init__(self, responses):
            self.model = "deepseek-flash"
            self._responses = list(responses)
            self.requests = []

        def chat(self, messages, **kwargs):
            self.requests.append({"messages": list(messages), **kwargs})
            return self._responses.pop(0)

    def setUp(self):
        FakeHttpx.calls = []
        FakeHttpx.payloads = {
            "post": {
                "results": [
                    {"title": "官方机制", "url": "https://example.com/mech", "content": "内容"}
                ]
            }
        }
        self._saved = sys.modules.get("httpx")
        sys.modules["httpx"] = FakeHttpx

    def tearDown(self):
        if self._saved is not None:
            sys.modules["httpx"] = self._saved
        else:
            sys.modules.pop("httpx", None)

    @staticmethod
    def tool_call(query="幻象 继承 攻击力"):
        return [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": WEB_SEARCH_TOOL_NAME,
                "arguments": json.dumps({"query": query, "reason": "版本机制"}),
            },
        }]

    @staticmethod
    def usage(prompt=100, completion=10):
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": prompt,
        }

    def test_loop_executes_the_search_and_returns_citations(self):
        client = self.StubClient([
            {"content": "", "reasoning_content": "", "tool_calls": self.tool_call(),
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
            {"content": "# 结论\n\n幻象不继承攻击力 [联网]。", "reasoning_content": "",
             "tool_calls": [], "finish_reason": "stop", "usage": self.usage(),
             "model": "deepseek-flash", "response_id": "r2"},
        ])
        result = run_search_loop(
            client,
            [{"role": "user", "content": "复盘"}],
            max_tokens=1000,
            temperature=0.2,
            search_config={"provider": "tavily", "api_key": "k", "results": 5, "max_calls": 3},
        )
        self.assertIn("幻象不继承攻击力", result["content"])
        self.assertEqual(result["tool_call_count"], 1)
        self.assertEqual(result["citations"][0]["url"], "https://example.com/mech")
        self.assertEqual(result["citations"][0]["query"], "幻象 继承 攻击力")
        # Usage is summed across both round trips.
        self.assertEqual(result["usage"]["prompt_tokens"], 200)
        # The tool result is fed back as a tool message.
        tool_messages = [
            message for message in client.requests[1]["messages"] if message.get("role") == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_1")
        self.assertIn("example.com/mech", tool_messages[0]["content"])
        # Tools stay available for the whole loop; the model simply stopped
        # calling them on the second round.
        self.assertEqual(len(client.requests), 2)
        self.assertIsNotNone(client.requests[1]["tools"])

    def test_loop_stops_at_the_iteration_cap(self):
        always_calls = [
            {"content": "", "reasoning_content": "", "tool_calls": self.tool_call(),
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": f"r{i}"}
            for i in range(4)
        ]
        client = self.StubClient(always_calls)
        result = run_search_loop(
            client,
            [{"role": "user", "content": "复盘"}],
            max_tokens=1000,
            temperature=0.2,
            search_config={"provider": "tavily", "api_key": "k", "results": 5, "max_calls": 2},
        )
        # max_calls=2 means two tool rounds then one final tool-less round.
        self.assertEqual(len(client.requests), 3)
        self.assertIsNone(client.requests[-1]["tools"])
        self.assertIsNotNone(result["finish_reason"])

    def test_unparseable_tool_arguments_do_not_crash_the_loop(self):
        client = self.StubClient([
            {"content": "", "reasoning_content": "",
             "tool_calls": [{"id": "c", "type": "function",
                             "function": {"name": WEB_SEARCH_TOOL_NAME, "arguments": "{not json"}}],
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
            {"content": "done", "reasoning_content": "", "tool_calls": [],
             "finish_reason": "stop", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r2"},
        ])
        result = run_search_loop(
            client,
            [{"role": "user", "content": "复盘"}],
            max_tokens=1000,
            temperature=0.2,
            search_config={"provider": "tavily", "api_key": "k", "results": 5, "max_calls": 2},
        )
        self.assertEqual(result["content"], "done")

    def test_search_failure_is_reported_to_the_model_without_aborting(self):
        sys.modules["httpx"] = types.SimpleNamespace(
            post=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
            get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        client = self.StubClient([
            {"content": "", "reasoning_content": "", "tool_calls": self.tool_call(),
             "finish_reason": "tool_calls", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
            {"content": "用本地数据回答", "reasoning_content": "", "tool_calls": [],
             "finish_reason": "stop", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r2"},
        ])
        result = run_search_loop(
            client,
            [{"role": "user", "content": "复盘"}],
            max_tokens=1000,
            temperature=0.2,
            search_config={"provider": "tavily", "api_key": "k", "results": 5, "max_calls": 2},
        )
        self.assertEqual(result["content"], "用本地数据回答")
        self.assertEqual(result["citations"], [])
        tool_message = [
            message for message in client.requests[1]["messages"] if message.get("role") == "tool"
        ][0]
        self.assertIn("error", tool_message["content"])

    def test_unconfigured_search_refuses_to_run_the_loop(self):
        client = self.StubClient([])
        with self.assertRaises(WebSearchNotConfigured):
            run_search_loop(
                client,
                [{"role": "user", "content": "复盘"}],
                max_tokens=1000,
                temperature=0.2,
                search_config={"provider": "tavily", "api_key": ""},
            )
        self.assertEqual(client.requests, [])

    def test_plain_answer_without_tool_calls_returns_immediately(self):
        client = self.StubClient([
            {"content": "直接回答", "reasoning_content": "", "tool_calls": [],
             "finish_reason": "stop", "usage": self.usage(), "model": "deepseek-flash",
             "response_id": "r1"},
        ])
        result = run_search_loop(
            client,
            [{"role": "user", "content": "复盘"}],
            max_tokens=1000,
            temperature=0.2,
            search_config={"provider": "tavily", "api_key": "k", "results": 5, "max_calls": 3},
        )
        self.assertEqual(result["content"], "直接回答")
        self.assertEqual(result["citations"], [])
        self.assertEqual(len(client.requests), 1)


if __name__ == "__main__":
    unittest.main()
