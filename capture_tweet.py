#!/usr/bin/env python3
"""Capture an original X/Twitter post as a high-res PNG via the official embed.

Usage: python3.10 capture_tweet.py <tweet_id> <out.png> [--theme dark|light] [--width 550]
"""
import sys
from playwright.sync_api import sync_playwright

tweet_id = sys.argv[1] if len(sys.argv) > 1 else "2064431111154053187"
out_path = sys.argv[2] if len(sys.argv) > 2 else "assets/tweet_original.png"
theme = "dark"
width = 550
if "--theme" in sys.argv:
    theme = sys.argv[sys.argv.index("--theme") + 1]
if "--width" in sys.argv:
    width = int(sys.argv[sys.argv.index("--width") + 1])

url = (f"https://platform.twitter.com/embed/Tweet.html"
       f"?id={tweet_id}&theme={theme}&width={width}&hideThread=true&dnt=true")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width + 60, "height": 2400},
                            device_scale_factor=2)
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(1500)

    # long posts are CSS line-clamped with a "Show more" link-out; unclamp them
    page.evaluate("""() => {
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
    }""")
    page.wait_for_timeout(500)

    card = page.locator("article").first
    card.screenshot(path=out_path)
    print(f"saved {out_path}")
    browser.close()
