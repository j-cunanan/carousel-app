#!/usr/bin/env python3
"""Generate LLMAW-branded cover art for carousel slides.

Supports two backends:
  1. OpenAI GPT Image 2.0 (default) — requires OPENAI_API_KEY
  2. xAI Grok Imagine — requires XAI_API_KEY or Hermes xAI OAuth token

Uses brand.json colors/style for prompt consistency.

Usage:
    uv run python generate_cover.py "Fable 5 changes everything"
    uv run python generate_cover.py "Why reasoning models win" --provider xai
    uv run python generate_cover.py "The prompt" --out assets/my_cover.png --style abstract
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BRAND_PATH = ROOT / "brand.json"
ASSETS = ROOT / "assets"


def load_brand() -> dict[str, Any]:
    """Load brand config for prompt guidance."""
    if BRAND_PATH.exists():
        return json.loads(BRAND_PATH.read_text())
    return {}


def build_prompt(topic: str, style: str) -> str:
    """Build an image generation prompt from topic + LLMAW brand.

    The LLMAW aesthetic is "Whiteout / ink on light" — cream paper
    background with dark ink typography and rust/terracotta accents.
    """
    brand = load_brand()
    colors = brand.get("colors", {})
    bg = colors.get("bg", "#F4F2EC")
    primary = colors.get("primary", "#C0552E")
    fg = colors.get("fg", "#16140F")

    style_direction = {
        "abstract": "abstract geometric composition, editorial magazine aesthetic",
        "typographic": "bold typographic layout on cream paper, ink-bleed texture",
        "minimal": "minimalist with dramatic negative space, single focal element",
        "illustrative": "editorial illustration, ink wash technique, grain texture",
        "photo": "cinematic photography with cream matte and grain overlay",
    }.get(style, style)

    return (
        f"Square editorial cover art for an Instagram carousel about '{topic}'. "
        f"Cream/off-white paper background ({bg}). "
        f"Dark ink ({fg}) and rust/terracotta accent color ({primary}). "
        f"{style_direction}. "
        f"High-end publication quality, 1080x1080 composition. "
        f"No text, no logos, no watermarks. "
        f"The image should feel like a premium print magazine cover — "
        f"textured paper, editorial gravitas, intellectual but not cold."
    )


def generate_openai(prompt: str, out_path: Path) -> Path:
    """Generate image via OpenAI GPT Image 2.0 (chat-based image output)."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("VCPH_OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY not set. Set it in ~/.hermes/.env or export it."
        )

    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit(
            "openai package not installed. Run: uv add openai"
        )

    client = OpenAI(api_key=api_key)

    print(f"Generating cover art via GPT Image 2.0...")
    response = client.images.generate(
        model="gpt-image-2",
        prompt=prompt,
        n=1,
    )

    # GPT Image 2 returns image data as b64_json (primary) or url
    image_data = response.data[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(image_data, 'b64_json') and image_data.b64_json:
        import base64
        out_path.write_bytes(base64.b64decode(image_data.b64_json))
        print(f"Saved {out_path}")
        return out_path
    elif hasattr(image_data, 'url') and image_data.url:
        import urllib.request
        urllib.request.urlretrieve(image_data.url, str(out_path))
        print(f"Saved {out_path}")
        return out_path

    raise SystemExit("GPT Image 2 returned no image data")


def _get_xai_token() -> str:
    """Get xAI bearer token from env or Hermes OAuth store."""
    # Prefer explicit API key
    api_key = os.environ.get("XAI_API_KEY")
    if api_key:
        return api_key

    # Try Hermes OAuth token via hermes CLI
    try:
        result = subprocess.run(
            ["hermes", "auth", "get-token", "xai-oauth"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            token_data = json.loads(result.stdout)
            return token_data.get("access_token", "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    raise SystemExit(
        "No xAI credentials found. Set XAI_API_KEY or run: hermes auth add xai-oauth"
    )


def generate_xai(prompt: str, out_path: Path) -> Path:
    """Generate image via xAI Grok Imagine API."""
    token = _get_xai_token()

    # xAI Images API — using the OpenAI-compatible endpoint
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit(
            "openai package not installed. Run: uv add openai"
        )

    client = OpenAI(
        api_key=token,
        base_url="https://api.x.ai/v1",
    )

    print(f"Generating cover art via Grok Imagine...")
    response = client.images.generate(
        model="grok-imagine-image",
        prompt=prompt,
        n=1,
    )

    image_url = response.data[0].url
    if not image_url:
        raise SystemExit("xAI returned no image URL")

    import urllib.request

    out_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(image_url, str(out_path))
    print(f"Saved {out_path}")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate LLMAW-branded cover art for carousel slides"
    )
    ap.add_argument("topic", help="The carousel topic/headline")
    ap.add_argument(
        "--provider",
        choices=["openai", "xai"],
        default="openai",
        help="Image generation backend (default: openai)",
    )
    ap.add_argument(
        "--out",
        "-o",
        type=Path,
        help="Output path (default: assets/cover_<topic_slug>.png)",
    )
    ap.add_argument(
        "--style",
        choices=["abstract", "typographic", "minimal", "illustrative", "photo"],
        default="abstract",
        help="Visual style direction (default: abstract)",
    )
    ap.add_argument(
        "--prompt-only",
        action="store_true",
        help="Print the prompt without generating (for manual use)",
    )
    ap.add_argument(
        "--size",
        default="1024x1024",
        help="Image size for OpenAI (default: 1024x1024)",
    )
    args = ap.parse_args()

    prompt = build_prompt(args.topic, args.style)

    if args.prompt_only:
        print(prompt)
        return 0

    out_path = args.out or ASSETS / f"cover_{re.sub(r'[^a-z0-9]+', '_', args.topic.lower()).strip('_')}.png"

    if args.provider == "openai":
        generate_openai(prompt, out_path)
    else:
        generate_xai(prompt, out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
