#!/usr/bin/env python3
"""Safely provision the fixed Slack trigger without exposing its token."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from review_trigger import EXPECTED_POLICY, load_secret_file, load_trigger_policy


BASE = Path(os.environ.get("DOTA2_BASE_DIR", "/opt/dota2-mcp"))
POLICY_FILE = BASE / "review-trigger-policy.json"
TOKEN_FILE = BASE / "slack-bot-token"


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the Ashfury deep-review Slack trigger")
    parser.add_argument("--channel-id", required=True, help="Slack channel ID, for example C0123456789")
    args = parser.parse_args()

    first = getpass.getpass("Slack Bot Token（输入不可见）: ").strip()
    second = getpass.getpass("再次输入 Slack Bot Token: ").strip()
    if first != second:
        raise SystemExit("两次 Token 不一致，未保存。")

    policy = dict(EXPECTED_POLICY)
    policy["slack_channel_id"] = args.channel_id.strip()
    atomic_write(TOKEN_FILE, first + "\n", 0o600)
    atomic_write(POLICY_FILE, json.dumps(policy, ensure_ascii=False, indent=2) + "\n", 0o600)

    try:
        load_secret_file(TOKEN_FILE)
        load_trigger_policy(POLICY_FILE)
    except Exception:
        TOKEN_FILE.unlink(missing_ok=True)
        POLICY_FILE.unlink(missing_ok=True)
        raise

    print("Slack 深度复盘触发配置已保存。")
    print(f"频道：{policy['slack_channel_id']}")
    print(f"项目：{policy['chatgpt_project_name']}")
    print(f"模型：{policy['model']} / {policy['reasoning_effort']}")
    print(f"Skill：{policy['required_skill']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
