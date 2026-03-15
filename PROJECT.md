# FactCheck Pro — Project Handover Document

> **Last updated:** 2026-03-15
> **Version:** v3.2
> **Status:** Live on Railway, actively tested

---

## 1. What This Project Is

FactCheck Pro is a WhatsApp bot that fact-checks claims sent by users via WhatsApp message. Users send text, images, audio, documents, or URLs (articles, Facebook, Instagram, TikTok, YouTube, X/Twitter). The bot:

1. Extracts verifiable claims from the content (before asking user to confirm)
2. Shows the user a numbered list of claims and asks for confirmation (Y/N)
3. Researches each claim independently using Google Fact Check API + scraped fact-check sources + Tavily real-time search
4. Returns a per-claim verdict with evidence, sources, and a truth rating

The target audience is people who want to quickly verify claims circulating on WhatsApp — particularly around politics and Middle East conflicts.

---

## 2. Architecture

```
WhatsApp Business API
        ↓ webhook POST /webhook
    Flask (bot.py)
        ↓
    [process() handler]
        ├── Download media (WhatsApp API)
        ├── OCR / transcribe / frame-extract
        ├── assess_content_claims() → Sonnet → claims list
        ├── Store in pending{} dict → send claims_confirm_msg to user
        └── On "Y" reply → run_check() per claim
                ├── google_fc(claim) → Google Fact Check API
                ├── scrape_sites(claim) → multi-source scrape
                ├── tavily_search(claim) → real-time news
                └── analyse_claim() → Sonnet → verdict + sources
```

**Deployment:** Railway (PaaS), auto-deploy from GitHub `main` branch
**Process manager:** Gunicorn, 4 workers, `--timeout 120`
**Build config:** `nixpacks.toml` (apt packages: ffmpeg, libsm6, libxext6, libxrender-dev)

---

## 3. Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.13 |
| Web framework | Flask + Gunicorn |
| AI (analysis + claims) | Anthropic `claude-sonnet-4-6` |
| AI (OCR, image) | Anthropic `claude-haiku-4-5-20251001` |
| Audio transcription | OpenAI Whisper (`whisper-1`) |
| Video download | yt-dlp + vikas5914 RapidAPI |
| Video frame extraction | cv2 (OpenCV) + ffmpeg fallback |
| Real-time search | Tavily API |
| Fact-check API | Google Fact Check Tools API |
| Database | SQLite (persisted on Railway Volume at `/data/factcheck.db`) |
| Scheduling | APScheduler (WhatsApp token auto-refresh every 50 days) |
| Platform | WhatsApp Business API (Meta Graph API v19.0) |

---

## 4. Key Files

```
whatsapp-factcheck/
├── bot.py              ← ENTIRE application logic (3079 lines)
├── requirements.txt    ← Python dependencies
├── nixpacks.toml       ← Railway build config (apt packages + start command)
├── static/
│   └── index.html      ← Landing page with WhatsApp link (wa.me/447863795638)
├── v1/, v2/, v3/       ← Legacy versions (ignore)
└── PROJECT.md          ← This file
```

All logic is in `bot.py`. There is no separate config file — all configuration comes from environment variables.

---

## 5. Environment Variables (Railway)

### Required (bot won't work without these)

| Variable | Description |
|---|---|
| `WHATSAPP_TOKEN` | Meta Graph API bearer token (auto-refreshed every 50 days) |
| `PHONE_NUMBER_ID` | WhatsApp Business phone number ID |
| `VERIFY_TOKEN` | Webhook verification token (default: `factcheck_verify_123`) |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `OPENAI_API_KEY` | OpenAI API key for Whisper transcription |
| `GOOGLE_FACT_CHECK_API_KEY` | Google Fact Check Tools API key |
| `RAPIDAPI_KEY` | RapidAPI key for vikas5914 video downloader |

### Persistence & Admin

| Variable | Value | Description |
|---|---|---|
| `DB_PATH` | `/data/factcheck.db` | SQLite path — must be on Railway Volume |
| `ADMIN_NUMBER` | `34643994740` | WhatsApp number for admin alerts (no + prefix) |
| `FREE_CHECKS_LIMIT` | `9999` (testing) / `3` (production) | Free checks per user before paywall |
| `PROFIT_MARGIN` | `2.0` | Cost multiplier for billing (2.0 = 100% margin) |
| `APP_BASE_URL` | `https://web-production-1f0a4.up.railway.app` | Used for webhook URLs |

### Real-time Search

| Variable | Value | Description |
|---|---|---|
| `TAVILY_API_KEY` | `tvly-dev-h3gEy-...` | Tavily search API — free 1000/month |

### Monetisation (Stripe — NOT YET SET UP)

| Variable | Description |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `TOPUP_5_LINK` | Stripe Payment Link for $5 top-up |
| `TOPUP_10_LINK` | Stripe Payment Link for $10 top-up |
| `TOPUP_25_LINK` | Stripe Payment Link for $25 top-up |
| `SUB_LINK` | Stripe Payment Link for $9.99/month subscription |

### Multi-Platform (NOT YET ACTIVE)

| Variable | Description |
|---|---|
| `MESSENGER_PAGE_TOKEN` | Facebook Page token (Messenger + Instagram DMs) |
| `MESSENGER_VERIFY_TOKEN` | Messenger webhook verify token |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TWITTER_CONSUMER_KEY` | Twitter/X app consumer key (requires paid dev tier) |
| `TWITTER_CONSUMER_SECRET` | Twitter/X app consumer secret |
| `TWITTER_ACCESS_TOKEN` | Twitter/X bot access token |
| `TWITTER_ACCESS_SECRET` | Twitter/X bot access token secret |

### Source Toggles

Every fact-check source can be enabled/disabled from Railway without code changes:

```
SRC_SNOPES, SRC_FULLFACT, SRC_FACTCHECKORG, SRC_POLITIFACT, SRC_AFP,
SRC_ALJAZEERA, SRC_MEE, SRC_NOVARA, SRC_CANARY, SRC_ZETEO,
SRC_BBC, SRC_REUTERS, SRC_AP, SRC_GUARDIAN, SRC_CNN,
SRC_MEMO, SRC_NEWARAB, SRC_BTSELEM, SRC_BELLINGCAT, SRC_HRW,
SRC_AMNESTY, SRC_UNNEWS, SRC_HAARETZ, SRC_ARABNEWS, SRC_GRAYZONE,
SRC_MINTPRESS, SRC_INTERCEPT, SRC_DEMOCRACYNOW, SRC_DDN, SRC_CODEPINK,
SRC_OWENJONES, SRC_CORBYN, SRC_ZARASULTANA, SRC_FINKELSTEIN, SRC_MOATS,
SRC_GALLOWAY_SITE, SRC_PSC, SRC_972MAG, SRC_MONDOWEISS, SRC_EINTIFADA,
SRC_RESPSTATECRAFT, SRC_YENISAFAK, ...
```

Set any to `"false"` in Railway to disable. Default: all enabled.

Custom sources can be added without code changes:

```
CUSTOM_SOURCES="Name|https://site.com/search?q={q},Name2|https://site2.com/?s={q}"
```

---

## 6. Railway Infrastructure

| Item | Value |
|---|---|
| Platform | Railway (railway.app) |
| Project name | enchanting-wholeness |
| Project ID | `ec1bd295-60a7-4d3c-b2ea-00bfc5b10e79` |
| Service ID | `3ae3bd52-301e-4003-b2cd-291436c7af2d` |
| Environment ID | `ebb5147d-8292-4b55-bd76-6a2c1b3e6564` |
| Volume | `web-volume` mounted at `/data` |
| Personal API token | `bc2d9c22-2d89-458c-8c33-3635a57193c7` |
| Project API token | `a150de81-9f32-42e3-acba-b0369b041ae3` |
| Live URL | `https://web-production-1f0a4.up.railway.app` |
| GitHub repo | `https://github.com/omartaslam/factcheck-bot` (main branch) |

Auto-deploy: every push to `main` triggers a Railway redeploy.

---

## 7. Core Bot Flow

### Incoming message → claim extraction → confirmation

```python
# bot.py: process() function
1. Receive WhatsApp webhook → extract message type and content
2. For video URLs: download via vikas5914 RapidAPI → extract frames (cv2/ffmpeg) + audio (yt-dlp + Whisper)
3. For images: OCR via Claude Haiku → text
4. For audio: transcribe via Whisper → text
5. assess_content_claims(text, source_type)  # Sonnet call
   → returns {claims, checkable, reason, suggestions}
6. If not checkable: send no_claims_msg() → done
7. If checkable: store claims in pending{from_num} dict, send claims_confirm_msg()
8. User replies "Y" → run_check() with pre_claims=stored_claims
9. User replies "N" → cancel
```

### run_check() — the fact-check engine

```python
for each claim:
    google_results = google_fc(claim)          # Google Fact Check API
    scraped_content = scrape_sites(claim)      # Multi-source parallel scrape
    tavily_results = tavily_search(claim)      # Real-time Tavily search
    verdict = analyse_claim(claim, evidence)   # Sonnet analysis
    send verdict to user
```

### Supported content types

| Type | Processing |
|---|---|
| Text | Direct claim extraction |
| Image | Claude Haiku OCR → text → claims |
| Audio (voice note) | Whisper transcription → text → claims |
| Document | Haiku OCR → text → claims |
| URL (article) | HTML scrape → text → claims |
| URL (YouTube/TikTok/FB Reel/IG Reel) | yt-dlp download → cv2/ffmpeg frames → yt-dlp audio → Whisper → claims |
| URL (FB/IG non-video post) | yt-dlp skip_download + OG scrape → post text → claims |
| URL (X/Twitter) | fxtwitter API → post text → claims |

---

## 8. Video Pipeline Detail

Facebook/Instagram Reels and other social video URLs follow this chain:

```
URL detected as video (video_path_hints: watch/video/reel/shorts/clip/share/v/share/r/)
    ↓
vikas5914 RapidAPI → download video bytes
    ↓
cv2 frame extraction → fails on fragmented MP4 (moov atom not found)
    ↓ (fallback)
ffmpeg frame extraction at fixed offsets [0, 3, 7, 12, 20s]
    → Note: ffmpeg declared in nixpacks.toml but may not be in PATH on Railway
    ↓ (fallback if ffmpeg fails)
yt-dlp audio-only download (_ytdlp_audio_bytes) → .m4a DASH stream
    ↓
Whisper transcription → transcript text
    ↓ (fallback if all above fails)
_fb_ig_post_scrape() → OG metadata from Facebook externalhit headers
    ↓
assess_content_claims(text) → Sonnet claim extraction
```

**Known issue:** ffmpeg is in `nixpacks.toml` `aptPkgs` but was not found in Railway PATH during testing. Video frame analysis therefore falls through to yt-dlp audio + OG scrape fallback. This works for most content.

---

## 9. Claim Extraction (assess_content_claims)

The key function for pre-confirmation claim extraction uses `claude-sonnet-4-6`.

**Key prompt rules:**
- Claims must be 5–12 words, short and direct
- Use the speaker's own framing (not academic paraphrase)
- Do NOT infer context not explicitly stated
- Do NOT add background info (e.g. "Mark Carney is PM of Canada" if not stated in content)
- Max 6 claims per check

**Example output for a FB Reel video (verified working 2026-03-15):**
```
1. Persians are not Arabs
2. Shia Muslims are not Sunni Muslims
3. Mark Carney criticised America as a 'mafia state' at the World Economic Forum
4. Canada is America's greatest ally
5. Canada is a vassal state to America
```

---

## 10. Database Schema (SQLite)

Located at `/data/factcheck.db` (Railway Volume — persists across redeploys).

**Tables:**
- `users` — `phone`, `free_checks_used`, `credits_cents`, `subscription` (active/none), `created_at`, `last_active`
- `checks` — `id`, `phone`, `query_hash`, `source_type`, `cost_cents`, `billing_type`, `created_at`
- `payments` — `id`, `phone`, `amount_cents`, `stripe_payment_id`, `created_at`

Billing types: `free`, `credited`, `subscribed`

---

## 11. Billing / Monetisation

**Current state:** `FREE_CHECKS_LIMIT=9999` — effectively unlimited for testing.

**Intended model:**
- Users get N free checks (configurable via `FREE_CHECKS_LIMIT`, default 3)
- After free checks: pay-as-you-go via Stripe top-up links or $9.99/month subscription
- Cost to user = actual API cost × `PROFIT_MARGIN` (default 2.0 = 100% markup)

**To activate:**
1. Create Stripe account, create Payment Links for $5/$10/$25 top-up and $9.99/month sub
2. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `TOPUP_5_LINK`, `TOPUP_10_LINK`, `TOPUP_25_LINK`, `SUB_LINK` in Railway
3. Set webhook endpoint in Stripe dashboard: `https://web-production-1f0a4.up.railway.app/stripe-webhook`
4. Set `FREE_CHECKS_LIMIT=3` in Railway
5. Test payment flow end-to-end

**Stripe webhook handler:** `POST /stripe-webhook` in `bot.py` — already implemented, processes `checkout.session.completed` events to add credits.

---

## 12. Multi-Platform Support

Code is implemented for all platforms but most are dormant pending credentials:

| Platform | Status | What's needed |
|---|---|---|
| WhatsApp | ✅ LIVE | Running |
| Facebook Messenger | Code ready | Set `MESSENGER_PAGE_TOKEN` in Railway |
| Instagram DMs | Code ready | Same `MESSENGER_PAGE_TOKEN` (shared with Messenger) |
| Telegram | Code ready | Set `TELEGRAM_BOT_TOKEN` in Railway |
| Twitter/X DMs | Code ready | Paid Twitter Developer account (~$100/month), set TWITTER_* vars |

**Endpoints:**
- WhatsApp: `GET/POST /webhook`
- Messenger/Instagram: `GET/POST /messenger-webhook`
- Telegram: `POST /telegram-webhook`
- Twitter: `GET/POST /twitter-webhook`

---

## 13. Admin Features

- **Admin alerts:** If Anthropic/OpenAI API credits run out, an alert WhatsApp message goes to `ADMIN_NUMBER` (throttled to 1/hour per provider)
- **Token auto-refresh:** WhatsApp token refreshed every 50 days via APScheduler (requires `FB_APP_ID` and `FB_APP_SECRET`)
- **Admin number:** `34643994740` — receives credit exhaustion and error alerts

---

## 14. Outstanding Tasks

### High Priority

1. **TikTok text overlay OCR** — videos with styled/animated text overlays not captured. Current `analyze_video_frames` uses Haiku; switch to Sonnet for better OCR, or add pytesseract as dedicated OCR on frames.

2. **Stripe setup** — monetisation not live. Steps:
   - Create Stripe Payment Links ($5, $10, $25 top-up + $9.99/month sub)
   - Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, payment link env vars in Railway
   - Reset `FREE_CHECKS_LIMIT` to `3`

3. **Low credit user alert** — when a user's free checks are exhausted, they currently see the paywall message but admin is separately alerted. Should be clean and user-friendly with payment link prominently shown.

### Medium Priority

4. **ffmpeg on Railway** — declared in `nixpacks.toml` `aptPkgs` but not found in PATH. Investigate why; fix would unlock video frame extraction for FB Reels (currently falls back to audio-only).

5. **More accurate usage calculation** — review cost tracking completeness (does it capture all Sonnet calls including `assess_content_claims`?).

6. **Sponsor ads** — `SPONSOR_ADS` env var exists but ad rotation not shown in current flow. Review and activate if monetisation strategy includes ads.

### Lower Priority / Future

7. **Messenger/Telegram activation** — set tokens when ready to expand platforms.

8. **Twitter/X activation** — requires paid Twitter Developer account (~$100/month).

9. **Supporting website** — standalone fact-checking website as alternative channel (not WhatsApp-dependent).

10. **In-platform integrations** — explore native FB, TikTok, Instagram, Twitter bot integrations (beyond DMs).

11. **Project documentation** — this file. Keep updated as development continues.

---

## 15. How to Continue Development

### Local setup

```bash
git clone https://github.com/omartaslam/factcheck-bot
cd factcheck-bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your API keys
python bot.py
```

### Testing

The bot requires a live WhatsApp Business API connection. For local testing:
- Use ngrok to expose local port to WhatsApp webhook
- Or test specific functions by calling them directly in Python

### Deploying

Push to `main` branch — Railway auto-deploys.

```bash
git add bot.py
git commit -m "fix/feat: description"
git push
```

### Railway CLI (for logs/env)

```bash
# Install: npm i -g @railway/cli
railway login
railway logs --service 3ae3bd52-301e-4003-b2cd-291436c7af2d
railway variables  # view env vars
```

Or use Railway API directly:

```bash
curl -s -H "Authorization: Bearer bc2d9c22-2d89-458c-8c33-3635a57193c7" \
  "https://backboard.railway.app/graphql/v2" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ deployments(first: 5, input: { serviceId: \"3ae3bd52-301e-4003-b2cd-291436c7af2d\" }) { edges { node { id status createdAt } } } }"}'
```

---

## 16. Recent Git History

```
f8caa0a  fix: concise claim extraction — short direct assertions, no inferred context
23a3807  fix: per-claim search queries instead of shared claims[0]
afb8c55  fix: use Sonnet for claim extraction, max 6 claims, stricter enumeration
889b105  fix: yt-dlp audio-only fallback when video transcription fails
7717733  fix: retry Whisper as audio/mp4 (m4a) when video/mp4 returns 400
8de3ccf  fix: extract audio to MP3 via ffmpeg before Whisper transcription
6b93b46  fix: ffmpeg frame fallback without ffprobe (not in Railway PATH)
5da8521  fix: video analysis fallback when cv2/audio both fail (FB Reels)
900da4f  fix: detect Facebook Reels (/share/r/) as video links
722f709  feat: extract and enumerate claims BEFORE Y confirmation
7e2b17f  fix: add missing _try_download_url/_extract_video_url + use claim as search query
ad7ca3c  fix: improve video claim extraction — more frames, concise prompt, larger text window
```
