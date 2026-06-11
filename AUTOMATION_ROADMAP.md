# Carousel Automation Roadmap

## Goal

Move the app from a one-URL carousel renderer to a mostly autonomous content system:

1. Watch high-signal X accounts.
2. Suggest only posts worth turning into LLMAW carousels.
3. Ask for a quick human approval.
4. Build the carousel automatically after approval.
5. Publish the approved carousel to Instagram without manual asset handling.

The human should spend time deciding what is worth running, not checking X, downloading media, or pushing files between tools.

## Operating Model

```text
Configured X follows
  -> story scout
  -> scored candidate queue
  -> Telegram / Codex approval
  -> carousel build workflow
  -> final review / optional approval
  -> Instagram publish
  -> run log + metrics
```

## Phase 1: Story Scout

Status: implemented as `story_scout.py`.

- Keep a local allowlist of X handles in `story_sources.example.json`.
- Scan recent posts from those handles with xAI `x_search`.
- Normalize posts into a durable queue at `out/automation/candidates.json`.
- Score candidates by engagement, keywords, media, and thread/story signals.
- Preserve approval state across scans so the same post is not repeatedly reset.

Useful defaults:

- `min_score`: 55
- `lookback_hours`: 24
- `max_posts_per_account`: 5
- Queue statuses: `candidate`, `approved`, `rejected`, `built`, `failed`

## Phase 2: Human Approval

Status: first Telegram path implemented.

- Send candidate notifications through Telegram when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured.
- Telegram messages include Approve & build / Reject inline buttons.
- `story_scout.py telegram-poll` processes Telegram button callbacks.
- CLI approval also exists for desktop use.

Codex mobile can be a later notification surface if there is a stable Codex-side automation or mobile push mechanism available to this workspace. Telegram is the practical v1 because it supports bot messages and approval callbacks today.

## Phase 3: Approved Build Orchestration

Status: implemented for carousel asset generation.

- Approving a candidate can call the existing `build_x_carousel.py` workflow.
- Each build writes to `out/automation/builds/<candidate_id>/`.
- The candidate queue records build status, manifest path, and failure details.
- Thread discovery, title enrichment, post rendering, and video slide handling remain owned by `build_x_carousel.py`.

## Phase 4: Pre-Publish QA

Status: planned.

- Validate that every slide file in `manifest.json` exists.
- Check image and video dimensions.
- Generate a compact preview contact sheet.
- Optionally require a second approval before publishing if the build includes generated art or video.

## Phase 5: Instagram Auto-Publish

Status: planned.

- Add an Instagram publisher around the Meta/Instagram Graph API.
- Required runtime credentials will likely include:
  - Instagram Business or Creator account connected to a Facebook Page.
  - Long-lived Meta access token with content publishing permissions.
  - Instagram business account ID.
- Publish flow should be isolated behind a `publish_instagram.py` command so credentials and API behavior do not leak into the scout or renderer.
- The publisher should support dry runs, publish manifests, retryable failures, and an explicit "do not publish duplicates" guard.

## Phase 6: Scheduling And Reliability

Status: planned.

- Run `story_scout.py scan --notify` on a schedule.
- Run `story_scout.py telegram-poll --watch` as a lightweight approval listener.
- Store a run log for scans, notifications, builds, and publishes.
- Add duplicate suppression across canonical X URLs.
- Add basic metrics: candidates found, approvals, builds, publishes, failures, and median time from X post to published carousel.

## Next Milestones

1. Add visual QA for approved builds.
2. Add Instagram publisher in dry-run mode.
3. Wire publisher to `Approve & build & publish` after QA passes.
4. Add scheduling docs for launchd, cron, or a hosted worker.
