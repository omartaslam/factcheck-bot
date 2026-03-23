# Fred • Fact Check — Admin & Maintenance Reference

> Quick reference for common admin tasks, maintenance operations, and emergency procedures.
> No Claude required — everything here can be done manually.

---

## 1. Key Credentials

| Item | Value |
|---|---|
| Production URL | `https://web-production-1f0a4.up.railway.app` |
| Admin token | `qc-test-fred-2026` |
| Dev phone number | `34643994740` |
| Railway personal token | `bc2d9c22-2d89-458c-8c33-3635a57193c7` |
| GitHub repo | `https://github.com/omartaslam/factcheck-bot` |

---

## 2. User Management — curl Commands

All commands work from any terminal. Replace `34643994740` with the target user's WhatsApp number.

### Reset today's daily free checks (give user 3 checks again)
```bash
curl -X POST https://web-production-1f0a4.up.railway.app/admin/set-balance \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: qc-test-fred-2026" \
  -d '{"platform":"whatsapp","uid":"34643994740","free_checks_used":0}'
```

### Reset 7-day trial (restart trial clock from today)
```bash
curl -X POST https://web-production-1f0a4.up.railway.app/admin/set-balance \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: qc-test-fred-2026" \
  -d '{"platform":"whatsapp","uid":"34643994740","reset_trial":true}'
```

### Reset daily checks AND restart trial in one go
```bash
curl -X POST https://web-production-1f0a4.up.railway.app/admin/set-balance \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: qc-test-fred-2026" \
  -d '{"platform":"whatsapp","uid":"34643994740","free_checks_used":0,"reset_trial":true}'
```

### Set a user's paid balance (e.g. add $5.00 = 500 cents)
```bash
curl -X POST https://web-production-1f0a4.up.railway.app/admin/set-balance \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: qc-test-fred-2026" \
  -d '{"platform":"whatsapp","uid":"34643994740","cents":500}'
```

### Check usage stats
```bash
curl -H "X-Admin-Token: qc-test-fred-2026" \
  https://web-production-1f0a4.up.railway.app/admin/stats
```

### Trigger daily summary email manually
```bash
curl -X POST https://web-production-1f0a4.up.railway.app/admin/daily-summary \
  -H "X-Admin-Token: qc-test-fred-2026"
```

---

## 3. User Management — Railway Database (no terminal needed)

1. Go to [railway.app](https://railway.app) → your project → **PostgreSQL** service → **Data** tab
2. Find table: `platform_users`
3. Find the row where `platform = 'whatsapp'` and `platform_id = '34643994740'` (or the user's number)

| Field | What it does | How to reset |
|---|---|---|
| `free_checks_used` | Today's check count | Set to `0` |
| `free_checks_date` | Date of last check | Set to `NULL` |
| `created_at` | Trial start timestamp | Set to current Unix time (get from [epochconverter.com](https://epochconverter.com)) |
| `balance_cents` | Paid credit balance in cents | Set to any integer (e.g. `500` = $5.00) |

---

## 4. Deploying Code Changes to Railway

Railway auto-deploys on every `git push` to `main`. Steps:

```bash
# 1. Make your changes to bot.py (or any file)

# 2. Stage and commit
git add bot.py
git commit -m "description of what you changed"

# 3. Push — Railway deploys automatically (takes ~2 min)
git push
```

### Check if Railway has deployed your latest push
```bash
curl -s -X POST https://backboard.railway.app/graphql/v2 \
  -H "Authorization: Bearer bc2d9c22-2d89-458c-8c33-3635a57193c7" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ deployments(first:1, input:{serviceId:\"3ae3bd52-301e-4003-b2cd-291436c7af2d\",environmentId:\"ebb5147d-8292-4b55-bd76-6a2c1b3e6564\"}) { edges { node { id status createdAt } } } }"}' \
  | python3 -m json.tool | grep -E "status|createdAt"
```
- `SUCCESS` = deployed and live
- `BUILDING` = still deploying

### Rollback to previous version
```bash
# Revert the last commit and push
git revert HEAD
git push
# Railway will redeploy the reverted code automatically
```

### Rollback a specific commit
```bash
git revert <commit-hash>   # e.g. git revert 8d4d767
git push
```

---

## 5. FB/IG Cookie Rotation (do every ~10 days)

Cookies expire ~10 days after last refresh. Must rotate before expiry or FB/IG scraping breaks.

1. Install **"Get cookies.txt LOCALLY"** browser extension (Chrome or Firefox)
2. Log into **Facebook** → click extension on `facebook.com` → **Export** → saves `facebook.com_cookies.txt`
3. Encode it:
   ```bash
   base64 -w 0 facebook.com_cookies.txt
   ```
4. Copy the output
5. Railway dashboard → service → **Variables** → update `FB_COOKIES_B64` → **Save**
6. Repeat for **Instagram**: log into instagram.com → export → encode → update `IG_COOKIES_B64`
7. Railway auto-redeploys. If not, trigger manually from the Deployments tab.

**Next rotation due:** ~2026-04-01

---

## 6. Environment Variables — Railway

Railway dashboard → your project → service → **Variables** tab.

| Variable | Purpose | Current value |
|---|---|---|
| `FREE_DAILY_LIMIT` | Free checks per day | `3` |
| `FREE_TRIAL_DAYS` | Trial length in days | `7` |
| `COST_PER_CHECK_CENTS` | Price per check in cents | `25` |
| `DEV_AUTOSELECT_NUM` | Dev phone — skips claim confirmation | `34643994740` |
| `DEV_AUTOSELECT_ON` | Enable dev bypass | `true` |
| `WEBSITE_URL` | Used in payment links | `https://fredcheck.com` |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook security | `whsec_...` (set in Stripe dashboard) |

To change a variable: update in Railway → service auto-redeploys.

---

## 7. Emergency Contacts / External Dashboards

| Service | Dashboard |
|---|---|
| Railway | [railway.app](https://railway.app) |
| Stripe payments | [dashboard.stripe.com](https://dashboard.stripe.com) |
| Anthropic (Claude API) | [console.anthropic.com](https://console.anthropic.com) |
| Tavily search | [app.tavily.com](https://app.tavily.com) |
| SendGrid email | [app.sendgrid.com](https://app.sendgrid.com) |
| Meta WhatsApp | [developers.facebook.com](https://developers.facebook.com) |
