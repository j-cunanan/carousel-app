#!/usr/bin/env python3.10
"""IG carousel build pipeline.

    carousel_src.html + assets/archivo.css
        -> carousel.html          (fonts inlined, deterministic render)
        -> out/slide_NN.png       (1080x1350 each, via Playwright)
        -> out/carousel.pptx      (one slide per page, for Canva import)

Usage:
    python3.10 build.py            # full build
    python3.10 build.py --scale 2  # 2160x2700 retina PNGs (PPTX stays 1080x1350)

Asset capture (run once per story):
    python3.10 capture_tweet_page.py <tweet_url> assets/tweet_original.png
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from pptx import Presentation
from pptx.util import Emu

ROOT = Path(__file__).parent
SRC = ROOT / "carousel_src.html"
FONTS = ROOT / "assets" / "archivo.css"
HTML = ROOT / "carousel.html"
OUT = ROOT / "out"

SLIDE_W, SLIDE_H = 1080, 1350
EMU_PER_PX = 9525  # 96 dpi


def inject_fonts() -> None:
    html = SRC.read_text().replace("/* __FONTS__ */", FONTS.read_text())
    HTML.write_text(html)
    print(f"[1/3] fonts inlined -> {HTML.name}")


def render_slides(scale: int) -> list[Path]:
    OUT.mkdir(exist_ok=True)
    pngs = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": SLIDE_W + 100, "height": SLIDE_H + 100},
            device_scale_factor=scale,
        )
        page.goto(HTML.as_uri())
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(400)  # font/image settle

        slides = page.locator(".slide")
        count = slides.count()
        for i in range(count):
            out_path = OUT / f"slide_{i + 1:02d}.png"
            slides.nth(i).screenshot(path=str(out_path))
            pngs.append(out_path)
            print(f"[2/3] rendered {out_path.name} ({SLIDE_W * scale}x{SLIDE_H * scale})")
        browser.close()
    return pngs


def export_pptx(pngs: list[Path]) -> Path:
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W * EMU_PER_PX)
    prs.slide_height = Emu(SLIDE_H * EMU_PER_PX)
    blank = prs.slide_layouts[6]
    for png in pngs:
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(str(png), 0, 0,
                                 width=prs.slide_width, height=prs.slide_height)
    out_path = OUT / "carousel.pptx"
    prs.save(out_path)
    print(f"[3/3] exported {out_path} ({len(pngs)} pages)")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=1, help="PNG pixel density multiplier")
    args = ap.parse_args()

    inject_fonts()
    pngs = render_slides(args.scale)
    if not pngs:
        print("no .slide elements found", file=sys.stderr)
        return 1
    export_pptx(pngs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
