#!/usr/bin/env python3
"""Fetch tweet content + metadata via xAI Responses API (uses SuperGrok/X Premium+).

Uses your Hermes xAI OAuth token from ~/.hermes/auth.json — no API key,
no X Developer enrollment needed. Calls xAI's Responses API with x_search.

Usage:
    uv run python fetch_tweet_data.py https://x.com/bcherny/status/2064431111154053187
    uv run python fetch_tweet_data.py 2064431111154053187 --out tweet.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any


def extract_tweet_id(raw: str) -> str:
    m = re.search(r"(?:status/|^)(\d{10,})", raw)
    if not m:
        raise SystemExit(f"Could not extract tweet ID from: {raw}")
    return m.group(1)


def compact_number(value: int | None) -> str:
    if value is None:
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 1_000:
        return f"{value / 1_000:.1f}K".replace(".0K", "K")
    return str(value)


def get_xai_token() -> str:
    """Get newest xAI OAuth access token from Hermes auth.json credential pool."""
    auth_path = Path.home() / ".hermes" / "auth.json"
    if not auth_path.exists():
        raise SystemExit("No Hermes auth.json found. Run: hermes auth add xai-oauth")

    data = json.loads(auth_path.read_text())
    pool = data.get("credential_pool", {})

    best_token = ""
    best_refresh = ""
    for entry in pool.get("xai-oauth", []):
        refresh = entry.get("last_refresh", "")
        token = entry.get("access_token", "")
        status = entry.get("last_status", "")
        if token and refresh > best_refresh and status != "exhausted":
            best_token = token
            best_refresh = refresh

    if not best_token:
        raise SystemExit(
            "No valid xAI OAuth token found. Run: hermes auth add xai-oauth"
        )

    return best_token


def fetch_tweet(tweet_id: str, token: str) -> dict[str, Any]:
    """Fetch tweet via xAI Responses API with x_search tool."""
    url = "https://api.x.ai/v1/responses"
    payload = {
        "model": "grok-4.3",
        "input": (
            f"Look up tweet {tweet_id} on X/Twitter. "
            f"Return ONLY a raw JSON object (no markdown, no code fences) with these exact fields: "
            f'"full_text": "the complete tweet text", '
            f'"author_name": "display name", '
            f'"handle": "@username", '
            f'"likes": number, '
            f'"retweets": number, '
            f'"replies": number'
        ),
        "tools": [{"type": "x_search"}],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise SystemExit(f"xAI API error {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error: {e.reason}")

    # Extract text from Responses API output
    text_content = ""
    for item in result.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text_content += part.get("text", "")

    if not text_content:
        raise SystemExit("xAI returned no content")

    # Extract JSON from the response
    json_match = re.search(r"\{[\s\S]*\}", text_content)
    if not json_match:
        raise SystemExit(f"Could not parse JSON from response:\n{text_content[:500]}")

    data = json.loads(json_match.group(0))

    author = data.get("author_name", "")
    handle = data.get("handle", "").lstrip("@")
    likes = data.get("likes", 0) or 0
    retweets = data.get("retweets", 0) or 0
    replies = data.get("replies", 0) or 0

    return {
        "id": tweet_id,
        "text": data.get("full_text", data.get("text", "")),
        "author": author,
        "handle": f"@{handle}" if handle else "",
        "likes": likes,
        "retweets": retweets,
        "replies": replies,
        "url": f"https://x.com/{handle}/status/{tweet_id}" if handle else "",
        "likes_fmt": compact_number(likes),
        "retweets_fmt": compact_number(retweets),
        "replies_fmt": compact_number(replies),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch tweet content via xAI Responses API"
    )
    ap.add_argument("tweet", help="Tweet URL or ID")
    ap.add_argument("--out", "-o", type=Path, help="Save JSON to file instead of stdout")
    args = ap.parse_args()

    tweet_id = extract_tweet_id(args.tweet)
    token = get_xai_token()

    print(f"Fetching tweet {tweet_id} via xAI...", file=sys.stderr)
    data = fetch_tweet(tweet_id, token)

    output = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(output + "\n")
        print(f"Saved {args.out}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
