#!/usr/bin/env python3
"""Create a branded carousel from one X/Twitter URL.

The workflow is intentionally one-input:

    uv run python build_x_carousel.py https://x.com/OpenAI/status/2061887650391625870

Outputs go to out/x_carousel by default. The first slide is a branded title
PNG. Each discovered post becomes either a branded post PNG or a branded
post+video MP4 when video is available.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from build_video_slide import (
    OUT,
    SLIDE_H,
    SLIDE_W,
    build_video_slide,
    clean_post_text,
    compact_number,
    extract_status_id,
    extract_status_url,
    format_post_date,
)

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "assets" / "archivo.css"
DEFAULT_OUT = OUT / "x_carousel"

X_COOKIE_DOMAINS = ("x.com", "twitter.com")


def canonical_x_url(value: str) -> str:
    status_url = extract_status_url(value) or value.strip()
    status_id = extract_status_id(status_url)
    if not status_id:
        raise SystemExit(f"could not find an X status id in: {value}")
    match = re.search(r"https://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/", status_url)
    handle = match.group(1) if match else "i"
    return f"https://x.com/{handle}/status/{status_id}"


def run_json(cmd: list[str]) -> dict[str, object] | None:
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def fetch_metadata(url: str, cookies_from_browser: str | None) -> dict[str, object] | None:
    cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--dump-json"]
    if cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])
    cmd.append(url)
    return run_json(cmd)


def metadata_has_video(metadata: dict[str, object] | None) -> bool:
    if not metadata:
        return False
    formats = metadata.get("formats")
    return isinstance(formats, list) and any(fmt.get("vcodec") != "none" for fmt in formats if isinstance(fmt, dict))


def parse_browser_cookie_spec(value: str) -> tuple[str, str | None, str | None, str | None]:
    browser_spec, _, container = value.partition("::")
    browser_profile, _, profile = browser_spec.partition(":")
    browser, _, keyring = browser_profile.partition("+")
    return browser, profile or None, keyring or None, container or None


def same_site_for_playwright(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.lower()
    if normalized == "strict":
        return "Strict"
    if normalized == "lax":
        return "Lax"
    if normalized == "none":
        return "None"
    return None


def cookie_domain_is_x(domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in X_COOKIE_DOMAINS)


def expires_for_playwright(value: int) -> int:
    if value <= 0:
        return -1
    if value > 10_000_000_000:
        value = int(value / 1_000_000 - 11_644_473_600)
    return value if value > 0 else -1


def load_playwright_cookies(cookies_from_browser: str | None) -> list[dict[str, object]]:
    if not cookies_from_browser:
        return []
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ModuleNotFoundError:
        print("[x] yt-dlp is required to import browser cookies; continuing without them")
        return []

    browser_name, profile, keyring, container = parse_browser_cookie_spec(cookies_from_browser)
    try:
        cookie_jar = extract_cookies_from_browser(
            browser_name,
            profile=profile,
            keyring=keyring,
            container=container,
        )
    except Exception as exc:
        print(f"[x] could not load browser cookies from {cookies_from_browser}: {exc}")
        return []

    cookies: list[dict[str, object]] = []
    for cookie in cookie_jar:
        if not cookie_domain_is_x(cookie.domain):
            continue
        item: dict[str, object] = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": bool(
                cookie.has_nonstandard_attr("HttpOnly")
                or cookie.has_nonstandard_attr("HTTPOnly")
            ),
        }
        if cookie.expires is not None:
            item["expires"] = expires_for_playwright(int(cookie.expires))
        same_site = same_site_for_playwright(
            cookie.get_nonstandard_attr("SameSite")
            or cookie.get_nonstandard_attr("sameSite")
        )
        if same_site:
            item["sameSite"] = same_site
        cookies.append(item)
    print(f"[x] loaded {len(cookies)} X/Twitter cookies from {cookies_from_browser}")
    return cookies


def post_from_metadata(url: str, metadata: dict[str, object] | None) -> dict[str, str]:
    metadata = metadata or {}
    author = str(metadata.get("uploader") or "Source post")
    handle = str(metadata.get("uploader_id") or "")
    if handle and not handle.startswith("@"):
        handle = f"@{handle}"
    text = clean_post_text(metadata.get("description")) or clean_post_text(metadata.get("title")) or "Source post"
    date = format_post_date(metadata.get("timestamp"))
    views = compact_number(metadata.get("view_count"))
    likes = compact_number(metadata.get("like_count"))
    reposts = compact_number(metadata.get("repost_count"))
    replies = compact_number(metadata.get("comment_count"))
    status_id = extract_status_id(url) or ""
    return {
        "url": url,
        "id": status_id,
        "author": author,
        "handle": handle,
        "text": text,
        "date": date,
        "views": views,
        "likes": likes,
        "reposts": reposts,
        "replies": replies,
    }


def is_embed_stop_line(line: str) -> bool:
    months = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    if re.search(rf"\b(?:{months})\b\s+\d{{1,2}},\s+\d{{4}}", line):
        return True
    if re.match(r"^(?:Read|Show|View)\b", line):
        return True
    if re.match(r"^\d[\d,.]*\s*(?:Views?|Likes?|Reposts?|Quotes?|Bookmarks?|Replies?)$", line):
        return True
    return line in {"Reply", "Repost", "Like", "View", "Share", "Copy link", "Translate post"}


def post_from_embed_text(url: str, embed_text: str) -> dict[str, str] | None:
    status_id = extract_status_id(url)
    if not status_id:
        return None
    lines = [line.strip() for line in embed_text.splitlines() if line.strip()]
    if not lines:
        return None

    handle = next((line for line in lines if line.startswith("@")), "")
    handle_index = lines.index(handle) if handle else -1
    author = ""
    if handle_index > 0:
        author = re.sub(r"\s*[✓✔].*$", "", lines[handle_index - 1]).strip()
    elif lines and not lines[0].startswith("@"):
        author = re.sub(r"\s*[✓✔].*$", "", lines[0]).strip()

    content_lines: list[str] = []
    start = handle_index + 1 if handle_index >= 0 else 1
    for line in lines[start:]:
        if is_embed_stop_line(line):
            break
        if line in {author, handle, "·", "Follow"}:
            continue
        content_lines.append(line)

    text = clean_post_text("\n".join(content_lines))
    return {
        "url": canonical_x_url(url),
        "id": status_id,
        "author": author or "Source post",
        "handle": handle,
        "text": text or "Source post",
        "date": "",
        "views": "",
        "likes": "",
        "reposts": "",
        "replies": "",
    }


def fetch_embed_post(url: str) -> dict[str, str] | None:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return None

    status_id = extract_status_id(url)
    if not status_id:
        return None
    embed_url = (
        "https://platform.twitter.com/embed/Tweet.html"
        f"?id={status_id}&theme=dark&width=550&hideThread=true&dnt=true"
    )
    print(f"[x] reading embed metadata {status_id}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 620, "height": 1600}, device_scale_factor=1)
            page.goto(embed_url, wait_until="networkidle")
            page.wait_for_timeout(1000)
            embed_text = page.locator("article").first.inner_text(timeout=10000)
            browser.close()
    except Exception:
        return None
    return post_from_embed_text(url, embed_text)


def article_to_post(article: dict[str, object]) -> dict[str, str] | None:
    url = str(article.get("url") or "")
    status_id = extract_status_id(url)
    if not status_id:
        return None
    text = str(article.get("text") or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    author = str(article.get("author") or "")
    handle = str(article.get("handle") or "")
    if not author and handle:
        for i, line in enumerate(lines):
            if line == handle and i > 0:
                author = re.sub(r"\s*[✓✔].*$", "", lines[i - 1]).strip()
                break
    handle_name = handle.lstrip("@").lower()
    content_lines = []
    for line in lines:
        normalized = re.sub(r"\s*[✓✔].*$", "", line).strip()
        if line in {author, handle, "·", "Follow"}:
            continue
        if normalized and normalized == author:
            continue
        if handle_name and normalized.lower() == handle_name:
            continue
        if re.match(r"^\d+[KMB]?$", line):
            continue
        if line in {"Reply", "Repost", "Like", "View", "Share"}:
            continue
        content_lines.append(line)
    content = clean_post_text("\n".join(content_lines))
    return {
        "url": canonical_x_url(url),
        "id": status_id,
        "author": author or "Source post",
        "handle": handle,
        "text": content,
        "date": "",
        "views": "",
        "likes": "",
        "reposts": "",
        "replies": "",
    }


def discover_thread_posts(url: str, max_posts: int, cookies_from_browser: str | None) -> list[dict[str, str]]:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return []

    print("[x] looking for thread posts")
    article_script = """
    () => Array.from(document.querySelectorAll('article')).map((article, index) => {
        const timeLink = article.querySelector('time')?.closest('a')?.href || "";
        const statusLinks = Array.from(article.querySelectorAll('a[href*="/status/"]')).map((a) => a.href);
        const status = timeLink || statusLinks.find(Boolean) || "";
        const links = Array.from(article.querySelectorAll('a[href^="/"], a[href^="https://x.com/"], a[href^="https://twitter.com/"]'));
        const handleLink = links.map((a) => a.textContent || "").find((text) => /^@/.test(text.trim())) || "";
        const author = Array.from(article.querySelectorAll('a[role="link"] span'))
          .map((el) => el.textContent || "").find((text) => text && !text.startsWith("@")) || "";
        const rect = article.getBoundingClientRect();
        return {
            url: status,
            handle: handleLink.trim(),
            author: author.trim(),
            text: article.innerText || "",
            y: Math.round(rect.top + window.scrollY),
            index,
        };
    })
    """

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 760, "height": 2200},
                device_scale_factor=1,
                color_scheme="dark",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            cookies = load_playwright_cookies(cookies_from_browser)
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception as exc:
                    print(f"[x] could not add browser cookies to Playwright: {exc}")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("article", timeout=20000)
            page.wait_for_timeout(2500)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            articles: list[dict[str, object]] = []
            seen_snapshot_ids: set[str] = set()

            def collect_visible_articles() -> int:
                added = 0
                for article in page.evaluate(article_script):
                    status_id = extract_status_id(str(article.get("url") or ""))
                    if not status_id:
                        continue
                    snapshot_key = f"{status_id}:{article.get('y')}:{article.get('index')}"
                    if snapshot_key in seen_snapshot_ids:
                        continue
                    seen_snapshot_ids.add(snapshot_key)
                    articles.append(article)
                    added += 1
                return added

            collect_visible_articles()
            for selector in ("text=/Show this thread/i", "text=/Show more replies/i", "text=/Read .*repl/i"):
                try:
                    page.locator(selector).first.click(timeout=2500)
                    page.wait_for_timeout(1600)
                    collect_visible_articles()
                except Exception:
                    pass

            stable_rounds = 0
            max_rounds = max(6, min(18, max_posts * 3))
            for _ in range(max_rounds):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(900)
                added = collect_visible_articles()
                if added:
                    stable_rounds = 0
                else:
                    stable_rounds += 1
                if stable_rounds >= 3:
                    break
            browser.close()
    except Exception:
        return []

    unique_posts: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for article in articles:
        post = article_to_post(article)
        if not post or post["id"] in seen_ids:
            continue
        seen_ids.add(post["id"])
        unique_posts.append(post)

    target_id = extract_status_id(url)
    target_index = next((i for i, post in enumerate(unique_posts) if post["id"] == target_id), -1)
    if target_index < 0:
        return unique_posts[:max_posts]

    target = unique_posts[target_index]
    thread_handle = target.get("handle", "").lower()
    if not thread_handle:
        thread_handle = next((post["handle"].lower() for post in unique_posts if post.get("handle")), "")

    start = target_index
    while start > 0:
        previous = unique_posts[start - 1]
        previous_handle = previous.get("handle", "").lower()
        if thread_handle and previous_handle and previous_handle != thread_handle:
            break
        start -= 1

    posts: list[dict[str, str]] = []
    for post in unique_posts[start:]:
        post_handle = post.get("handle", "").lower()
        if thread_handle and post_handle and post_handle != thread_handle:
            if any(item["id"] == target_id for item in posts):
                break
            continue
        posts.append(post)
        if len(posts) >= max_posts:
            break

    if len(posts) <= 1 and not cookies_from_browser:
        print(
            "[x] only one public post was visible; if this is a thread, "
            "try --cookies-from-browser chrome"
        )
    return posts


def capture_embed(status_id: str, out_path: Path, *, width: int = 620) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit("playwright is required to capture X embeds") from exc

    url = (
        "https://platform.twitter.com/embed/Tweet.html"
        f"?id={status_id}&theme=dark&width={width}&hideThread=true&dnt=true"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[x] capturing embed {status_id} -> {out_path.name}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width + 60, "height": 2400}, device_scale_factor=2)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.evaluate(
            """() => {
                for (const el of document.querySelectorAll('*')) {
                    const cs = getComputedStyle(el);
                    if (cs.webkitLineClamp && cs.webkitLineClamp !== 'none') {
                        el.style.webkitLineClamp = 'unset';
                        el.style.display = 'block';
                    }
                    if (el.tagName === 'A' && el.textContent.trim() === 'Show more') {
                        el.style.display = 'none';
                    }
                }
            }"""
        )
        page.wait_for_timeout(300)
        page.locator("article").first.screenshot(path=str(out_path))
        browser.close()
    return out_path


def clamp_words(text: str, limit: int) -> str:
    words = re.findall(r"[\w’'-]+", text)
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit])


def title_from_post(post: dict[str, str]) -> tuple[str, str]:
    text = post.get("text", "")
    text = re.split(r"[.!?]\s+", text.strip())[0] or text
    words = clamp_words(text, 14)
    if not words:
        return "The Post", ""
    parts = words.split()
    if len(parts) >= 3:
        return " ".join(parts[:-1]), parts[-1]
    return words, ""


def shared_css() -> str:
    return f"""
{FONTS.read_text()}

:root {{
  --bg: #F4F2EC;
  --bg-top: #E9E6DF;
  --fg: #16140F;
  --ink-soft: rgba(20, 18, 14, 0.78);
  --primary: #C0552E;
  --rule: rgba(20, 18, 14, 0.28);
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ margin: 0; background: #555; font-family: 'Archivo', sans-serif; }}
.slide {{
  width: {SLIDE_W}px;
  height: {SLIDE_H}px;
  position: relative;
  overflow: hidden;
  background: linear-gradient(180deg, var(--bg-top) 0%, #F1EEE6 48%, var(--bg) 100%);
  color: var(--fg);
}}
.handle {{
  position: absolute;
  top: 68px;
  left: 78px;
  display: flex;
  align-items: center;
  gap: 14px;
}}
.handle span {{
  font-size: 27px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}}
.kicker {{
  position: absolute;
  top: 176px;
  left: 120px;
  right: 120px;
  display: flex;
  align-items: center;
  gap: 28px;
}}
.kicker::before, .kicker::after {{
  content: '';
  flex: 1;
  height: 2px;
  background: var(--rule);
}}
.kicker em {{
  font-style: normal;
  font-size: 25px;
  font-weight: 700;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--ink-soft);
  white-space: nowrap;
}}
.dots {{
  position: absolute;
  left: 0;
  right: 0;
  bottom: 86px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}}
.dots .dash {{
  width: 34px;
  height: 9px;
  border-radius: 5px;
  background: var(--primary);
}}
.dots .dot {{
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: rgba(20, 18, 14, 0.28);
}}
"""


def handle_markup() -> str:
    return """
  <div class="handle">
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none">
      <rect x="2" y="2" width="20" height="20" rx="5.5" fill="#16140F"/>
      <circle cx="12" cy="12" r="4.4" stroke="#F4F2EC" stroke-width="1.8"/>
      <circle cx="17.2" cy="6.8" r="1.3" fill="#F4F2EC"/>
    </svg>
    <span>@llmaw</span>
  </div>
"""


def dot_markup(active: int, count: int) -> str:
    return "\n".join('<div class="dash"></div>' if i == active else '<div class="dot"></div>' for i in range(1, count + 1))


def render_title_slide(post: dict[str, str], out_path: Path, count: int, title: str | None) -> Path:
    headline, accent = title_from_post(post)
    if title:
        bits = title.rsplit(" ", 1)
        headline, accent = (bits[0], bits[1]) if len(bits) == 2 else (title, "")
    source = f"{post.get('author', 'Source post')} {post.get('handle', '')}".strip()
    html_path = out_path.with_suffix(".html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    safe_headline = html.escape(headline)
    safe_accent = html.escape(accent)
    safe_source = html.escape(source)
    html_text = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{shared_css()}
.title-cluster {{
  position: absolute;
  left: 118px;
  right: 118px;
  top: 348px;
  text-align: center;
}}
.headline {{
  font-size: 92px;
  font-weight: 850;
  letter-spacing: -0.03em;
  line-height: 1.05;
}}
.headline .accent {{ color: var(--primary); }}
.source {{
  margin-top: 34px;
  font-size: 30px;
  font-weight: 650;
  color: var(--ink-soft);
}}
</style></head>
<body>
<div class="slide">
{handle_markup()}
  <div class="kicker"><em>From X</em></div>
  <div class="title-cluster">
    <h1 class="headline">{safe_headline}<br><span class="accent">{safe_accent}</span></h1>
    <div class="source">{safe_source}</div>
  </div>
  <div class="dots">{dot_markup(1, count)}</div>
</div>
</body></html>"""
    html_path.write_text(html_text)
    render_html_slide(html_path, out_path)
    return out_path


def render_post_slide(post: dict[str, str], embed_png: Path, out_path: Path, active: int, count: int) -> Path:
    html_path = out_path.with_suffix(".html")
    label = html.escape(f"{post.get('author', 'Source post')} {post.get('handle', '')}".strip())
    html_text = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{shared_css()}
.shot-wrap {{
  position: absolute;
  top: 148px;
  left: 40px;
  width: 1000px;
  height: 1044px;
  display: flex;
  align-items: center;
  justify-content: center;
}}
.tweet-shot {{
  max-width: 100%;
  max-height: 100%;
  border-radius: 28px;
  box-shadow: 0 30px 70px rgba(20, 18, 14, 0.22);
}}
.source-label {{
  position: absolute;
  left: 120px;
  right: 120px;
  bottom: 112px;
  text-align: center;
  font-size: 23px;
  font-weight: 800;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  color: var(--primary);
}}
</style></head>
<body>
<div class="slide">
{handle_markup()}
  <div class="shot-wrap"><img class="tweet-shot" src="{embed_png.resolve().as_uri()}"></div>
  <div class="source-label">{label}</div>
  <div class="dots">{dot_markup(active, count)}</div>
</div>
</body></html>"""
    html_path.write_text(html_text)
    render_html_slide(html_path, out_path)
    return out_path


def render_html_slide(html_path: Path, out_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit("playwright is required to render carousel slides") from exc
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H}, device_scale_factor=1)
        page.goto(html_path.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        page.locator(".slide").screenshot(path=str(out_path))
        browser.close()


def build_x_carousel(
    url: str,
    *,
    out_dir: Path,
    max_thread_posts: int,
    title: str | None,
    no_thread: bool,
    cookies_from_browser: str | None,
) -> Path:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    url = canonical_x_url(url)
    posts = [] if no_thread else discover_thread_posts(url, max_thread_posts, cookies_from_browser)
    if not posts:
        metadata = fetch_metadata(url, cookies_from_browser)
        embed_post = fetch_embed_post(url) if metadata is None else None
        posts = [embed_post or post_from_metadata(url, metadata)]

    first_metadata = fetch_metadata(posts[0]["url"], cookies_from_browser)
    if first_metadata:
        posts[0] = {**posts[0], **post_from_metadata(posts[0]["url"], first_metadata)}
    elif posts[0].get("text") == "Source post":
        embed_post = fetch_embed_post(posts[0]["url"])
        if embed_post:
            posts[0] = {**posts[0], **embed_post}

    total = len(posts) + 1
    slides: list[dict[str, object]] = []
    title_path = out_dir / "slide_01.png"
    render_title_slide(posts[0], title_path, total, title)
    slides.append({"index": 1, "type": "title", "path": str(title_path), "source_url": posts[0]["url"]})

    for idx, post in enumerate(posts, start=2):
        source_url = post["url"]
        metadata = fetch_metadata(source_url, cookies_from_browser)
        if metadata:
            post = {**post, **post_from_metadata(source_url, metadata)}
        elif post.get("text") == "Source post":
            embed_post = fetch_embed_post(source_url)
            if embed_post:
                post = {**post, **embed_post}

        if metadata_has_video(metadata):
            out_path = out_dir / f"slide_{idx:02d}.mp4"
            frame_path = out_dir / f"slide_{idx:02d}_frame.png"
            poster_path = out_dir / f"slide_{idx:02d}_poster.png"
            build_video_slide(
                source=source_url,
                tweet_embed_file=None,
                out_path=out_path,
                frame_out=frame_path,
                poster_out=poster_path,
                caption="",
                kicker="",
                source_label=post.get("handle", ""),
                active=idx,
                count=total,
                fit="cover",
                fps=30,
                mute=False,
                cookies_from_browser=cookies_from_browser,
                layout="post-video",
                post_author=post.get("author"),
                post_handle=post.get("handle"),
                post_text=post.get("text"),
                post_date=post.get("date"),
            )
            slides.append(
                {
                    "index": idx,
                    "type": "post-video",
                    "path": str(out_path),
                    "poster": str(poster_path),
                    "source_url": source_url,
                }
            )
        else:
            status_id = post["id"] or extract_status_id(source_url)
            if not status_id:
                raise SystemExit(f"could not find status id for {source_url}")
            embed_path = out_dir / f"source_post_{idx:02d}.png"
            capture_embed(status_id, embed_path)
            out_path = out_dir / f"slide_{idx:02d}.png"
            render_post_slide(post, embed_path, out_path, idx, total)
            slides.append({"index": idx, "type": "post", "path": str(out_path), "source_url": source_url})

    manifest = {
        "source_url": url,
        "thread_post_count": len(posts),
        "slide_count": total,
        "slides": slides,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[x] wrote manifest -> {manifest_path}")
    return manifest_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="X/Twitter status URL")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max-thread-posts", type=int, default=8)
    ap.add_argument("--title", help="Override generated title slide text")
    ap.add_argument("--no-thread", action="store_true", help="Only build from the supplied post")
    ap.add_argument(
        "--cookies-from-browser",
        default=os.environ.get("X_COOKIES_FROM_BROWSER"),
        help=(
            "Use browser cookies for X thread discovery and gated media "
            "(also configurable with X_COOKIES_FROM_BROWSER)"
        ),
    )
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("warning: ffmpeg not found; video posts will fail to render", file=sys.stderr)

    build_x_carousel(
        args.url,
        out_dir=args.out_dir,
        max_thread_posts=args.max_thread_posts,
        title=args.title,
        no_thread=args.no_thread,
        cookies_from_browser=args.cookies_from_browser,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
