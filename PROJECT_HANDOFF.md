# FactCheck Pro — Project Handoff Document
*Last updated: 2026-03-15 (session 2). For any AI agent or developer picking up this project.*

---

## What This Project Is

**FactCheck Pro v3.2** is a WhatsApp chatbot + web app that fact-checks claims, images, audio, video, and URLs. Users send a message and receive a structured verdict: truth rating, evidence, source links, truth score (1–10), and propaganda technique analysis.

---

## Deployment

| Item | Value |
|---|---|
| **Live URL** | https://web-production-1f0a4.up.railway.app/ |
| **Web UI** | https://web-production-1f0a4.up.railway.app/web |
| **Platform** | Railway (Hobby plan — $5/month) |
| **Railway project** | enchanting-wholeness |
| **GitHub repo** | https://github.com/omartaslam/factcheck-bot (branch: main) |
| **Deploy method** | Push to GitHub main → Railway auto-deploys |
| **Runtime** | Python/Flask + Gunicorn (4 workers, 120s timeout) |
| **Build config** | `nixpacks.toml` — apt installs ffmpeg, libsm6, libxext6, libxrender-dev |
| **Process** | `gunicorn bot:app --workers 4 --bind 0.0.0.0:$PORT --timeout 120` |

---

## Tech Stack

- **Python 3** / Flask + Gunicorn
- **Anthropic Claude** — `claude-sonnet-4-6` for synthesis/verdict, `claude-haiku-4-5-20251001` for OCR, debate pro/con, neutralization, claim extraction
- **OpenAI** — `whisper-1` for audio transcription (primary), `gpt-4o-mini` for OCR fallback, `gpt-4o` for analysis fallback
- **Google Fact Check Tools API** — cross-references claims against published fact-checks
- **yt-dlp** — downloads/extracts data from YouTube, Facebook, Instagram, TikTok, Twitter/X
- **RapidAPI** — TikTok downloader (7scorp), Twitter/Facebook downloader (vikas5914)
- **OpenCV (cv2) + PIL** — video frame extraction
- **APScheduler** — background job for WhatsApp token auto-refresh (every 50 days)
- **WhatsApp Business API** — via Meta Graph API v19.0
- **SQLite** — user accounts, billing, payment history (see DB_PATH env var)
- **Stripe** — payments, top-up, subscriptions

---

## Main File: `bot.py`

Single-file Flask app (~1600 lines). Key sections:

### Environment Variables (all set in Railway)

| Variable | Purpose |
|---|---|
| `WHATSAPP_TOKEN` | Meta Graph API access token |
| `PHONE_NUMBER_ID` | WhatsApp Business phone number ID |
| `VERIFY_TOKEN` | Webhook verify token (default: `factcheck_verify_123`) |
| `ANTHROPIC_API_KEY` | Claude API key |
| `OPENAI_API_KEY` | OpenAI API key (Whisper + GPT-4o fallback) |
| `GOOGLE_FACT_CHECK_API_KEY` | Google Fact Check Tools API key |
| `FB_APP_ID` | Facebook App ID (default: `913551238207108`) |
| `FB_APP_SECRET` | Facebook App Secret (for token auto-refresh) |
| `FB_COOKIES_B64` | Base64-encoded Netscape-format Facebook cookies (for yt-dlp) |
| `IG_COOKIES_B64` | Base64-encoded Instagram cookies (for yt-dlp) |
| `RAPIDAPI_KEY` | RapidAPI key for TikTok/Twitter downloaders |
| `ADMIN_NUMBER` | WhatsApp number for API credit alerts (international format, no +) |
| `DB_PATH` | SQLite DB path (default `/tmp/factcheck.db` — resets on redeploy; set to Railway Volume path for persistence) |
| `SRC_*` | Toggle individual fact-check sources on/off (see Sources section) |
| `CUSTOM_SOURCES` | Add extra sources without code changes |

### Billing / Monetisation Variables (set in Railway)

| Variable | Default | Purpose |
|---|---|---|
| `FREE_CHECKS_LIMIT` | `3` | Free fact-checks per WhatsApp number before payment required |
| `PROFIT_MARGIN` | `2.0` | Multiplier on raw API costs charged to users (2.0 = 100% margin) |
| `STRIPE_SECRET_KEY` | — | Stripe secret key (`sk_live_...`) |
| `STRIPE_WEBHOOK_SECRET` | — | Stripe webhook signing secret (`whsec_...`) |
| `TOPUP_5_LINK` | — | Stripe Payment Link URL for $5 top-up |
| `TOPUP_10_LINK` | — | Stripe Payment Link URL for $10 top-up |
| `TOPUP_25_LINK` | — | Stripe Payment Link URL for $25 top-up |
| `SUB_LINK` | — | Stripe Payment Link URL for $9.99/month subscription |
| `SPONSOR_ADS` | — | Pipe-separated ad lines shown on free tier responses e.g. `Ad text 1\|Ad text 2` |

### Stripe Setup (required in Stripe dashboard)

1. Create **Payment Links** for each top-up amount ($5, $10, $25) — store URLs in `TOPUP_*` env vars
2. Create a **Payment Link** for the $9.99/month subscription — store in `SUB_LINK`
3. Create a **Webhook** pointing to `https://web-production-1f0a4.up.railway.app/webhook/stripe`
   - Events: `checkout.session.completed`, `customer.subscription.deleted`
   - Store signing secret in `STRIPE_WEBHOOK_SECRET`
4. Append `?client_reference_id=wa_PHONENUMBER` to payment links when sending to WhatsApp users — webhook uses this to identify who paid

### Message Flow

1. WhatsApp sends webhook POST to `/webhook`
2. Bot deduplicates message by ID (in-memory set, max 10,000)
3. Drops messages older than 5 minutes (stale guard)
4. Content extracted based on type (text/image/audio/video/document/URL)
5. User sees a confirm preview: "Reply Y to fact-check / N to cancel"
6. On Y: **billing check**
   - Free checks remaining → allow (free tier, ads shown)
   - Balance covers cost → allow (deduct after check)
   - Subscriber → allow (unlimited, no ads)
   - Insufficient balance → send payment prompt with Stripe links
7. `run_check()` launches in background thread
8. `run_check()` pipeline:
   a. **Neutralize** — `neutralize_claim()` strips emotional framing (Haiku)
   b. **Extract** — `extract_claims()` splits into ≤4 individual claims (Haiku)
   c. **Scrape** — `scrape_sites()` + `google_fc()` run once, shared across all claims
   d. For each claim: **Debate** — `claude_analyse()` runs pro/con in parallel (Haiku×2) then synthesizes (Sonnet)
   e. Report sent per claim with `*— CLAIM N/M —*` header if multi-claim
   f. **Cost tracked** — actual Anthropic/OpenAI tokens counted, converted to cents with profit margin
   g. **Deduct** balance (or increment free check counter)
9. Report formatted with verdict, truth gauge, truth score (1–10), key facts, sources, confidence
10. Ad appended for free-tier users if `SPONSOR_ADS` is set

### Billing Model

API costs (at-cost, used for internal calculation):

| Model | Input | Output |
|---|---|---|
| claude-sonnet-4-6 | $3.00/M tokens | $15.00/M tokens |
| claude-haiku-4-5-20251001 | $0.25/M tokens | $1.25/M tokens |
| gpt-4o | $2.50/M tokens | $10.00/M tokens |
| gpt-4o-mini | $0.15/M tokens | $0.60/M tokens |
| whisper-1 | $0.006/minute | — |

Charged to user = at-cost × `PROFIT_MARGIN` (default 2.0).

Approximate user-facing costs per fact-check (with 2x margin):
- Text/URL: ~5–8¢
- Image: ~6–9¢
- Audio: ~7–10¢
- Video: ~10–15¢

$5 top-up ≈ 60–100 fact-checks.

### Database Schema (SQLite)

```sql
users         -- web accounts (id, email, password_hash, tier, balance_cents, created_at)
tokens        -- web auth tokens (token, user_id, expires_at)
history       -- web fact-check history (user_id, query, results_json, created_at)
wa_users      -- WhatsApp users (wa_id, free_checks_used, balance_cents, tier, stripe_customer_id, created_at, last_seen)
transactions  -- all financial events (user_type, user_id, txn_type, amount_cents, description, stripe_session_id, created_at)
```

### Web API Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /web` | None | Serve web UI (static/index.html) |
| `POST /api/register` | None | Create web account |
| `POST /api/login` | None | Login, get token |
| `GET /api/me` | Bearer token | Current user info |
| `POST /api/factcheck` | Optional | Fact-check (5/day anon, unlimited registered) |
| `GET /api/history` | Bearer token | Fact-check history |
| `GET /api/billing` | Bearer token | Balance, usage, transaction history |
| `POST /webhook/stripe` | Stripe signature | Handle Stripe payment events |

### Token Auto-Refresh

APScheduler runs `refresh_whatsapp_token()` every 50 days using the Facebook Graph API token exchange endpoint. Requires `FB_APP_ID` and `FB_APP_SECRET`.

### API Credit Alerting

When Anthropic or OpenAI API returns credit/quota errors (HTTP 402/529), `send_admin_alert()` sends a WhatsApp message to `ADMIN_NUMBER`. Throttled to once per hour per provider.

### Source Toggle Variables

All default to `"true"`. Set to `"false"` in Railway to disable any source.

**Fact-check organisations:** `SRC_SNOPES`, `SRC_FULLFACT`, `SRC_FACTCHECKORG`, `SRC_POLITIFACT`, `SRC_AFP`

**News outlets:** `SRC_ALJAZEERA`, `SRC_BBC`, `SRC_REUTERS`, `SRC_AP`, `SRC_GUARDIAN`, `SRC_CNN`

**Independent/alternative media:** `SRC_MEE`, `SRC_NOVARA`, `SRC_CANARY`, `SRC_ZETEO`, `SRC_YENISAFAK`, `SRC_972MAG`, `SRC_MONDOWEISS`, `SRC_EINTIFADA`, `SRC_INTERCEPT`, `SRC_HAARETZ`, `SRC_DDN`, `SRC_DEMOCRACYNOW`, `SRC_GRAYZONE`, `SRC_MINTPRESS`

**Journalist/commentator channels:** `SRC_OWENJONES`, `SRC_OWENJONES_SUB`, `SRC_CORBYN`, `SRC_CORBYN_SITE`, `SRC_ZARASULTANA`, `SRC_SULTANA_SITE`, `SRC_FINKELSTEIN`, `SRC_FINKELSTEIN_SUB`, `SRC_CODEPINK`, `SRC_CODEPINK_SITE`, `SRC_MOATS`, `SRC_MOATS_YT`, `SRC_GALLOWAY_SITE`, `SRC_PSC`, `SRC_SUBSTACK`, `SRC_DDN_YT`

---

## Test Suite

`test_all.py` — 49 tests covering all media types and pipeline functions. Run with:
```bash
source venv/bin/activate
python test_all.py
```

---

## Known Issues / Current State

### SQLite persistence
Default `DB_PATH=/tmp/factcheck.db` resets on every Railway redeploy. For production:
1. Add a Railway Volume
2. Set `DB_PATH=/data/factcheck.db`

### Web UI WhatsApp link
`static/index.html` contains placeholder `wa.me/message/factcheckpro` — replace with real WhatsApp link.

### Nitter sources (503)
`nitter.poast.org` consistently returns 503. These sources waste scrape time. Consider disabling via SRC_* vars or removing from source list.

---

## Outstanding Tasks

### In Progress
- **Monetisation system** — billing gate, Stripe integration, per-user usage tracking, ads

### High Priority
1. Add Railway Volume for SQLite persistence
2. Add all `SRC_*` variables to Railway
3. Set `ADMIN_NUMBER` in Railway
4. Configure Stripe (create Payment Links, set webhook, add env vars)
5. Replace WhatsApp placeholder link in web UI

### Medium Priority
6. Multi-platform expansion (FB Messenger, Instagram DMs, TikTok comment replies)
7. Admin dashboard — `/admin` page: messages processed, API cost, top claims, error rate

### Low Priority
8. Multi-language support
9. Replace nitter sources (503) with working alternatives
10. Subscription management web UI (cancel, upgrade, billing history page)

---

## Repository Structure

```
whatsapp-factcheck/
├── bot.py              # Main application (all logic, ~1600 lines)
├── test_all.py         # 49-test suite
├── static/
│   └── index.html      # Web UI (dark-mode SPA)
├── requirements.txt    # Python dependencies
├── nixpacks.toml       # Railway build config
├── Procfile            # Start command
├── runtime.txt         # Python version
├── cookies.txt         # Facebook cookies (local testing only)
├── www.instagram.com_cookies.txt  # Instagram cookies (local testing only)
├── update.sh           # Helper: push updates
├── bot.py.bak          # Backup
└── v3/                 # Earlier v3 iteration
```

---

## How to Deploy a Change

```bash
cd /home/anon/whatsapp-factcheck
git add bot.py static/index.html  # or whichever files changed
git commit -m "describe change"
git push origin main
# Railway auto-deploys from main branch (~2 minutes)
```

---

## How to Check Logs

Railway dashboard → enchanting-wholeness → Deployments → latest → Logs tab.

Or: `railway logs`

---

## API Keys / Secrets Location

All secrets in Railway environment variables. Do NOT commit to repo.

Railway API token (project-scoped): `a150de81-9f32-42e3-acba-b0369b041ae3`

---

## Local Development

```bash
cd /home/anon/whatsapp-factcheck
source venv/bin/activate
python bot.py
# run tests:
python test_all.py
```

---

## System Prompt / Bot Persona

**FactCheck Pro** — world-class fact-checker for journalists and activists, deep expertise in Gaza conflict, Iran-US-Israel tensions, West Bank, Hamas, Hezbollah, and regional players. Rigorously balanced, calls out falsehoods from all sides equally, flags propaganda techniques and media bias.
