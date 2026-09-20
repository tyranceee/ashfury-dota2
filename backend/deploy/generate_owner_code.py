#!/usr/bin/env python3
"""Generate a short-lived, one-time Owner device authorization code."""

import argparse
import os
from datetime import datetime
from pathlib import Path

from owner_review import OwnerReviewStore


BASE = Path(os.environ.get("DOTA2_BASE_DIR", "/opt/dota2-mcp"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an Ashfury Dota Owner authorization code")
    parser.add_argument("--minutes", type=int, default=15, help="Code lifetime, 1–60 minutes")
    arguments = parser.parse_args()
    store = OwnerReviewStore(
        Path(os.environ.get("DOTA2_OWNER_DB", BASE / "owner_review.sqlite3")),
        Path(os.environ.get("DOTA2_REVIEW_SIGNING_SECRET", BASE / "review-signing-secret")),
    )
    code, expires_at = store.create_pairing_code(arguments.minutes * 60)
    print(f"Owner authorization code: {code}")
    print(f"Expires at: {datetime.fromtimestamp(expires_at).astimezone().isoformat(timespec='seconds')}")
    print("Open: https://ashfury.cn/dota2/?owner=authorize")


if __name__ == "__main__":
    main()
