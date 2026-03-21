# Fred • Fact Check — Project Reference

> **Purpose:** This document is the authoritative handoff reference. Any developer or AI assistant joining this project should be able to read this file and continue work without needing additional context. Updated automatically every 30 minutes during active development sessions.

**Last updated:** 2026-03-21 (session 13 continued)

---

## 1. What is Fred?

Fred is a **WhatsApp fact-checking bot** for journalists, activists, and media professionals. Users send a claim, URL, image, audio note, or video link via WhatsApp and Fred returns a structured verdict with source evidence from 65+ global fact-check and news outlets across 6 world regions.

**Brand name:** Fred • Fact Check (the bot is called "Fred")
**Website:** https://fredcheck.com
**WhatsApp number:** registered via Meta/WhatsApp Cloud API
**Target audience:** Professional journalists, independent journalists, news/media outlets, activist organisations (B2B focus)

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3 |
| Web framework | Flask + Gunicorn |
| Deployment | Railway (auto-deploy on `git push main`) |
| Build system | nixpacks.toml |
| Database | PostgreSQL (Railway-managed) |
| AI — analysis | Anthropic Claude Sonnet 4.6 (`claude-sonnet-4-6`) |
| AI — OCR/fast tasks | Anthropic Claude Haiku 4.5 |
| AI — transcription | OpenAI Whisper |
| Search | Tavily (primary), Brave Search (secondary) |
| Video download | yt-dlp |
| Video frame extraction | cv2 (OpenCV) + ffmpeg |
| Messaging | WhatsApp Cloud API (Meta) |
| Email | SendGrid |

---

## 3. Repository

- **GitHub:** https://github.com/omartaslam/factcheck-bot
- **Main branch:** `main` (Railway deploys on push)
- **Primary file:** `bot.py` — all bot logic in one file
- **Static website:** `static/index.html` — fredcheck.com landing page
- **Config:** `nixpacks.toml`, `requirements.txt`, `Procfile`

---

## 4. Railway Infrastructure

| Item | Value |
|---|---|
| Personal token | `bc2d9c22-2d89-458c-8c33-3635a57193c7` |
| Project ID | `ec1bd295-60a7-4d3c-b2ea-00bfc5b10e79` |
| Service ID | `3ae3bd52-301e-4003-b2cd-291436c7af2d` |
| Environment ID | `ebb5147d-8292-4b55-bd76-6a2c1b3e6564` |
| Production URL | https://web-production-1f0a4.up.railway.app/ |

**Key env vars (set in Railway dashboard):**
- `WHATSAPP_TOKEN` — Meta WhatsApp Cloud API token
- `ANTHROPIC_API_KEY` — Claude API key
- `OPENAI_API_KEY` — Whisper transcription
- `TAVILY_API_KEY` — Tavily search
- `BRAVE_API_KEY` — Brave search fallback
- `DATABASE_URL` — PostgreSQL (auto-set by Railway)
- `WA_CONVERSATION_COST=0.041` — WhatsApp conversation cost per message (Europe/Spain)
- `FREE_CHECKS_LIMIT=9999` — currently unlimited (change once pricing decided)
- `DEV_AUTOSELECT_NUM=34643994740` — developer phone number for auto-bypass
- `DEV_AUTOSELECT_ON=true` — enables dev claim auto-selection bypass
- `WEBSITE_URL` — should be set to `https://fredcheck.com` (not yet set as of 2026-03-20)

---

## 5. Key Architecture — How a Message Is Processed

```
WhatsApp → webhook POST /webhook → Flask handler
    ↓
Determine message type: text | image | audio | video | url
    ↓
Extract content:
  text       → parse directly
  image      → OCR via Claude/OpenAI vision
  audio      → transcribe via Whisper → Claude fallback
  video (WA) → disabled — users send URLs instead
  URL        → classify as video_link or non-video
    video_link → yt-dlp download → frame extract + transcribe
    non-video  → FB/IG scrape | article fetch | og:metadata
    ↓
Claim extraction → Claude Sonnet (extract 1–N checkable claims)
    ↓
Claim selection → user picks which claims to check (free: 1, paid: all)
    ↓
For each claim:
  Search: Tavily advanced (main + regional) + Brave + optional Tavily social/Spanish
  Synthesis: Claude Sonnet — verdict + evidence + sources
  Debate: Claude Haiku ×2 (pro/con) — optional, shown as indicator in footer
  Neutralise: Claude Haiku — balance any AI bias
    ↓
Format verdict → send via WhatsApp
```

### Verdict output format

**Header:**
```
*Fred Check* _(Beta)_  |  Text
```

**Footer:**
```
──────────────────────
Cost: $0.0587  •  Fred Check *(Beta)*
⚖️ pro/con debate        ← only if debate ran
🌐 https://fredcheck.com
```

---

## 6. Content Extraction — Platform Coverage

### 6.1 Video links (video_domains list)
Platforms detected as video links: YouTube, TikTok, Twitter/X, Facebook video paths, Instagram Reels, Vimeo, Dailymotion, Rumble, Odysee, Bitchute, Telegram.

**Pipeline:** `_get_video_duration()` → `download_video_url()` → frame extract + transcribe → yt-dlp audio fallback → YouTube captions fallback (`_ytdlp_captions()`)

### 6.2 Facebook / Instagram
- **Video posts:** yt-dlp download → frames + audio; always also fetch post caption via `_fb_ig_post_scrape()`
- **Image posts (as MP4):** Facebook CDN wraps static images as MP4 — detected by OCRing first extracted frame when no audio transcript
- **Non-video posts:** `_fb_ig_post_scrape()` using facebookexternalhit/WhatsApp/Twitterbot UA rotation + IG cookie-auth fallback
- **FB/IG cookies:** stored as env vars, expire ~2026-03-30 (URGENT — must rotate)

### 6.3 Twitter / X
- `_fxtwitter_text()` — fxtwitter API for tweet text, date, quote tweets, and photo OCR

### 6.4 Generic article URLs
`fetch()` → `html_text()` for article body; extra URLs extracted from plain-text messages also fetched

### 6.5 Private / deleted / restricted content detection
`_is_content_unavailable(fb_og)` — signal-based detection:
- Checks `og:title` against `_UNAVAIL_TITLE_PHRASES` (FB/IG, Twitter/X, YouTube, TikTok, generic HTTP errors)
- Checks `og:description` against `_UNAVAIL_DESC_PHRASES`
- Checks redirect URL against `_UNAVAIL_URL_FRAGMENTS`
- Checks HTTP status code against `_UNAVAIL_HTTP_CODES = {403, 404, 410, 451}`
- Empty description + no image = private/deleted signal

`_check_url_unavailable(url)` — used for platforms without scrape data (YouTube, TikTok, Twitter/X when download fails): does a lightweight GET + og:tag extraction + `_is_content_unavailable()` check.

User sees: `🔒 This content appears to be private, deleted, or restricted and cannot be accessed.`

---

## 7. Key Functions Reference

| Function | Location | Purpose |
|---|---|---|
| `_fb_ig_post_scrape(url)` | ~line 1069 | Scrape FB/IG og: tags with UA rotation + IG cookie fallback |
| `_is_content_unavailable(og)` | ~line 1200 | Signal-based private/deleted detection |
| `_check_url_unavailable(url)` | ~line 1225 | Lightweight GET-based unavailability check for all platforms |
| `_fxtwitter_text(url)` | ~line 1257 | Tweet text, quote tweet, photo OCR via fxtwitter API |
| `_ytdlp_captions(url)` | before `_get_video_duration` | YouTube auto-caption extraction via yt-dlp |
| `_get_video_duration(url)` | ~line 965 | Returns duration in seconds; returns -1 on failure |
| `download_video_url(url)` | — | cobalt → yt-dlp → fxtwitter → og:metadata fallback chain |
| `_ytdlp_download(url)` | ~line 951 | yt-dlp download; returns bytes + metadata (incl. uploader/channel) |
| `_ytdlp_audio_bytes(url)` | — | Audio-only yt-dlp download for transcription fallback |
| `extract_video_frames(bytes)` | — | cv2 + ffmpeg frame extraction |
| `ocr_image(bytes)` | ~line 410 | Claude/OpenAI vision OCR |
| `transcribe(bytes, mime)` | — | Whisper + Claude audio fallback |
| `_og_metadata(url)` | ~line 1256 | Last-resort og:tag + og:image OCR from any URL |
| `fetch(url)` | ~line 384 | Simple GET → html_text; logs 403/404/410/451 distinctly |
| `send(to, text)` | — | Send WhatsApp message via Cloud API |
| `estimate_cost()` | — | WARN: hardcoded values ~12× too low — must fix before charging users |

---

## 8. Bot Messages & UX

### Welcome (new users)
```
Welcome to Fred • Fact Check 👋

I fact-check claims across 65+ sources from 6 world regions — with no default Western narrative.

Send me any of these:
• A claim, headline or quote
• A URL (news article, Facebook, Instagram, TikTok, YouTube)
• An image, video or voice note

You have 9999 free checks to try it out.

Type HELP anytime for a full guide.
🌐 https://fredcheck.com
```

### Claim selection
- `A`, `a`, `All`, `ALL` = check all claims
- `1 2 3`, `1,2,3`, `1, 2, 3` = multi-select
- Free users: shown all claims but restricted to picking 1

### Special commands
- `HELP` — full guide
- `FEEDBACK` — (planned, not yet implemented)

---

## 9. Cost Model

| Claim type | AI cost | + WA ($0.041) | Total |
|---|---|---|---|
| Text | ~$0.111 | $0.041 | ~$0.152 |
| Image | ~$0.119 | $0.041 | ~$0.160 |
| Audio | ~$0.123 | $0.041 | ~$0.164 |
| Video | ~$0.140 | $0.041 | ~$0.181 |

**Note:** `estimate_cost()` in code currently returns values ~12× too low. Must fix before charging users.

**Cost breakdown per claim:**
- Tavily searches: $0.064 (58% of total)
- Claude Sonnet synthesis: $0.035
- Claude claim extraction: $0.005
- Brave Search: $0.005
- Claude Haiku debate: $0.002
- Claude Haiku neutralise: $0.0002

---

## 10. Business / Meta Status

### Meta Business Verification
- Status: **In review** (submitted 2026-03-19, ~2 working days)
- Business: Fred Check (sole trader, registered HMRC 2026-03-19)
- Documents: HMRC acknowledgment + Monzo Business bank statement
- Domain verified: fredcheck.com (meta-tag method, gold dot visible in WA)
- **Next:** Once approved → submit app review for `whatsapp_business_messaging`, `whatsapp_business_management`, `public_profile`

### External Services
| Service | Status | Notes |
|---|---|---|
| Tavily | Active PAYG | $0.008/credit; switch to Project plan at >470 claims/month |
| Brave Search | Active | Already in use |
| Perplexity Sonar | In codebase, inactive | Activate post-beta with `PERPLEXITY_API_KEY` |
| SendGrid | Free trial until 2026-05-18 | 100 emails/day |
| FB/IG cookies | Refreshed 2026-03-20, expire ~2026-04-03 | ✅ both done — next rotation due ~2026-04-01 |

---

## 11. Outstanding Tasks (Priority Order)

### Urgent
1. **FB/IG cookies rotation** — expires ~2026-03-30 (~10 days).

   **Status:** FB + IG cookies both refreshed 2026-03-20 ✅. Next rotation due ~2026-04-01.

   **Manual rotation steps (do every ~10 days until automation is live):**
   1. Install "Get cookies.txt LOCALLY" extension (Chrome or Firefox)
   2. Log into Facebook → click extension on `facebook.com` → **Export** → saves `facebook.com_cookies.txt`
   3. `base64 -w 0 facebook.com_cookies.txt` → copy output
   4. Railway dashboard → service → Variables → update `FB_COOKIES_B64` → Save
   5. Repeat for Instagram: log into Instagram → export from `instagram.com` → encode → update `IG_COOKIES_B64`
   6. Railway auto-redeploys; if not, trigger manually

   **Permanent automation (blocked):** `scripts/refresh_cookies.py` + `.github/workflows/refresh-fb-ig-cookies.yml` built (commit `94a2ce4`, not yet pushed). Needs: GitHub PAT `workflow` scope → push → add 9 GitHub secrets (`FB_EMAIL`, `FB_PASSWORD`, `IG_USERNAME`, `IG_PASSWORD`, `RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENV_ID`, `RAILWAY_SERVICE_ID`, `SENDGRID_API_KEY`) → dedicated FB/IG account with 2FA disabled.

### Blocked / Pending Decision
2. **Pricing / free claims strategy** — must decide before Stripe:
   - How many free claims for regular vs B2B users?
   - Time-limited trial (7 days) vs fixed claim count?
   - Two-tier search quality for free vs paid?
3. **Meta app review** — submit once business verification approved

4. **Stripe — COMPLETE** ✅
   - Payment Links live: $1/$5/$10/$25 (live mode)
   - Webhook: `checkout.session.completed` → `https://web-production-1f0a4.up.railway.app/stripe-webhook`
   - All env vars in Railway: `TOPUP_1_LINK`, `TOPUP_5_LINK`, `TOPUP_10_LINK`, `TOPUP_25_LINK`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
   - No subscription — real cost ~$0.185/check; $9.99/mo breaks even at only 54 checks
   - **Pending:** test payment prompt (set `FREE_CHECKS_LIMIT=0`), set final `FREE_CHECKS_LIMIT` value

### Ready to Implement
5. **Test Hive AI/deepfake detection** — `HIVE_API_KEY` needs adding to Railway.
6. **QA automation suite** — big task. End-to-end testing across: content extraction (all platforms/input types), claim formulation, search quality, source diversity, bias neutralisation, verdict accuracy, edge cases, latency, cost. Build fixture library + Claude-as-judge scoring + nightly GitHub Action regression run. Uses existing `/admin/qc` endpoint as foundation.
5. **Rotating tagline carousel on fredcheck.com** — add more straplines beneath/alongside "Truth Beyond Borders". Confirmed taglines so far: "Tackling misinformation since birth". Candidates: "Facts don't have a postcode", "Beyond the Western headline", "No default narrative", "Six regions. One truth.", "Every story has another side", "Built for those who ask questions", "The antidote to algorithmic bias", "Checking power, everywhere", "For journalists who dig deeper", "Where facts meet all perspectives".
6. **Split verdict into multiple WA messages** — Meta charges per 24hr conversation not per message, so splitting is free. Improves readability. Discuss format next session.
5. **Stripe setup** — Payment Links, `TOPUP_LINK`/`SUB_LINK` env vars, webhook handler
5. **Fix `estimate_cost()`** — values ~12× too low; must fix before charging
6. **FREE_CHECKS_LIMIT** — change from 9999 to agreed number post pricing decision
7. **WEBSITE_URL env var** — set to `https://fredcheck.com` in Railway
8. **fredcheck.co.uk** — add as custom domain in Railway
9. **User feedback system** — `FEEDBACK` command, 👍/👎 rating buttons, store in DB
10. **Persist `pending` state to DB** — currently lost on every redeploy
11. **SendGrid DMARC** — verify once DNS propagates
12. **Tavily language passes** — French/Urdu/Swahili (English-only currently)
13. **Perplexity Sonar** — activate post-beta with `PERPLEXITY_API_KEY`

---

## 12. Recently Completed Work (Session 13 — 2026-03-21)

- **RATING RULE ON SOURCE FRAMING** added to synthesis prompt (commit `51c3afb`):
  - Claude was conflating source video/post framing with the extracted claim
  - e.g. "first responders reported secondary explosions" rated MISLEADING because video implied planted bombs
  - Fix: judge the claim as stated, never downgrade because the source makes a further unjustified leap
  - Tested: 9/11 claim now correctly returns TRUE

- **Message sequencing fixes** (commits `0bd759b`, `2d4f34a`):
  - 🔬 Running OSINT verification... now a separate message, sent *before* cross-referencing
  - — CLAIM X/Y — now a separate message before each verdict, not prepended to verdict body

- **Removed auto-injected AI/deepfake claim for video** (commit `b0c0185`):
  - Was adding "Is this video real and not AI-generated or manipulated?" as final claim
  - Redundant — Hive OSINT covers this; Claude is unqualified to answer it

- **Fixed duplicate daily summary emails** (commit `c3403a1`):
  - Old polling thread fired on every Railway redeploy after 07:00 UTC (last_sent reset to None)
  - Replaced with APScheduler cron job, misfire_grace_time=None — fires once at 07:00 UTC only

- **Hive OSINT now runs on WA video uploads** (commits `2bf11ab`, `9def045`):
  - Video uploads had no image_bytes/source_url so OSINT was skipped entirely
  - Fix: extract middle frame (40% through video) and pass as image_bytes for Hive
  - Middle frame chosen over first frame — avoids title cards, more representative of content
  - Latency impact: zero (OSINT runs in background thread in parallel with claim extraction)

## 12b. Previously Completed Work (Session 12 — 2026-03-20)

- **Daily usage summary email** (`_send_daily_summary()`, commit `13a2b37`):
  - Background scheduler thread fires at 07:00 UTC daily, reports previous day
  - Content: total checks, cost, active/new users, per-user claim breakdown with ratings
  - Manual trigger: `POST /admin/daily-summary` with `X-Admin-Token: qc-test-fred-2026`
  - Optional body `{"date": "YYYY-MM-DD"}` for specific day

- **Daily free check limit** (commit `12cac8b`):
  - 15 checks/day, resets at midnight UTC
  - `free_checks_date` column added; `_daily_free_used()` handles daily reset
  - **Railway**: set `FREE_CHECKS_LIMIT=15`

- **Updated welcome message** (commit `0cc275e`):
  - `_(BETA)_` in title, "I'm FRED" intro, beta footer with contact details, free checks count removed

- **New beta user email notification** (`_notify_new_user()`, commit `1e7a7e3`):
  - Extracts WhatsApp profile name from `contacts[0].profile.name` in webhook payload
  - Stores `profile_name` in `platform_users` DB (migration: `ALTER TABLE platform_users ADD COLUMN profile_name TEXT`)
  - On new user: async email to `hello@fredcheck.com` — number, display name, join timestamp
  - Subject: `🆕 New beta user: <name or number>`

### Previously (Session 11 — 2026-03-20)

- **Automated FB/IG cookie refresh** (`scripts/refresh_cookies.py` + `.github/workflows/refresh-fb-ig-cookies.yml`):
  - Playwright headless browser logs into FB and IG with stored credentials
  - Exports cookies in Netscape format (yt-dlp compatible), base64-encodes, pushes to Railway via GraphQL API
  - Runs every Monday 03:00 UTC — well within ~14-day cookie lifespan
  - On failure: emails hello@fredcheck.com via SendGrid + uploads login screenshots as GitHub artifacts
  - Supports `workflow_dispatch` manual trigger with per-platform skip options
  - Committed as `94a2ce4` but **not yet pushed** — GitHub PAT needs `workflow` scope added first

### Previously (Session 10 — 2026-03-20)

- **Holistic content unavailability detection** — fully implemented (session 10) across all platforms:
  - Expanded `_UNAVAIL_TITLE_PHRASES`, `_UNAVAIL_DESC_PHRASES`, `_UNAVAIL_URL_FRAGMENTS` to cover FB/IG, Twitter/X, YouTube, TikTok, generic HTTP errors
  - Added `_UNAVAIL_HTTP_CODES = {403, 404, 410, 451}`
  - Added `_check_url_unavailable(url)` — lightweight GET + HTTP code + og:tag check for platforms with no scrape data
  - Plugged into video_link `else:` branch (no bytes, no metadata)
  - Plugged into video_link `elif metadata:` branch (metadata signals checked before audio attempt)
  - Updated `fetch()` to log distinctly on unavailability HTTP codes

- **Previously (sessions 9–10):**
  - Meta business verification submitted with Monzo Business bank statement
  - Multiple content extraction bugs fixed: FB image-as-MP4, post caption after video, carousel thumbnails, WhatsApp image caption field, OCR-all-candidates (not stop-at-first), extra URL fetching, YouTube captions fallback
  - `_fxtwitter_text()` enhanced with quote tweet + photo OCR
  - `_fb_ig_post_scrape()` enhanced with IG cookie-auth fallback + redirect tracking
  - Private/deleted detection evolved from char-count heuristic → signal-based `_is_content_unavailable()`
  - `_get_video_duration()` returns -1 (not 0) on failure to distinguish "no video" from "zero-length"
  - Website: gold numbered labels 01–06, FB domain meta-tag, sign-in gate modal
  - Dev auto-select bypass: `DEV_AUTOSELECT_ON` + `DEV_AUTOSELECT_NUM` env vars

---

## 13. Development Conventions

- **Deploy:** `git push origin main` → Railway auto-deploys (~60–90s). Wait before testing.
- **Logs:** Railway dashboard → enchanting-wholeness → Deployments → View logs
- **Dev bypass:** Set `DEV_AUTOSELECT_ON=true` + `DEV_AUTOSELECT_NUM=<your number>` to skip claim selection in testing
- **File structure:** All logic is in `bot.py`. No separate modules.
- **AI model selection:** Sonnet 4.6 for quality tasks (synthesis, claim extraction), Haiku 4.5 for fast/cheap tasks (OCR, debate, neutralise)
- **Error handling:** Never silently swallow errors that affect output quality. Log at appropriate level (info/warning/error).
- **Content extraction philosophy:** Always map ALL platforms and ALL states before implementing any fix. Never fix one bug at a time reactively — breadth-first, then implement once.
- **Verdict philosophy:** TRUE means TRUE. Don't hedge defensively when retrieval is thin — fix the search, not the verdict.
- **PROJECT.md updates:** Updated automatically every 30 minutes during active sessions and pushed to GitHub.

---

## 14. Key Design Decisions (recorded for continuity)

| Decision | Rationale |
|---|---|
| WhatsApp-first, not app | Zero friction for target markets (Africa, LatAm, MENA) where WhatsApp penetration is near-total |
| Video upload disabled | WhatsApp video uploads are unreliable and large; users send URLs instead |
| Single `bot.py` file | Simplicity for Railway deployment; refactor to modules only when necessary |
| Sonnet for synthesis | Quality critical; haiku degrades verdict accuracy noticeably |
| 65+ sources across 6 regions | USP is non-Western narrative — must maintain diversity |
| B2B target | Higher LTV, more willing to pay, professional credibility matters more |
| Signal-based unavailability | Char-count heuristics too fragile; og:title phrases are explicit platform signals |
| fxtwitter for Twitter/X | Twitter API is expensive; fxtwitter gives tweet text + metadata for free |
| Debate indicator (not inline) | Keeps verdict clean; signals reasoning depth without cluttering output |
