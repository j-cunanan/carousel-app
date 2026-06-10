# LLMAW Carousel Automation

This repo renders LLMAW-branded carousel assets from HTML:

- `out/slide_NN.png` for static carousel pages
- `out/carousel.pptx` for Canva import
- `out/video_slide_02.mp4` for a branded video page inside a carousel

## One-time setup

```sh
uv run python -m playwright install chromium
```

## Static PNG/PPTX build

```sh
uv run python build.py
```

Use retina PNGs when needed:

```sh
uv run python build.py --scale 2
```

## One-URL X Carousel

Drop in one X/Twitter status URL:

```sh
uv run python build_x_carousel.py "https://x.com/OpenAI/status/2061887650391625870"
```

The script writes an ordered carousel folder to `out/x_carousel`:

- `slide_01.png`: branded title/hook slide
- `slide_02.png`: branded post slide for a normal post
- `slide_02.mp4`: branded post+video slide when the post has video
- `manifest.json`: ordered slide list and source URLs

By default it tries to detect same-author thread posts from the X page and creates one post/media slide for each detected part. Use `--no-thread` to force a single-post carousel, `--max-thread-posts` to cap a long thread, or `--title` to override the generated title slide.

X sometimes hides thread replies from anonymous browsers. If a URL is part of a thread but only one post is visible, let the workflow use your logged-in browser cookies:

```sh
uv run python build_x_carousel.py "https://x.com/OpenAI/status/2061887650391625870" \
  --cookies-from-browser chrome
```

For automation triggers that should still accept only the URL, set this once in the runtime environment:

```sh
export X_COOKIES_FROM_BROWSER=chrome
```

## Branded Video Slide

Use a local video:

```sh
uv run python build_video_slide.py \
  --source assets/video_sources/example.mp4 \
  --source-label "SOURCE VIDEO" \
  --caption "The source clip stays inside the LLMAW carousel frame."
```

Use an X/Twitter embed snippet as the full post context plus the video:

```sh
uv run python build_video_slide.py \
  --tweet-embed-file assets/tweet_embed.html \
  --layout post-video \
  --source-label "@claudeai on X" \
  --kicker "The post"
```

The embed file can contain the raw code copied from X's embed-post feature:

```html
<blockquote class="twitter-tweet">
  <p lang="en" dir="ltr">Post text... <a href="https://t.co/example">pic.twitter.com/example</a></p>
  &mdash; Claude (@claudeai)
  <a href="https://x.com/claudeai/status/2064394146916229443">June 9, 2026</a>
</blockquote>
<script async src="https://platform.x.com/widgets.js" charset="utf-8"></script>
```

Use an X/Twitter post URL:

```sh
uv run python build_video_slide.py \
  --source "https://x.com/claudeai/status/2064394146916229443" \
  --source-label "@claudeai on X" \
  --caption "Claude's launch video, framed as a carousel receipt."
```

If X gates the media, pass browser cookies through to `yt-dlp`:

```sh
uv run python build_video_slide.py \
  --source "https://x.com/claudeai/status/2064394146916229443" \
  --cookies-from-browser chrome \
  --source-label "@claudeai on X" \
  --caption "Claude's launch video, framed as a carousel receipt."
```

## Full Build Plus Video

`build.py` keeps the static PNG/PPTX path and can also emit the MP4 slide in one command:

```sh
uv run python build.py \
  --video-tweet-embed-file assets/tweet_embed.html \
  --video-layout post-video \
  --video-source-label "@claudeai on X" \
  --video-kicker "The post"
```

Video outputs:

- `out/video_frame_02.png`: the LLMAW frame used behind the clip
- `out/video_slide_02.mp4`: the carousel-ready MP4
- `out/video_slide_02_poster.png`: first-frame poster for previews
- `out/video_slide_02.json`: source and render manifest

The default video fit is `contain`, preserving the full source clip inside the branded media well. Use `--fit cover` or `--video-fit cover` when you want the clip to fill the well by cropping.
