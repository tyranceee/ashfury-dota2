"""DeepSeek-backed preliminary review: prompt store, cost-aware scheduler, jobs.

This module owns four concerns:

1. **Off-peak window arithmetic.** DeepSeek bills off-peak rates at half of the
   peak rates. Peak hours are ``01:00-04:00`` and ``06:00-10:00`` UTC, Monday
   through Friday, *excluding* Chinese public holidays. Everything else is
   off-peak. All comparisons are done in UTC; Beijing time is only used for
   display.

2. **The auto-review toggle.** ``enable``/``disable`` is the only switching the
   Owner needs. When enabled, the scheduler only ever starts work during an
   off-peak window. When a long review would cross into a peak window, it is
   paused and resumed in the next off-peak window instead of being billed at
   the peak rate.

3. **Prompt storage.** The Owner pastes a Markdown prompt in the web UI and it
   is stored verbatim as an immutable revision. Reviews record which revision
   produced them.

4. **Preliminary review jobs.** Batches of the most recently parsed matches are
   sent to DeepSeek in a single request because the parsed JSON already carries
   the cross-match context a per-match split would lose.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
from pathlib import Path

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_API_KEY_FILE = "deepseek-api-key"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 900
DEFAULT_MAX_OUTPUT_TOKENS = 32000
DEFAULT_BATCH_SIZE = 3
MAX_BATCH_SIZE = 10
MAX_PROMPT_CHARS = 200000
DEFAULT_RETRY_BACKOFF_SECONDS = (60, 300, 900)
MAX_JOB_ATTEMPTS = 5

# Peak windows in UTC (start_hour inclusive, end_hour exclusive), weekdays only.
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))
PEAK_WEEKDAYS = (0, 1, 2, 3, 4)  # Monday..Friday
BEIJING_OFFSET_HOURS = 8

# Conservative built-in calendar of Chinese public holidays. DeepSeek treats a
# full Chinese public holiday as off-peak, so the dates below only ever widen
# the off-peak window. Unknown future dates stay conservatively "peak" until
# the official notice is added here or via the calendar file.
BUILTIN_HOLIDAY_CALENDAR = {
    "2026": [
        "2026-01-01", "2026-01-02", "2026-01-03",
        "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18",
        "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22",
        "2026-02-23",
        "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
        "2026-06-19", "2026-06-20", "2026-06-21",
        "2026-09-25", "2026-09-26", "2026-09-27",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
    ],
}

PROMPT_SCHEMA_VERSION = "ashfury.deepseek-prompt.v1"
JOB_SCHEMA_VERSION = "ashfury.deepseek-review.v1"
OUTPUT_SCHEMA_VERSION = "ashfury.preliminary-review.v1"


class DeepSeekConfigError(Exception):
    pass


class DeepSeekRequestError(Exception):
    pass


# --------------------------------------------------------------------------
# Off-peak window arithmetic
# --------------------------------------------------------------------------


def load_holiday_calendar(calendar_path: Path | str | None) -> set[str]:
    dates = {day for days in BUILTIN_HOLIDAY_CALENDAR.values() for day in days}
    if calendar_path is None:
        return dates
    try:
        raw = json.loads(Path(calendar_path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return dates
    if isinstance(raw, dict):
        for year_days in raw.values():
            if isinstance(year_days, list):
                dates.update(str(day) for day in year_days)
    elif isinstance(raw, list):
        dates.update(str(day) for day in raw)
    return dates


def _as_utc(now) -> dt.datetime:
    if now is None:
        return dt.datetime.now(dt.timezone.utc)
    if isinstance(now, dt.datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=dt.timezone.utc)
        return now.astimezone(dt.timezone.utc)
    return dt.datetime.fromtimestamp(int(now), dt.timezone.utc)


def is_off_peak(now=None, holidays: set[str] | None = None) -> bool:
    """True when DeepSeek bills the off-peak rate at this instant."""
    moment = _as_utc(now)
    if moment.weekday() not in PEAK_WEEKDAYS:
        return True
    if holidays and moment.strftime("%Y-%m-%d") in holidays:
        return True
    for start_hour, end_hour in PEAK_WINDOWS_UTC:
        if start_hour <= moment.hour < end_hour:
            return False
    return True


def next_window_boundary(now=None, holidays: set[str] | None = None) -> tuple[dt.datetime, bool]:
    """Return the next instant the billing state changes and the state after it.

    The returned boolean is the off-peak state that begins at that instant.
    """
    moment = _as_utc(now)
    if is_off_peak(moment, holidays):
        # Walk forward minute by minute is wasteful; iterate hour buckets of the
        # current day and then the next seven days, which is bounded and exact
        # because every peak window starts and ends on an hour boundary.
        candidate = moment.replace(minute=0, second=0, microsecond=0)
        for _ in range(24 * 8):
            candidate += dt.timedelta(hours=1)
            if not is_off_peak(candidate, holidays):
                return candidate, False
        return moment + dt.timedelta(days=7), False
    candidate = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        candidate += dt.timedelta(hours=1)
        if is_off_peak(candidate, holidays):
            return candidate, True
    return moment + dt.timedelta(days=7), True


def next_off_peak_start(now=None, holidays: set[str] | None = None) -> dt.datetime:
    """First instant at or after ``now`` that is billed off-peak."""
    moment = _as_utc(now)
    if is_off_peak(moment, holidays):
        return moment
    probe = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        probe += dt.timedelta(hours=1)
        if is_off_peak(probe, holidays):
            return probe
    return moment + dt.timedelta(days=7)


def next_off_peak_runway(
    now=None,
    minimum_seconds: int = 600,
    holidays: set[str] | None = None,
) -> dt.datetime:
    """Next instant inside an off-peak window that can still fit ``minimum_seconds``.

    A ten-minute sliver between two peak windows is off-peak but useless for a
    review, so this skips it and returns the start of the next window that is
    long enough. Windows are hour-aligned, so probing hour boundaries is exact.
    """
    moment = _as_utc(now)
    if (
        is_off_peak(moment, holidays)
        and off_peak_seconds_remaining(moment, holidays) >= minimum_seconds
    ):
        return moment
    probe = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        probe += dt.timedelta(hours=1)
        if (
            is_off_peak(probe, holidays)
            and off_peak_seconds_remaining(probe, holidays) >= minimum_seconds
        ):
            return probe
    return moment + dt.timedelta(days=7)


def off_peak_seconds_remaining(now=None, holidays: set[str] | None = None) -> int:
    """Seconds of continuous off-peak time left; 0 when currently in peak."""
    moment = _as_utc(now)
    if not is_off_peak(moment, holidays):
        return 0
    boundary, state = next_window_boundary(moment, holidays)
    if not state:
        return max(0, int((boundary - moment).total_seconds()))
    return 7 * 24 * 3600


def _beijing(moment: dt.datetime) -> dt.datetime:
    return moment.astimezone(
        dt.timezone(dt.timedelta(hours=BEIJING_OFFSET_HOURS))
    )


def schedule_snapshot(now=None, holidays: set[str] | None = None) -> dict:
    moment = _as_utc(now)
    boundary, state = next_window_boundary(moment, holidays)
    return {
        "now_utc": moment.isoformat(),
        "now_beijing": _beijing(moment).isoformat(),
        "off_peak_now": is_off_peak(moment, holidays),
        "next_change_utc": boundary.isoformat(),
        "next_change_beijing": _beijing(boundary).isoformat(),
        "next_change_is_off_peak": state,
        "off_peak_seconds_remaining": off_peak_seconds_remaining(moment, holidays),
        "peak_windows_utc": [f"{start:02d}:00-{end:02d}:00" for start, end in PEAK_WINDOWS_UTC],
        "peak_windows_beijing": [
            f"{(start + BEIJING_OFFSET_HOURS) % 24:02d}:00-{(end + BEIJING_OFFSET_HOURS) % 24:02d}:00"
            for start, end in PEAK_WINDOWS_UTC
        ],
        "peak_days": "Monday-Friday",
        "off_peak_rule": (
            "Off-peak is half price. Peak is 01:00-04:00 and 06:00-10:00 UTC on "
            "weekdays, excluding Chinese public holidays; weekends and holidays "
            "are off-peak all day."
        ),
        "holiday_count_loaded": len(holidays or ()),
    }


# --------------------------------------------------------------------------
# Prompt revisions
# --------------------------------------------------------------------------


def prompt_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PromptStore:
    """Immutable, revision-numbered Markdown prompts pasted by the Owner."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return {"schema_version": PROMPT_SCHEMA_VERSION, "revisions": [], "active_revision": None}
        except (OSError, json.JSONDecodeError):
            return {"schema_version": PROMPT_SCHEMA_VERSION, "revisions": [], "active_revision": None}
        if not isinstance(data, dict):
            return {"schema_version": PROMPT_SCHEMA_VERSION, "revisions": [], "active_revision": None}
        data.setdefault("revisions", [])
        data.setdefault("active_revision", None)
        return data

    def _save(self, data: dict) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def create_revision(self, content: str, note: str = "", title: str = "") -> dict:
        content = content if isinstance(content, str) else ""
        if not content.strip():
            raise ValueError("Prompt markdown is empty")
        if len(content) > MAX_PROMPT_CHARS:
            raise ValueError(f"Prompt markdown exceeds {MAX_PROMPT_CHARS} characters")
        digest = prompt_digest(content)
        with self._lock:
            data = self._load()
            for revision in data["revisions"]:
                if revision.get("sha256") == digest:
                    data["active_revision"] = revision["revision"]
                    self._save(data)
                    return {**revision, "reused": True}
            revision_number = max(
                (int(item.get("revision") or 0) for item in data["revisions"]),
                default=0,
            ) + 1
            record = {
                "revision": revision_number,
                "title": (title or "").strip()[:120],
                "note": (note or "").strip()[:500],
                "sha256": digest,
                "char_count": len(content),
                "line_count": content.count("\n") + 1,
                "created_at": int(time.time()),
                "created_by": "owner",
                "content": content,
            }
            data["revisions"].append(record)
            data["active_revision"] = revision_number
            self._save(data)
            return {**record, "reused": False}

    def list_revisions(self, include_content: bool = False) -> dict:
        with self._lock:
            data = self._load()
        revisions = []
        for record in sorted(
            data["revisions"], key=lambda item: int(item.get("revision") or 0), reverse=True
        ):
            entry = {key: value for key, value in record.items() if key != "content"}
            if include_content:
                entry["content"] = record.get("content", "")
            revisions.append(entry)
        active = data.get("active_revision")
        return {
            "schema_version": PROMPT_SCHEMA_VERSION,
            "active_revision": int(active) if active else None,
            "revision_count": len(revisions),
            "revisions": revisions,
        }

    def get_revision(self, revision: int | None = None) -> dict | None:
        with self._lock:
            data = self._load()
        if revision is None:
            active = data.get("active_revision")
            if not active:
                return None
            revision = int(active)
        for record in data["revisions"]:
            if int(record.get("revision") or 0) == int(revision):
                return record
        return None

    def activate(self, revision: int) -> dict | None:
        with self._lock:
            data = self._load()
            found = None
            for record in data["revisions"]:
                if int(record.get("revision") or 0) == int(revision):
                    found = record
                    break
            if found is None:
                return None
            data["active_revision"] = int(revision)
            self._save(data)
        return {key: value for key, value in found.items() if key != "content"}

    def state(self) -> dict:
        listing = self.list_revisions(include_content=False)
        active = self.get_revision(listing["active_revision"]) if listing["active_revision"] else None
        return {
            "configured": active is not None,
            "active_revision": listing["active_revision"],
            "revision_count": listing["revision_count"],
            "active_prompt": (
                {
                    "revision": active["revision"],
                    "title": active.get("title", ""),
                    "sha256": active["sha256"],
                    "char_count": active["char_count"],
                    "created_at": active["created_at"],
                }
                if active
                else None
            ),
        }


# --------------------------------------------------------------------------
# DeepSeek client
# --------------------------------------------------------------------------


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ):
        if not api_key or not api_key.strip():
            raise DeepSeekConfigError("DeepSeek API key is not configured")
        self.api_key = api_key.strip()
        self.base_url = (base_url or DEEPSEEK_BASE_URL).rstrip("/")
        self.model = model or DEEPSEEK_MODEL
        self.timeout_seconds = float(timeout_seconds)

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        temperature: float = 0.2,
        json_mode: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        thinking: str | None = None,
    ) -> dict:
        import httpx

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": False,
        }
        if thinking:
            payload["thinking"] = {"type": thinking}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            # `required` and named tool choices return 400 in thinking mode.
            payload["tool_choice"] = tool_choice or "auto"
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except Exception as error:  # network-level failure
            raise DeepSeekRequestError(f"DeepSeek request failed: {error}") from error

        if response.status_code != 200:
            detail = response.text[:400] if response.text else ""
            raise DeepSeekRequestError(
                f"DeepSeek HTTP {response.status_code}: {detail}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise DeepSeekRequestError("DeepSeek returned a non-JSON response") from error

        choices = body.get("choices") or []
        if not choices:
            raise DeepSeekRequestError("DeepSeek response contained no choices")
        message = choices[0].get("message") or {}
        usage = body.get("usage") or {}
        return {
            "content": message.get("content") or "",
            "reasoning_content": message.get("reasoning_content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "finish_reason": choices[0].get("finish_reason"),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
            },
            "model": body.get("model") or self.model,
            "response_id": body.get("id"),
        }

    def probe(self) -> dict:
        """Cheap credential check used by the settings panel."""
        result = self.chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=4,
            temperature=0.0,
        )
        return {
            "ok": True,
            "model": result["model"],
            "finish_reason": result["finish_reason"],
            "usage": result["usage"],
        }


def read_api_key(key_path: Path | str) -> str:
    path = Path(key_path)
    try:
        value = path.read_text("utf-8").strip()
    except OSError as error:
        raise DeepSeekConfigError(
            f"DeepSeek API key file is missing: {path}"
        ) from error
    if not value:
        raise DeepSeekConfigError(f"DeepSeek API key file is empty: {path}")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    if mode & 0o077:
        raise DeepSeekConfigError(
            f"DeepSeek API key file permissions must be 600, found {mode:o}"
        )
    return value


# --------------------------------------------------------------------------
# Cost estimation
# --------------------------------------------------------------------------


def estimate_cost(usage: dict, off_peak: bool = True) -> dict:
    """Rough USD estimate for deepseek-flash; peak rates are exactly double."""
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    prompt_total = int(usage.get("prompt_tokens") or 0)
    cache_miss = int(
        usage.get("prompt_cache_miss_tokens")
        if usage.get("prompt_cache_miss_tokens") is not None
        else max(0, prompt_total - cache_hit)
    )
    completion = int(usage.get("completion_tokens") or 0)
    multiplier = 1.0 if off_peak else 2.0
    hit_cost = cache_hit / 1_000_000 * 0.003 * multiplier
    miss_cost = cache_miss / 1_000_000 * 0.15 * multiplier
    output_cost = completion / 1_000_000 * 0.6 * multiplier
    return {
        "currency": "USD",
        "billing_window": "off_peak" if off_peak else "peak",
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
        "completion_tokens": completion,
        "estimated_usd": round(hit_cost + miss_cost + output_cost, 6),
        "rate_table": {
            "cache_hit_input_per_million": 0.003 * multiplier,
            "cache_miss_input_per_million": 0.15 * multiplier,
            "output_per_million": 0.6 * multiplier,
        },
    }


# --------------------------------------------------------------------------
# Job store
# --------------------------------------------------------------------------

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_PAUSED = "paused_peak"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_ACTIVE = {JOB_PENDING, JOB_RUNNING, JOB_PAUSED}

DEFAULT_SETTINGS = {
    "auto_review_enabled": False,
    "batch_size": DEFAULT_BATCH_SIZE,
    "model": DEEPSEEK_MODEL,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "temperature": 0.2,
    "resume_only_off_peak": True,
    "enable_web_search": False,
    "max_search_calls": 5,
    "reasoning_effort": "high",
    "last_run_at": None,
    "last_result": None,
    "next_planned_at": None,
    "worker_note": "idle",
}


class ReviewJobStore:
    """Single-file job queue plus the auto-review switch and worker settings."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", JOB_SCHEMA_VERSION)
        data.setdefault("settings", {})
        for key, value in DEFAULT_SETTINGS.items():
            data["settings"].setdefault(key, value)
        jobs = data.get("jobs")
        data["jobs"] = jobs if isinstance(jobs, dict) else {}
        data.setdefault("batches", {})
        return data

    def _save(self, data: dict) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    # ----- settings ----------------------------------------------------

    def settings(self) -> dict:
        with self._lock:
            return dict(self._load()["settings"])

    def update_settings(self, changes: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS)
        with self._lock:
            data = self._load()
            for key, value in (changes or {}).items():
                if key not in allowed or value is None:
                    continue
                if key == "batch_size":
                    value = max(1, min(int(value), MAX_BATCH_SIZE))
                if key == "max_output_tokens":
                    value = max(512, min(int(value), 384000))
                if key == "max_search_calls":
                    value = max(0, min(int(value), 20))
                if key == "temperature":
                    value = max(0.0, min(float(value), 2.0))
                if key == "reasoning_effort":
                    value = str(value)
                    if value not in {"none", "low", "high", "max"}:
                        value = "high"
                if key in {"auto_review_enabled", "resume_only_off_peak", "enable_web_search"}:
                    value = bool(value)
                data["settings"][key] = value
            self._save(data)
            return dict(data["settings"])

    # ----- jobs --------------------------------------------------------

    @staticmethod
    def job_id(match_id: int) -> str:
        return f"dr_{int(match_id)}"

    def upsert_job(
        self,
        match_id: int,
        batch_id: str,
        prompt_revision: int,
        model: str,
        priority: int = 100,
    ) -> tuple[dict, bool]:
        match_id = int(match_id)
        with self._lock:
            data = self._load()
            job_id = self.job_id(match_id)
            existing = data["jobs"].get(job_id)
            if existing and existing.get("status") in {JOB_DONE, JOB_RUNNING}:
                return dict(existing), False
            record = existing or {
                "job_id": job_id,
                "match_id": match_id,
                "created_at": int(time.time()),
                "attempts": 0,
                "cost": None,
            }
            record.update({
                "batch_id": batch_id,
                "prompt_revision": int(prompt_revision),
                "model": model,
                "status": JOB_PENDING,
                "priority": int(priority),
                "queued_at": int(time.time()),
                "last_error": None,
            })
            data["jobs"][job_id] = record
            data["batches"].setdefault(batch_id, {
                "batch_id": batch_id,
                "created_at": int(time.time()),
                "prompt_revision": int(prompt_revision),
                "model": model,
                "match_ids": [],
            })
            batch = data["batches"][batch_id]
            if match_id not in batch["match_ids"]:
                batch["match_ids"].append(match_id)
            self._save(data)
            return dict(record), existing is None

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._load()["jobs"].get(job_id)
            return dict(job) if job else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            jobs = list(self._load()["jobs"].values())
        jobs.sort(key=lambda item: int(item.get("queued_at") or item.get("created_at") or 0), reverse=True)
        return jobs[: max(1, int(limit))]

    def pending(self) -> list[dict]:
        with self._lock:
            jobs = [
                dict(job)
                for job in self._load()["jobs"].values()
                if job.get("status") in {JOB_PENDING, JOB_PAUSED}
            ]
        jobs.sort(key=lambda item: (
            int(item.get("priority") or 100),
            -int(item.get("match_id") or 0),
        ))
        return jobs

    def mark(
        self,
        job_id: str,
        status: str,
        *,
        error: str | None = None,
        output_path: str | None = None,
        cost: dict | None = None,
        increment_attempts: bool = False,
    ) -> dict | None:
        with self._lock:
            data = self._load()
            job = data["jobs"].get(job_id)
            if job is None:
                return None
            job["status"] = status
            job["updated_at"] = int(time.time())
            if error is not None:
                job["last_error"] = str(error)[:2000]
            if output_path is not None:
                job["output_path"] = output_path
            if cost is not None:
                job["cost"] = cost
                job.setdefault("costs", []).append(cost)
            if increment_attempts:
                job["attempts"] = int(job.get("attempts") or 0) + 1
            if status in {JOB_DONE, JOB_FAILED}:
                job["finished_at"] = int(time.time())
            self._save(data)
            return dict(job)

    def set_runtime(self, **changes) -> dict:
        with self._lock:
            data = self._load()
            data["settings"].update({
                key: value for key, value in changes.items()
                if key in DEFAULT_SETTINGS
            })
            self._save(data)
            return dict(data["settings"])

    def stats(self) -> dict:
        with self._lock:
            jobs = list(self._load()["jobs"].values())
        counts = {}
        for job in jobs:
            counts[job.get("status", "unknown")] = counts.get(job.get("status", "unknown"), 0) + 1
        total_cost = sum(
            float(entry.get("estimated_usd") or 0)
            for job in jobs
            for entry in (job.get("costs") or ([job["cost"]] if job.get("cost") else []))
        )
        return {
            "total_jobs": len(jobs),
            "by_status": counts,
            "estimated_total_usd": round(total_cost, 6),
        }
