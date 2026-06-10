#!/usr/bin/env python3
"""Capture an original X post from its x.com page (full text, no embed truncation).

Usage: python3.10 capture_tweet_page.py <tweet_url> <out.png>
"""
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "https://x.com/bcherny/status/2064431111154053187"
out_path = sys.argv[2] if len(sys.argv) > 2 else "assets/tweet_original.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 720, "height": 2600},
                            device_scale_factor=2,
                            color_scheme="dark",
                            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                                        "Chrome/126.0.0.0 Safari/537.36"))
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("article", timeout=20000)
    except Exception:
        page.screenshot(path=out_path + ".debug.png")
        print("article not found — wrote debug screenshot")
        sys.exit(1)
    page.wait_for_timeout(2500)

    # dismiss sign-in sheet if present
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    # clip the capture above the "Read N replies" bar
    card = page.locator("article").first
    box = card.bounding_box()
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
    page.screenshot(path=out_path, clip={
        "x": box["x"], "y": box["y"], "width": box["width"], "height": height,
    })
    print(f"saved {out_path}")
    browser.close()
