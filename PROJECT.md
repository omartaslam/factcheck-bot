# Fred • Fact Check — Project Reference

> **Purpose:** This document is the authoritative handoff reference. Any developer or AI assistant joining this project should be able to read this file and continue work without needing additional context. Updated automatically every 30 minutes during active development sessions.

**Last updated:** 2026-03-21 (session 15 — closed)

---

## 1. What is Fred?

Fred is a **WhatsApp fact-checking bot** for journalists, activists, and media professionals. Users send a claim, URL, image, audio note, or video link via WhatsApp and Fred returns a structured verdict with source evidence from 65+ global fact-check and news outlets across 6 world regions.

**Brand name:** Fred • Fact Check (the bot is called "Fred")
**Website:** https://fredcheck.com
**WhatsApp number:** +447863795638 (registered via Meta/WhatsApp Cloud API)
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
- `BALANCE` — shows remaining free checks or credit balance
- React 👍/👎 to verdict — stores accuracy rating
- Long-press verdict → Reply — stores text feedback comment
- `FEEDBACK` — (planned, not yet implemented — freeform command)

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
- Status: **Rejected** (sole trader insufficient — Meta requires stronger business documentation)
- **Decision:** Register as a Limited Company (Companies House, ~£50, 24hrs online) then resubmit
- Domain verified: fredcheck.com (meta-tag method, gold dot visible in WA)
- **Next:** Register Ltd → resubmit with certificate of incorporation → app review for `whatsapp_business_messaging`, `whatsapp_business_management`, `public_profile`

### Going live without verification — NO BLOCKERS
- Fred is live now at +447863795638, open beta, Stripe payments all working
- Verification only unlocks: green tick, higher messaging tiers (>250 conversations/day), app review
- 250 conversations/day cap is fine for early beta

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

### Immediate bugs (fixed this session)
1. ~~**"FACTCHECK PRO"** still in claim selection~~ — fixed (commit `466c9e8`) ✅
2. ~~**"free checks remaining today"**~~ — fixed, removed "today" ✅
3. ~~**Est. cost shown in claim selection**~~ — removed entirely ✅
4. ~~**X/Twitter false-positive unavailability**~~ — fixed (commit `a1299ea`) ✅
5. ~~**Double welcome on new user + early return**~~ — fixed ✅
6. **X video download** — vikas5914 RapidAPI key was expired; user updated key. yt-dlp still blocked by X. Text fallback via fxtwitter works. Video content itself unverified.

### Service monitoring (new task)
- Email alert when any API/service/cookies goes down (RapidAPI, Hive, SendGrid, FB/IG cookies)

### Blocked
5. **Cookie automation** — FB + IG cookies expire ~2026-04-03. Blocked on GitHub PAT `workflow` scope
6. **Meta app review** — submit once business verification approved

### Ready to implement
7. **QA automation suite** — ⏸ SHELVED. Infrastructure complete: `scripts/qa_runner.py` + `scripts/qa_fixtures.json`, 28 fixtures across all categories, POST `/admin/run-qa` endpoint live. Shelved 2026-03-21 because Claude's capacity limitations make the suite too slow and fragile to be a useful daily tool (~70 min runtime, context pressure, no mid-run visibility). Known quality issues: FALSE returned instead of UNVERIFIABLE for ambiguous claims; "vaccines kill more than COVID" returns FALSE not MISLEADING. Do not delete — park until either (a) Claude is faster/cheaper or (b) a lightweight 5–8 fixture subset is carved out for quick iteration.
8. **Service health monitoring** — email alert when RapidAPI/Hive/SendGrid/FB-IG cookies go down
9. **Split verdict into multiple WA messages**
10. **source_url stored in request_log** — ✅ done (commit `5149819`)
11. **WEBSITE_URL env var** — set to `https://fredcheck.com` in Railway
12. **fredcheck.co.uk** — add as custom domain in Railway
13. **FEEDBACK command** — freeform text (reactions + reply feedback already done)
14. **Persist `pending` state to DB** — lost on every redeploy
15. **SendGrid DMARC** — verify once DNS propagates
16. **Tavily language passes** — French/Urdu/Swahili
17. **Perplexity Sonar** — activate post-beta with `PERPLEXITY_API_KEY`
18. **Review COST_PER_CHECK_CENTS** — check against real dashboard data before beta go-live

---

## 12. Recently Completed Work (Session 15 — 2026-03-21)

- **Suppress qctest_ from new-user emails** (commit `4afaf25`):
  - `_notify_new_user()` returns early for any wa_id starting with `qctest_`
  - Fixes ~12 spam emails received per QA suite run during beta testing
  - `_send_daily_summary()` also filters `qctest_%` from check rows and new-user counts

- **QA runner — rich email report** (commit `1ee0671`):
  - `--email` flag: runs suite then sends single consolidated email to hello@fredcheck.com
  - Per-fixture email includes: input, extracted claims text, verdict reasoning snippet, failed checks only
  - `extract_claims_text(messages)` — parses numbered claim lines from pipeline output
  - `extract_verdict_text(messages)` — extracts VERDICT line + 3 lines of reasoning
  - Format designed for human review of pipeline quality (not just pass/fail counts)
  - Run: `python3 scripts/qa_runner.py --email`

- **QA run results** (two runs, consistent failures):
  - 10/12 fixtures pass, 65/67 checks pass
  - ❌ `twitter-text-only` — `contains: twttr` check wrong (tweet text not echoed in messages, just used as query input). Fixture assertion needs relaxing.
  - ❌ `youtube-video` — WHO press conference video (`h4cJMlYBOzA`) is unavailable on YouTube. Need replacement stable URL.
  - ✅ All text, BBC, Reuters, X video (text fallback), multi-claim, Arabic, unverifiable fixtures pass

- **Noted for later**: Full audit log for customers — store full verdict text + cited sources in request_log; HISTORY command or web view for customer check history.

- **QA runner `--email` fix**: SENDGRID_API_KEY not available in local shell — email only works when triggered via `/admin/run-qa` on Railway.

- **POST /admin/run-qa endpoint** (commit `d804eeb`):
  - Triggers full QA suite in background thread, emails results to hello@fredcheck.com
  - Returns immediately; results arrive ~25 min later
  - Optional body `{"id": "fixture-id"}` to run single fixture
  - `curl -X POST https://fredcheck.com/admin/run-qa -H "X-Admin-Token: qc-test-fred-2026"`

- **QA fixture set expanded from 12 → 28** (commits `7e4dce2`, `92ba266`):
  - New text fixtures: historical TRUE, climate denial, election fraud, statistical cherry-pick, NEEDS CONTEXT, contested geopolitical (Ukraine/Nord Stream), health misinfo (ivermectin), misattributed quote (Einstein), Arabic language input, satire detection
  - New URL fixtures: Al Jazeera article, unavailable content test, politically sensitive tweet
  - New platform fixtures: Facebook (George Galloway post), Instagram Reel, TikTok (Sky Sports), AI/deepfake TikTok (provisional)
  - 24 active, 0 placeholders remaining
  - Runner skips `skip: true` fixtures automatically

- **QA runner reliability fixes** (commits `c8b0124`, `abe7075`):
  - Subprocess timeout increased from 30 min → 2 hours (28 fixtures can take ~70 min)
  - Failure notification email now sent immediately if run fails or times out
  - stderr captured so failures are no longer silent

- **Quality observations from earlier runs** (12-fixture suite):
  - Fred defaults to FALSE when it can't verify something rather than UNVERIFIABLE — synthesis prompt issue to fix
  - "Vaccines kill more people than COVID" → FALSE (should be MISLEADING)
  - "UK secret plan to ban protests" → FALSE (should be UNVERIFIABLE)
  - Source counts consistently 55–83 per verdict ✅
  - Non-Western sources firing correctly on MENA claims ✅
  - X video text fallback solid ✅

- **Media type coverage gap identified**:
  - ❌ Image OCR (WhatsApp image), audio/voice note, carousel posts, out-of-context image not testable via current `/admin/qc` endpoint
  - Fix: extend `/admin/qc` to accept `image_url` field — Fred downloads and OCRs as if WhatsApp sent it
  - Decision: image/audio testing manual-only for now (can't simulate WhatsApp media via /admin/qc)

- **VCF contact card created** (`static/fred-check.vcf`, commit `b163fdc`):
  - For sharing with beta testers via WhatsApp
  - Includes embedded logo, +447863795638, website URL, brief description
  - Share via phone Contacts app → Share contact → WhatsApp (not as file attachment)

- **Meta WhatsApp Business profile** — updated ✅. Category: Public service. Description updated to: "Fred • AI Fact Checker / Truth Beyond Borders 🌍 / No default narrative - 70+ sources. / Send any link, image, video or claim for a balanced verdict. / fredcheck.com / hello@fredcheck.com"

- **Open beta confirmed** — Fred live at +447863795638, any number can message. Meta business verification is separate (green tick + higher tiers only).

- **Welcome message verified** via /admin/qc — new user flow correct end-to-end. Feedback number in welcome (+34643994740) is user's personal beta feedback number — intentional.

- **Verdict feedback vs welcome feedback** — two separate things: welcome has personal number for general beta feedback; verdict feedback (reactions + reply) stored in DB per-verdict.

## 12a. Previously Completed Work (Session 14 — 2026-03-21)

- **Geo-localised source preview** (commit `9ae76ba`):
  - `_GEO_SOURCE_BOOST` maps phone country prefixes to locally familiar sources
  - UK (+44) → BBC/Channel 4/Guardian/FullFact; MENA (+971/+966/etc) → Al Jazeera/Arab News; LatAm (+54/+57/etc) → Chequeado/BBC Mundo; etc.
  - `_geo_boost_sources(from_num)` longest-prefix match; boosted sources surface in cross-referencing preview
  - `_source_preview_msg(topic_text, from_num=...)` updated at both WA and platform call sites

- **BALANCE command** (commit `e4f8593`):
  - Users type `BALANCE` to check remaining credits without doing a fact-check
  - Free tier: "✓ Free checks remaining: 9 of 12"; Paid: "✓ Balance: $4.73"; Subscriber: "♾ Subscriber — unlimited access"

- **COST_PER_CHECK_CENTS env var** (commit `3515851`):
  - Drives check estimates in payment prompt — set in Railway, no redeploy needed
  - Default: `9` (cents per text check with 2× margin); payment prompt now shows ~11/$1, ~56/$5, ~111/$10, ~278/$25
  - Real cost is ~8–9¢/check (API only, 2× margin applied); WA conversation fee ($0.041) is absorbed by the business, not charged to user balance

- **Low balance warning** (commit `c7678cb`) — fires after paid check when balance < `COST_PER_CHECK_CENTS`; sends warning + full top-up prompt

- **Verdict reaction feedback** (commit `483cf82`) — `send()` returns WA message ID; stored in `request_log.wa_message_id`; incoming reactions matched to verdict and stored as `feedback` (+1/-1) + `feedback_emoji`

- **Reply-to-verdict text feedback** (commit `f8c2941`) — user long-presses verdict → Reply → text stored as `feedback_text` in `request_log`; Fred confirms with thank-you message

- **HELP updated** (commit `6f81929`) — added BALANCE command and feedback instructions

- **COST_PER_CHECK_CENTS default 9→19** (commit `9723cca`) — reflects full retail price (API + WA fee + infrastructure + 100% markup); displays ~5/$1, ~26/$5, ~53/$10, ~131/$25

- **Taglines toned down** (commit `9d3000d`) — truth-seeker framing, less confrontational; injustice implicit not stated

- **13 rotating taglines** (commit `b21a655`):
  - Website badge carousel: shuffled randomly on each page load, rotates every 4s
  - Verdict footer: random tagline replaces static "⚖️ pro/con debate" line
  - Taglines: Truth Beyond Borders · Facts don't have a postcode · Because the truth is a human right · Checking power, everywhere · Every lie unchallenged is an injustice · Beyond the Western headline · Tackling misinformation since birth · For those who refuse to be misled · Hold power to account, wherever it sits · No default narrative · Truth is resistance · Fact-checking is a form of justice · We don't just check facts. We fight for them.

- **Railway env vars confirmed set:**
  - `FREE_CHECKS_LIMIT=12` (beta value)
  - `HIVE_API_KEY` — confirmed added; Hive AI/deepfake detection now active

## 12b. Previously Completed Work (Session 13 — 2026-03-21)

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
