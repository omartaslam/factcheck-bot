# 🔴 TOKEN EXPIRED - Quick Fix Guide

Your WhatsApp token expired on March 12, 2026. Both tokens in `.env` and `meta access token` are invalid.

## Problem
- **Temporary tokens expire after 24 hours**
- Text messages, images, videos all fail
- Bot can't communicate with WhatsApp API

## Solution: Get a New Token

### Option A: Temporary Token (Quick, expires in 24 hours)

1. Visit: https://developers.facebook.com/apps
2. Select your WhatsApp Business app
3. Left sidebar: **WhatsApp → API Setup**
4. Click **"Generate Token"** button
5. Copy the token (starts with `EAAMZB...`)

### Option B: System User Token (Recommended, lasts 60 days)

1. Visit: https://business.facebook.com/settings/system-users
2. Click **Add** → Create System User
3. Assign **WhatsApp Business Management** permissions
4. Click **Generate New Token**
5. Select assets: Your WhatsApp Business account
6. Set expiration: **60 days**
7. Copy the token

## Update Everything

### Step 1: Update Local Environment
```bash
cd /home/anon/whatsapp-factcheck
./update_token.sh
# Paste your new token when prompted
```

### Step 2: Update Railway (CRITICAL!)
1. Go to https://railway.app
2. Select your **factcheck-bot** project
3. Click **Variables** tab
4. Find `WHATSAPP_TOKEN`
5. Click Edit → Paste new token → Save
6. Railway will auto-redeploy (wait ~2 minutes)

### Step 3: Verify Webhook Connection
1. Back in Meta Developer Console
2. Go to **WhatsApp → Configuration**
3. Find **Webhook** section
4. Edit webhook settings:
   - **Callback URL**: `https://[your-railway-url].up.railway.app/webhook`
   - **Verify Token**: `factcheck_verify_123`
5. Subscribe to fields: **messages**
6. Click **Verify and Save**

## Test Your Bot

Send a text message to your WhatsApp Business number:
```
"Test message"
```

You should get a cost confirmation within 3 seconds.

## Troubleshooting

**Bot still not responding?**
```bash
# Test token locally
python test_token.py

# Check if Railway deployment succeeded
# Visit your Railway dashboard → Deployments tab
# Look for green checkmark on latest deployment
```

**Railway deployment failed?**
- Check Logs tab in Railway dashboard
- Look for errors related to opencv-python-headless or ffmpeg
- If needed, trigger manual redeploy

**Webhook verification failed?**
- Ensure Railway URL is correct
- Verify token must be: `factcheck_verify_123`
- Check that bot is running (Railway shows "Active")

## Prevent Future Expiration

**Use System User Tokens** (60-day expiration) instead of temporary tokens (24-hour expiration).

Set a calendar reminder for 55 days from now to regenerate the token.

## Need Help?

If stuck, share:
1. Output of `python test_token.py`
2. Railway deployment logs
3. Meta webhook verification error (if any)
