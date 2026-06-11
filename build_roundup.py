#!/usr/bin/env python3
"""Build a multi-tweet roundup carousel from a topic or list of URLs.

Two modes:
  1. Topic search (needs xAI): finds viral tweets via x_search
  2. URL list (no xAI): paste tweet URLs directly

Usage:
    uv run python build_roundup.py "AI agents taking over coding"
    uv run python build_roundup.py --urls https://x.com/a/status/1 https://x.com/b/status/2
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "assets" / "archivo.css"
ASSETS = ROOT / "assets"
OUT = ROOT / "out" / "x_roundup"
SLIDE_W, SLIDE_H = 1080, 1350

# ── xAI token ──────────────────────────────────────────────

def resolve_token() -> tuple[str, str]:
    """Resolve API token with fallback chain: xAI API key → xAI OAuth → Bearer Token.
    Returns (token, source_name)."""
    # 1. xAI API key from env
    api_key = os.environ.get("XAI_API_KEY")
    if api_key:
        return api_key, "xai_api_key"

    # 2. xAI OAuth from Hermes auth.json (newest non-exhausted)
    auth_path = Path.home() / ".hermes" / "auth.json"
    if auth_path.exists():
        data = json.loads(auth_path.read_text())
        pool = data.get("credential_pool", {})
        best_token, best_refresh = "", ""
        for e in pool.get("xai-oauth", []):
            r = e.get("last_refresh", "")
            t = e.get("access_token", "")
            if t and r > best_refresh:
                best_token, best_refresh = t, r
        if best_token:
            return best_token, "xai_oauth"

    # 3. X Bearer Token from env or .env
    bearer = os.environ.get("X_BEARER_TOKEN", "")
    if bearer and len(bearer) > 50:
        return bearer, "bearer_token"

    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "BEARER" in line.upper() and not line.strip().startswith("#"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(val) > 50:
                    return val, "bearer_token"

    return "", "none"


def find_tweets_xai(topic: str, count: int, token: str) -> list[dict]:
    """Find viral tweets via xAI x_search."""
    url = "https://api.x.ai/v1/responses"
    payload = {
        "model": "grok-4.3",
        "input": (
            f"Find {count} viral X/Twitter posts from the last 7 days about '{topic}'. "
            f"Must have 300+ likes. Return as JSON array with fields: "
            f'url, author_name, author_handle, full_text, likes, retweets, replies. '
            f"Return ONLY valid JSON, no markdown."
        ),
        "tools": [{"type": "x_search"}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    text = ""
    for item in result.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text += part.get("text", "")
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        raise SystemExit(f"xAI returned no tweet data for: {topic}")
    return json.loads(match.group(0))


def find_tweets_bearer(topic: str, count: int, token: str) -> list[dict]:
    """Find tweets via X API v2 Bearer Token search."""
    query = urllib.parse.quote(f"{topic} min_faves:300 -is:retweet lang:en")
    url = (
        f"https://api.x.com/2/tweets/search/recent"
        f"?query={query}&max_results={min(count, 10)}"
        f"&tweet.fields=created_at,public_metrics"
        f"&expansions=author_id&user.fields=name,username"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "carousel-app/1.0")

    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read())

    tweets_data = raw.get("data", [])
    users = {u["id"]: u for u in raw.get("includes", {}).get("users", [])}

    results = []
    for t in tweets_data[:count]:
        user = users.get(t.get("author_id", ""), {})
        metrics = t.get("public_metrics", {})
        results.append({
            "url": f"https://x.com/{user.get('username','i')}/status/{t['id']}",
            "author_name": user.get("name", ""),
            "author_handle": f"@{user.get('username', '')}",
            "full_text": t.get("text", ""),
            "likes": metrics.get("like_count", 0),
            "retweets": metrics.get("retweet_count", 0),
            "replies": metrics.get("reply_count", 0),
        })
    return results


def find_tweets(topic: str, count: int) -> list[dict]:
    """Find tweets with fallback: xAI → Bearer Token → error."""
    token, source = resolve_token()
    if not token:
        raise SystemExit(
            "No API credentials found. Options:\n"
            "  1. Set XAI_API_KEY in ~/.hermes/.env\n"
            "  2. Run: hermes auth add xai-oauth\n"
            "  3. Set X_BEARER_TOKEN in ~/.hermes/.env\n"
            "  4. Use --urls mode (no API needed)"
        )

    print(f"[auth] using {source}", file=sys.stderr)

    if source in ("xai_api_key", "xai_oauth"):
        try:
            return find_tweets_xai(topic, count, token)
        except Exception as e:
            print(f"[xai] failed: {e}, trying Bearer Token...", file=sys.stderr)
            # Fall through to bearer
            token2, source2 = "", ""
            # Try bearer specifically
            bearer = os.environ.get("X_BEARER_TOKEN", "")
            if bearer and len(bearer) > 50:
                token2, source2 = bearer, "bearer_token"
            if not token2:
                env_path = Path.home() / ".hermes" / ".env"
                if env_path.exists():
                    for line in env_path.read_text().splitlines():
                        if "BEARER" in line.upper() and not line.strip().startswith("#"):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if len(val) > 50:
                                token2, source2 = val, "bearer_token"
                                break
            if token2:
                print(f"[auth] fallback to {source2}", file=sys.stderr)
                return find_tweets_bearer(topic, count, token2)
            raise

    if source == "bearer_token":
        return find_tweets_bearer(topic, count, token)

    raise SystemExit(f"Unknown token source: {source}")


def compact_number(value: int | None) -> str:
    if value is None: return ""
    if value >= 1_000_000: return f"{value/1_000_000:.1f}M".replace(".0M","M")
    if value >= 1_000: return f"{value/1_000:.1f}K".replace(".0K","K")
    return str(value)


def extract_status_id(url: str) -> str:
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else ""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


# ── Cover art ──────────────────────────────────────────────

def generate_cover(topic: str, out_path: Path) -> Path:
    """Generate cover art via GPT Image 2.0."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("VCPH_OPENAI_API_KEY")
    if not api_key:
        print("[cover] OPENAI_API_KEY not set, skipping cover art", file=sys.stderr)
        return out_path

    try:
        from openai import OpenAI
    except ImportError:
        print("[cover] openai not installed", file=sys.stderr)
        return out_path

    client = OpenAI(api_key=api_key)
    prompt = (
        f"Square editorial cover art for an Instagram carousel about '{topic}'. "
        f"Cream/off-white paper background (#F4F2EC). "
        f"Dark ink (#16140F) and rust/terracotta accent (#C0552E). "
        f"Abstract geometric composition, editorial magazine aesthetic. "
        f"High-end publication quality, 1080x1080. No text, no logos."
    )

    print(f"[cover] generating via GPT Image 2.0...", file=sys.stderr)
    resp = client.images.generate(model="gpt-image-2", prompt=prompt, n=1)
    img = resp.data[0]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(img, "b64_json") and img.b64_json:
        import base64
        out_path.write_bytes(base64.b64decode(img.b64_json))
    elif hasattr(img, "url") and img.url:
        urllib.request.urlretrieve(img.url, str(out_path))
    print(f"[cover] saved {out_path.name}", file=sys.stderr)
    return out_path


# ── Tweet capture ──────────────────────────────────────────

def capture_tweet(status_id: str, out_path: Path) -> Path:
    """Screenshot tweet via Playwright — uses direct x.com page (more reliable than embed)."""
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://x.com/i/status/{status_id}"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(
            viewport={"width": 720, "height": 2600},
            device_scale_factor=2,
            color_scheme="dark",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("article", timeout=15000)
        except Exception:
            page.screenshot(path=str(out_path.parent / f"{out_path.stem}_debug.png"))
            browser.close()
            raise SystemExit(f"article not found for {status_id}")

        page.wait_for_timeout(2000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        card = page.locator("article").first
        box = card.bounding_box()
        if not box:
            page.screenshot(path=str(out_path))
            browser.close()
            return out_path

        # Clip above "Read N replies" bar
        cut_top = page.evaluate("""() => {
            const els = document.querySelectorAll('article button, article a, article div[role="button"]');
            for (const el of els) {
                if (/^Read .+ replies$/.test((el.textContent || '').trim())) {
                    return el.getBoundingClientRect().top;
                }
            }
            return null;
        }""")
        height = (cut_top - box["y"] - 14) if cut_top else box["height"]
        page.screenshot(path=str(out_path), clip={
            "x": box["x"], "y": box["y"], "width": box["width"], "height": height,
        })
        browser.close()

    print(f"[capture] {out_path.name}", file=sys.stderr)
    return out_path


# ── HTML slide generation ──────────────────────────────────

def title_slide_html(topic: str, cover_path: Path, slide_num: int, total: int) -> str:
    """Generate HTML for the title slide."""
    cover_rel = f"../title_assets/{cover_path.name}"
    accent_word = topic.split()[-1] if topic.split() else topic
    main = " ".join(topic.split()[:-1]) if len(topic.split()) > 1 else ""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{FONTS.read_text()}
:root {{ --bg: #F4F2EC; --bg-top: #E9E6DF; --fg: #16140F; --ink-soft: rgba(20,18,14,0.78); --primary: #C0552E; --muted: rgba(20,18,14,0.55); --rule: rgba(20,18,14,0.28); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ margin:0; background:transparent; font-family:'Archivo',sans-serif; }}
.slide {{ width:{SLIDE_W}px; height:{SLIDE_H}px; position:relative; overflow:hidden; background:linear-gradient(180deg,var(--bg-top) 0%,#F1EEE6 48%,var(--bg) 100%); color:var(--fg); }}
.handle {{ position:absolute; top:68px; left:78px; display:flex; align-items:center; gap:14px; }}
.handle span {{ font-size:27px; font-weight:700; letter-spacing:0.14em; text-transform:uppercase; }}
.zone {{ position:absolute; pointer-events:none; }}
.zone.top-right {{ top:40px; right:40px; width:160px; height:80px; }}
.zone.bottom-handle {{ bottom:40px; left:340px; width:400px; height:60px; }}
.cover {{ position:absolute; top:90px; left:70px; width:940px; height:470px; border-radius:28px; object-fit:cover; background:#1a1a2e; }}
.headline {{ position:absolute; top:620px; left:120px; right:120px; text-align:center; font-size:72px; font-weight:800; letter-spacing:-0.03em; line-height:1.08; }}
.headline .accent {{ color:var(--primary); }}
.sub {{ position:absolute; top:780px; left:120px; right:120px; text-align:center; font-size:28px; font-weight:600; color:var(--ink-soft); }}
.swipe {{ position:absolute; top:880px; left:0; right:0; display:flex; align-items:center; justify-content:center; gap:16px; }}
.swipe span {{ font-size:24px; font-weight:700; letter-spacing:0.16em; text-transform:uppercase; }}
.swipe .chip {{ width:34px; height:34px; border-radius:8px; background:var(--primary); display:flex; align-items:center; justify-content:center; }}
.dots {{ position:absolute; bottom:108px; left:0; right:0; display:flex; align-items:center; justify-content:center; gap:12px; }}
.dots .dash {{ width:34px; height:9px; border-radius:5px; background:var(--primary); }}
.dots .dot {{ width:9px; height:9px; border-radius:50%; background:rgba(20,18,14,0.28); }}
.kicker {{ position:absolute; top:176px; left:120px; right:120px; display:flex; align-items:center; gap:28px; }}
.kicker::before,.kicker::after {{ content:''; flex:1; height:2px; background:var(--rule); }}
.kicker em {{ font-style:normal; font-size:22px; font-weight:700; letter-spacing:0.22em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }}
</style></head><body>
<div class="slide">
  <div class="handle"><svg width="32" height="32" viewBox="0 0 24 24" fill="none"><rect x="2" y="2" width="20" height="20" rx="5.5" fill="#16140F"/><circle cx="12" cy="12" r="4.4" stroke="#F4F2EC" stroke-width="1.8"/><circle cx="17.2" cy="6.8" r="1.3" fill="#F4F2EC"/></svg><span>@llmaw</span></div>
  <div class="zone top-right"></div>
  <div class="kicker"><em>Roundup</em></div>
  <img class="cover" src="{cover_rel}" alt="">
  <h1 class="headline">{html.escape(main)}<br><span class="accent">{html.escape(accent_word)}</span></h1>
  <p class="sub">{total} tweets on this topic — curated for you</p>
  <div class="swipe"><span>Swipe for more</span><div class="chip"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 5l7 7-7 7" stroke="#F4F2EC" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></div></div>
  <div class="dots">{dots_html(slide_num, total)}</div>
  <div class="zone bottom-handle"></div>
</div></body></html>"""


def tweet_slide_html(author: str, handle: str, text: str, metrics: dict, img_src: str, slide_num: int, total: int) -> str:
    """Generate HTML for a tweet embed slide."""
    safe_author = html.escape(author)
    safe_handle = html.escape(handle)
    safe_text = html.escape(text[:300])
    safe_img = html.escape(img_src)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{FONTS.read_text()}
:root {{ --bg: #F4F2EC; --bg-top: #E9E6DF; --fg: #16140F; --ink-soft: rgba(20,18,14,0.78); --primary: #C0552E; --muted: rgba(20,18,14,0.55); --rule: rgba(20,18,14,0.28); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ margin:0; background:transparent; font-family:'Archivo',sans-serif; }}
.slide {{ width:{SLIDE_W}px; height:{SLIDE_H}px; position:relative; overflow:hidden; background:linear-gradient(180deg,var(--bg-top) 0%,#F1EEE6 48%,var(--bg) 100%); color:var(--fg); }}
.handle {{ position:absolute; top:68px; left:78px; display:flex; align-items:center; gap:14px; }}
.handle span {{ font-size:27px; font-weight:700; letter-spacing:0.14em; text-transform:uppercase; }}
.zone {{ position:absolute; pointer-events:none; }}
.zone.top-right {{ top:40px; right:40px; width:160px; height:80px; }}
.zone.bottom-handle {{ bottom:40px; left:340px; width:400px; height:60px; }}
.kicker {{ position:absolute; top:196px; left:120px; right:120px; display:flex; align-items:center; gap:28px; }}
.kicker::before,.kicker::after {{ content:''; flex:1; height:2px; background:var(--rule); }}
.kicker em {{ font-style:normal; font-size:22px; font-weight:700; letter-spacing:0.22em; text-transform:uppercase; color:var(--ink-soft); white-space:nowrap; }}
.tweet-shot {{ position:absolute; top:300px; left:110px; width:860px; display:block; border-radius:24px; box-shadow:0 30px 70px rgba(20,18,14,0.22); }}
.tweet-meta {{ position:absolute; top:1050px; left:120px; right:120px; text-align:center; font-size:24px; font-weight:600; color:var(--ink-soft); }}
.tweet-meta strong {{ font-weight:800; color:var(--fg); }}
.dots {{ position:absolute; bottom:108px; left:0; right:0; display:flex; align-items:center; justify-content:center; gap:12px; }}
.dots .dash {{ width:34px; height:9px; border-radius:5px; background:var(--primary); }}
.dots .dot {{ width:9px; height:9px; border-radius:50%; background:rgba(20,18,14,0.28); }}
</style></head><body>
<div class="slide">
  <div class="handle"><svg width="32" height="32" viewBox="0 0 24 24" fill="none"><rect x="2" y="2" width="20" height="20" rx="5.5" fill="#16140F"/><circle cx="12" cy="12" r="4.4" stroke="#F4F2EC" stroke-width="1.8"/><circle cx="17.2" cy="6.8" r="1.3" fill="#F4F2EC"/></svg><span>@llmaw</span></div>
  <div class="zone top-right"></div>
  <div class="kicker"><em>The proof</em></div>
  <img class="tweet-shot" src="{safe_img}" alt="Tweet from {safe_author}">
  <div class="tweet-meta"><strong>{safe_author}</strong> {safe_handle} · {metrics.get('likes_fmt','')} likes · {metrics.get('retweets_fmt','')} RTs</div>
  <div class="dots">{dots_html(slide_num, total)}</div>
  <div class="zone bottom-handle"></div>
</div></body></html>"""


def cta_slide_html(slide_num: int, total: int) -> str:
    """Generate HTML for the final CTA slide."""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{FONTS.read_text()}
:root {{ --bg: #F4F2EC; --bg-top: #E9E6DF; --fg: #16140F; --ink-soft: rgba(20,18,14,0.78); --primary: #C0552E; --muted: rgba(20,18,14,0.55); --rule: rgba(20,18,14,0.28); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ margin:0; background:transparent; font-family:'Archivo',sans-serif; }}
.slide {{ width:{SLIDE_W}px; height:{SLIDE_H}px; position:relative; overflow:hidden; background:linear-gradient(180deg,var(--bg-top) 0%,#F1EEE6 48%,var(--bg) 100%); color:var(--fg); display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; }}
.handle {{ position:absolute; top:68px; left:78px; display:flex; align-items:center; gap:14px; }}
.handle span {{ font-size:27px; font-weight:700; letter-spacing:0.14em; text-transform:uppercase; }}
.headline {{ font-size:64px; font-weight:800; letter-spacing:-0.03em; line-height:1.12; }}
.headline .accent {{ color:var(--primary); }}
.sub {{ margin-top:36px; font-size:30px; font-weight:600; color:var(--ink-soft); max-width:700px; }}
.cta-chip {{ margin-top:56px; display:inline-flex; align-items:center; gap:12px; background:var(--primary); color:#FFF8F2; font-size:28px; font-weight:800; padding:20px 48px; border-radius:16px; letter-spacing:0.04em; }}
.cta-chip svg {{ display:block; }}
.dots {{ position:absolute; bottom:108px; left:0; right:0; display:flex; align-items:center; justify-content:center; gap:12px; }}
.dots .dash {{ width:34px; height:9px; border-radius:5px; background:var(--primary); }}
.dots .dot {{ width:9px; height:9px; border-radius:50%; background:rgba(20,18,14,0.28); }}
</style></head><body>
<div class="slide">
  <div class="handle"><svg width="32" height="32" viewBox="0 0 24 24" fill="none"><rect x="2" y="2" width="20" height="20" rx="5.5" fill="#16140F"/><circle cx="12" cy="12" r="4.4" stroke="#F4F2EC" stroke-width="1.8"/><circle cx="17.2" cy="6.8" r="1.3" fill="#F4F2EC"/></svg><span>@llmaw</span></div>
  <h1 class="headline">Follow <span class="accent">@llmaw</span></h1>
  <p class="sub">Daily AI news — curated from X, built by machines, approved by humans.</p>
  <div class="cta-chip"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="#FFF8F2" stroke-width="3" stroke-linecap="round"/></svg>Save for later</div>
  <div class="dots">{dots_html(slide_num, total)}</div>
</div></body></html>"""


def dots_html(active: int, total: int) -> str:
    items = []
    for i in range(1, total + 1):
        cls = "dash" if i == active else "dot"
        items.append(f'<div class="{cls}"></div>')
    return "\n    ".join(items)


def render_slide(html_content: str, out_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    tmp = OUT / "_render_tmp.html"
    tmp.write_text(html_content)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(
            viewport={"width": SLIDE_W, "height": SLIDE_H},
            device_scale_factor=1,
        )
        page.goto(tmp.as_uri())
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(400)
        page.locator(".slide").screenshot(path=str(out_path))
        browser.close()
    tmp.unlink()
    print(f"[render] {out_path.name}", file=sys.stderr)
    return out_path


# ── Main ───────────────────────────────────────────────────

def build_roundup(topic: str, tweet_count: int = 4) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    topic_slug = slug(topic)

    # 1. Find tweets (xAI → Bearer Token fallback)
    print(f"[scout] searching X for '{topic}'...", file=sys.stderr)
    tweets = find_tweets(topic, tweet_count)
    print(f"[scout] found {len(tweets)} tweets", file=sys.stderr)

    # 2. Generate cover art
    cover_path = OUT / "title_assets" / f"cover_{topic_slug}.png"
    generate_cover(topic, cover_path)

    # 3. Capture tweet screenshots
    captures = []
    for i, tweet in enumerate(tweets):
        status_id = extract_status_id(tweet.get("url", ""))
        if not status_id:
            continue
        out_png = OUT / "captures" / f"tweet_{i+1:02d}.png"
        try:
            capture_tweet(status_id, out_png)
            captures.append((tweet, out_png))
        except Exception as e:
            print(f"[capture] failed tweet {i+1}: {e}", file=sys.stderr)

    total_slides = 1 + len(captures) + 1  # title + tweets + CTA
    slides = []

    # 4. Title slide
    title_html = title_slide_html(topic, cover_path, 1, total_slides)
    title_png = OUT / f"roundup_{topic_slug}_01.png"
    render_slide(title_html, title_png)
    slides.append({"num": 1, "type": "title", "path": str(title_png)})

    # 5. Tweet slides
    for i, (tweet, _) in enumerate(captures):
        num = i + 2
        metrics = {
            "likes_fmt": compact_number(tweet.get("likes", 0)),
            "retweets_fmt": compact_number(tweet.get("retweets", 0)),
        }
        img_rel = f"captures/tweet_{i+1:02d}.png"
        tweet_html = tweet_slide_html(
            tweet.get("author_name", ""),
            tweet.get("author_handle", ""),
            tweet.get("full_text", ""),
            metrics,
            img_rel,
            num,
            total_slides,
        )
        png_path = OUT / f"roundup_{topic_slug}_{num:02d}.png"
        render_slide(tweet_html, png_path)
        slides.append({"num": num, "type": "tweet", "author": tweet.get("author_name"), "path": str(png_path)})

    # 6. CTA slide
    cta_html = cta_slide_html(total_slides, total_slides)
    cta_png = OUT / f"roundup_{topic_slug}_{total_slides:02d}.png"
    render_slide(cta_html, cta_png)
    slides.append({"num": total_slides, "type": "cta", "path": str(cta_png)})

    # 7. Manifest
    manifest = {
        "topic": topic,
        "tweets_found": len(tweets),
        "tweets_captured": len(captures),
        "total_slides": total_slides,
        "slides": slides,
    }
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n[build] {total_slides} slides -> {OUT}", file=sys.stderr)

    return manifest


def build_roundup_from_urls(urls: list[str], topic: str = "") -> dict:
    """Build a roundup from a list of tweet URLs — no xAI needed."""
    OUT.mkdir(parents=True, exist_ok=True)
    topic_slug = slug(topic) if topic else "url_roundup"

    # Extract tweets from URLs — just use status IDs, screenshots carry all info
    tweets = []
    for url in urls:
        status_id = extract_status_id(url)
        if status_id:
            tweets.append({
                "url": url,
                "author_name": "",
                "author_handle": "",
                "full_text": "",
                "likes": 0,
                "retweets": 0,
            })
            print(f"[url] {status_id}", file=sys.stderr)

    if not tweets:
        raise SystemExit("No valid tweet URLs found")

    # Generate cover
    display_topic = topic or f"{len(tweets)} tweets roundup"
    cover_path = OUT / "title_assets" / f"cover_{topic_slug}.png"
    generate_cover(display_topic, cover_path)

    # Capture screenshots
    captures = []
    for i, tweet in enumerate(tweets):
        status_id = extract_status_id(tweet.get("url", ""))
        if not status_id:
            continue
        out_png = OUT / "captures" / f"tweet_{i+1:02d}.png"
        try:
            capture_tweet(status_id, out_png)
            captures.append((tweet, out_png))
        except Exception as e:
            print(f"[capture] failed: {e}", file=sys.stderr)

    total_slides = 1 + len(captures) + 1
    slides = []

    # Title
    title_html = title_slide_html(display_topic, cover_path, 1, total_slides)
    title_png = OUT / f"roundup_{topic_slug}_01.png"
    render_slide(title_html, title_png)
    slides.append({"num": 1, "type": "title", "path": str(title_png)})

    # Tweet slides
    for i, (tweet, _) in enumerate(captures):
        num = i + 2
        metrics = {"likes_fmt": compact_number(tweet.get("likes", 0)), "retweets_fmt": compact_number(tweet.get("retweets", 0))}
        img_rel = f"captures/tweet_{i+1:02d}.png"
        tweet_html = tweet_slide_html(
            tweet.get("author_name", ""), tweet.get("author_handle", ""),
            tweet.get("full_text", ""), metrics, img_rel, num, total_slides,
        )
        png_path = OUT / f"roundup_{topic_slug}_{num:02d}.png"
        render_slide(tweet_html, png_path)
        slides.append({"num": num, "type": "tweet", "author": tweet.get("author_name"), "path": str(png_path)})

    # CTA
    cta_html = cta_slide_html(total_slides, total_slides)
    cta_png = OUT / f"roundup_{topic_slug}_{total_slides:02d}.png"
    render_slide(cta_html, cta_png)
    slides.append({"num": total_slides, "type": "cta", "path": str(cta_png)})

    manifest = {"topic": display_topic, "mode": "urls", "tweets_captured": len(captures), "total_slides": total_slides, "slides": slides}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n[build] {total_slides} slides -> {OUT}", file=sys.stderr)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a multi-tweet roundup carousel")
    ap.add_argument("topic", nargs="?", help="Topic to search for (e.g. 'AI agents 2026') — omit if using --urls")
    ap.add_argument("--urls", nargs="+", help="Tweet URLs to include (no xAI needed)")
    ap.add_argument("--count", type=int, default=4, help="Number of tweets (topic mode only, default: 4)")
    ap.add_argument("--provider", choices=["openai", "gemini"], default="openai")
    args = ap.parse_args()

    if args.urls:
        topic = args.topic or ""
        build_roundup_from_urls(args.urls, topic)
    elif args.topic:
        build_roundup(args.topic, args.count)
    else:
        ap.error("Either provide a topic or use --urls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
