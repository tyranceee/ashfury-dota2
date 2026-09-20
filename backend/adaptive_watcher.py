#!/usr/bin/env python3
"""Adaptive ashfury Dota monitor: Steam presence + OpenDota basic data/parse trigger."""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx


ACCOUNT_ID = int(os.environ.get("DOTA_ACCOUNT_ID", "212121467"))
STEAM_ID64 = os.environ.get("DOTA_STEAM_ID64", "76561198172387195")
STEAM_WEB_API_KEY = os.environ.get("STEAM_WEB_API_KEY", "")

PRESENCE_INTERVAL = int(os.environ.get("PRESENCE_POLL_SECONDS", "600"))
RESULT_INTERVAL = int(os.environ.get("RESULT_POLL_SECONDS", "10"))
OFFLINE_RESULT_INTERVAL = int(os.environ.get(
    "OFFLINE_RESULT_POLL_SECONDS", str(PRESENCE_INTERVAL)
))
PARSE_CHECK_INTERVAL = int(os.environ.get("PARSE_CHECK_SECONDS", "600"))
LOCAL_PARSE_GRACE_SECONDS = int(os.environ.get("LOCAL_PARSE_GRACE_SECONDS", "3600"))

BASE_DIR = Path(os.environ.get("DOTA_BASE_DIR", "/opt/dota2-mcp"))
STATE_FILE = BASE_DIR / "adaptive_monitor_state.json"
INDEX_FILE = BASE_DIR / "matches_index.json"
DATA_DIR = BASE_DIR / "data"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
LOCAL_PARSE_FILES = {
    "replay-events.ndjson",
    "combat-log.txt",
    "combat-log.ndjson",
    "manifest.json",
}

OD_BASE = "https://api.opendota.com/api"
STEAM_PRESENCE_URL = (
    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
)
USER_AGENT = "Ashfury-Dota-Adaptive-Monitor/1.0"

DEFAULT_STATE = {
    "dota_online": False,
    "last_presence_check_at": None,
    "last_result_check_at": None,
    "last_processed_match_id": None,
    "last_discovered_match_id": None,
    "opendota_backoff_until": 0,
}


def log(message):
    print("[{:%Y-%m-%d %H:%M:%S}] {}".format(datetime.now(), message), flush=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default.copy() if isinstance(default, dict) else default


def save_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_state():
    state = DEFAULT_STATE.copy()
    state.update(load_json(STATE_FILE, {}))
    return state


def is_newer_match(candidate, baseline):
    return baseline is None or int(candidate) > int(baseline)


def is_parsed(match):
    return (
        isinstance(match.get("players"), list)
        and len(match.get("players")) == 10
        and match.get("version") is not None
        and isinstance(match.get("teamfights"), list)
        and len(match.get("teamfights")) > 0
        and isinstance(match.get("objectives"), list)
        and len(match.get("objectives")) > 0
        and isinstance(match.get("radiant_gold_adv"), list)
        and len(match.get("radiant_gold_adv")) > 0
        and isinstance(match.get("radiant_xp_adv"), list)
        and len(match.get("radiant_xp_adv")) > 0
    )


def local_parse_ready(match_id):
    directory = ARTIFACTS_DIR / str(match_id)
    return directory.is_dir() and LOCAL_PARSE_FILES.issubset(
        {path.name for path in directory.iterdir() if path.is_file()}
    )


def extract_job_id(response):
    if not isinstance(response, dict):
        return None
    candidates = [response.get("jobId"), response.get("job_id"), response.get("id")]
    if isinstance(response.get("job"), dict):
        job = response["job"]
        candidates.extend([job.get("jobId"), job.get("job_id"), job.get("id")])
    return next((value for value in candidates if value is not None), None)


def build_summary(summary, old=None):
    old = old or {}
    return {
        "match_id": int(summary["match_id"]),
        "start_time": summary.get("start_time"),
        "duration": summary.get("duration"),
        "hero_id": summary.get("hero_id"),
        "kills": summary.get("kills"),
        "deaths": summary.get("deaths"),
        "assists": summary.get("assists"),
        "player_slot": summary.get("player_slot"),
        "radiant_win": summary.get("radiant_win"),
        "parsed": old.get("parsed", False),
        "parse_status": old.get("parse_status", "unknown"),
        "parse_source": old.get("parse_source"),
        "parse_discovered_at": old.get("parse_discovered_at"),
        "parse_requested": old.get("parse_requested", False),
        "parse_requested_at": old.get("parse_requested_at"),
        "parse_job_id": old.get("parse_job_id"),
        "last_parse_check": old.get("last_parse_check"),
    }


class OpenDotaClient(object):
    def __init__(self, client=None):
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self.last_remaining_minute = None
        self.last_remaining_day = None

    def _remember_limits(self, response):
        self.last_remaining_minute = response.headers.get(
            "x-rate-limit-remaining-minute"
        )
        self.last_remaining_day = response.headers.get("x-rate-limit-remaining-day")

    def _request(self, method, path):
        response = self.client.request(method, OD_BASE + path)
        self._remember_limits(response)
        response.raise_for_status()
        return response.json() if response.text else {}

    def latest_match(self):
        matches = self._request(
            "GET", "/players/{}/matches?limit=1".format(ACCOUNT_ID)
        )
        return matches[0] if matches else None

    def recent_matches(self, limit=100):
        return self._request(
            "GET", "/players/{}/matches?limit={}".format(ACCOUNT_ID, limit)
        )

    def match(self, match_id):
        return self._request("GET", "/matches/{}".format(match_id))

    def request_parse(self, match_id):
        return self._request("POST", "/request/{}".format(match_id))


class SteamPresenceClient(object):
    def __init__(self, api_key, client=None):
        if not api_key:
            raise RuntimeError("STEAM_WEB_API_KEY is required")
        self.api_key = api_key
        self.client = client or httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def is_dota_online(self):
        response = self.client.get(
            STEAM_PRESENCE_URL,
            params={"key": self.api_key, "steamids": STEAM_ID64},
        )
        response.raise_for_status()
        players = response.json().get("response", {}).get("players", [])
        if not players:
            raise RuntimeError("Steam presence response did not contain the target player")
        return str(players[0].get("gameid", "")) == "570"


class AdaptiveWatcher(object):
    def __init__(self, presence, opendota, clock=None, sleeper=None):
        self.presence = presence
        self.opendota = opendota
        self.clock = clock or time.time
        self.sleeper = sleeper or time.sleep
        self.state = load_state()
        self._seed_baseline_from_local_index()
        self.next_presence_at = 0
        self.next_result_at = 0
        self.next_parse_check_at = 0

    def save_state(self):
        save_json_atomic(STATE_FILE, self.state)

    def _seed_baseline_from_local_index(self):
        if self.state.get("last_processed_match_id") is not None:
            return
        index = load_json(INDEX_FILE, {})
        if not index:
            return
        latest = max(
            index.values(),
            key=lambda item: (
                int(item.get("start_time") or 0),
                int(item.get("match_id") or 0),
            ),
        )
        self.state["last_processed_match_id"] = str(latest["match_id"])
        self.save_state()
        log("以本地索引 {} 预置 Match ID 基线".format(latest["match_id"]))

    def check_presence(self):
        online = self.presence.is_dota_online()
        changed = online != bool(self.state.get("dota_online"))
        self.state["dota_online"] = online
        self.state["last_presence_check_at"] = int(self.clock())
        self.save_state()
        log("Dota 在线状态：{}{}".format("在线" if online else "离线", "（变化）" if changed else ""))
        if online and changed:
            self.next_result_at = 0

    def _save_recent_index(self, matches):
        old_index = load_json(INDEX_FILE, {})
        new_index = {}
        for summary in sorted(
            matches, key=lambda item: item.get("start_time", 0), reverse=True
        )[:100]:
            key = str(summary["match_id"])
            new_index[key] = build_summary(summary, old_index.get(key, {}))
        save_json_atomic(INDEX_FILE, new_index)
        return new_index

    def _register_new_match(self, match_id, summary):
        matches = self.opendota.recent_matches(100)
        index = self._save_recent_index(matches)
        key = str(match_id)
        entry = index.get(key, build_summary(summary))
        now = int(self.clock())
        entry["parse_discovered_at"] = now
        entry["last_parse_check"] = now

        if local_parse_ready(match_id):
            entry["parsed"] = True
            entry["parse_status"] = "parsed"
            entry["parse_source"] = "dota_replay_desk"
            log("{} 已存在 DotaReplayDesk 结果，跳过 OpenDota".format(match_id))
        else:
            entry["parsed"] = False
            entry["parse_status"] = "waiting_local"
            entry["parse_source"] = None
            log("{} 已登记，保留 {} 秒本地解析窗口".format(
                match_id, LOCAL_PARSE_GRACE_SECONDS
            ))

        index[key] = entry
        save_json_atomic(INDEX_FILE, index)

    def check_result(self):
        latest = self.opendota.latest_match()
        self.state["last_result_check_at"] = int(self.clock())
        if not latest:
            self.save_state()
            return

        match_id = str(latest["match_id"])
        baseline = self.state.get("last_processed_match_id")
        if baseline is None:
            self.state["last_processed_match_id"] = match_id
            self.save_state()
            log("以 {} 建立 Match ID 基线".format(match_id))
            return

        if not is_newer_match(match_id, baseline):
            self.save_state()
            return

        self.state["last_discovered_match_id"] = match_id
        self.save_state()
        log("发现新比赛 {}，登记统一解析状态".format(match_id))
        self._register_new_match(match_id, latest)
        self.state["last_processed_match_id"] = match_id
        self.save_state()

    def check_pending_parse(self):
        index = load_json(INDEX_FILE, {})
        pending = sorted(
            [
                item for item in index.values()
                if not item.get("parsed")
                and item.get("parse_status")
                in {"waiting_local", "requested", "waiting", "unavailable"}
            ],
            key=lambda item: item.get("start_time", 0),
            reverse=True,
        )[:3]
        changed = False
        for entry in pending:
            match_id = entry["match_id"]
            now = int(self.clock())

            if local_parse_ready(match_id):
                entry["parsed"] = True
                entry["parse_status"] = "parsed"
                entry["parse_source"] = "dota_replay_desk"
                entry["last_parse_check"] = now
                changed = True
                log("{} 本地解析已就绪，跳过 OpenDota".format(match_id))
                continue

            discovered_at = int(
                entry.get("parse_discovered_at")
                or entry.get("start_time")
                or now
            )
            if not entry.get("parse_requested") and (
                now - discovered_at < LOCAL_PARSE_GRACE_SECONDS
            ):
                entry["parse_status"] = "waiting_local"
                entry["last_parse_check"] = now
                changed = True
                continue

            full = self.opendota.match(match_id)
            entry["last_parse_check"] = now
            if is_parsed(full):
                entry["parsed"] = True
                entry["parse_status"] = "parsed"
                entry["parse_source"] = "opendota"
                save_json_atomic(DATA_DIR / "{}.json".format(match_id), full)
                changed = True
                log("{} 解析完成并缓存".format(match_id))
                continue

            if not entry.get("parse_requested"):
                result = self.opendota.request_parse(match_id)
                entry["parse_requested"] = True
                entry["parse_requested_at"] = now
                entry["parse_job_id"] = extract_job_id(result)
                entry["parse_status"] = "requested"
                log("{} 已提交唯一一次 OpenDota 解析请求，job={}".format(
                    match_id, entry.get("parse_job_id")
                ))
            else:
                entry["parse_status"] = "waiting"
                log("{} OpenDota 仍在解析，10 分钟后再同步".format(match_id))
            changed = True

        if changed:
            save_json_atomic(INDEX_FILE, index)

    def run_due(self):
        now = self.clock()
        if now >= self.next_presence_at:
            try:
                self.check_presence()
            except Exception as error:
                log("在线状态查询失败：{}".format(error))
            self.next_presence_at = now + PRESENCE_INTERVAL

        if now >= self.next_result_at:
            result_interval = (
                RESULT_INTERVAL
                if self.state.get("dota_online")
                else OFFLINE_RESULT_INTERVAL
            )
            try:
                self.check_result()
                self.next_result_at = now + result_interval
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                delay = 60 if status == 429 else result_interval
                self.next_result_at = now + delay
                log("比赛结果查询失败 HTTP {}，{} 秒后重试".format(status, delay))
            except Exception as error:
                self.next_result_at = now + result_interval
                log("比赛结果查询失败：{}".format(error))

        if now >= self.next_parse_check_at:
            try:
                self.check_pending_parse()
            except Exception as error:
                log("解析状态复查失败：{}".format(error))
            self.next_parse_check_at = now + PARSE_CHECK_INTERVAL

    def run_forever(self):
        log("启动自适应监控：在线状态 {} 秒，在线结果 {} 秒，离线兜底结果 {} 秒，解析状态 {} 秒".format(
            PRESENCE_INTERVAL, RESULT_INTERVAL, OFFLINE_RESULT_INTERVAL,
            PARSE_CHECK_INTERVAL
        ))
        while True:
            self.run_due()
            self.sleeper(1)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    watcher = AdaptiveWatcher(
        SteamPresenceClient(STEAM_WEB_API_KEY),
        OpenDotaClient(),
    )
    watcher.run_forever()


if __name__ == "__main__":
    main()
