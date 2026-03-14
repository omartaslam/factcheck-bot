#!/usr/bin/env python3
"""Test WhatsApp token validity"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("PHONE_NUMBER_ID")

print("🔍 Testing WhatsApp Token Validity...\n")
print(f"Token: {TOKEN[:50]}..." if TOKEN else "❌ No token found")
print(f"Phone ID: {PHONE_ID}\n")

# Test token by getting phone number info
url = f"https://graph.facebook.com/v19.0/{PHONE_ID}"
headers = {"Authorization": f"Bearer {TOKEN}"}

try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status Code: {response.status_code}")

    if response.status_code == 200:
        print("✅ TOKEN IS VALID!")
        data = response.json()
        print(f"   Phone: {data.get('display_phone_number', 'N/A')}")
        print(f"   Name: {data.get('verified_name', 'N/A')}")
    elif response.status_code == 401:
        print("❌ TOKEN EXPIRED OR INVALID")
        print("   You need to generate a new token from Meta Developer Console")
    elif response.status_code == 404:
        print("❌ PHONE NUMBER ID NOT FOUND")
        print("   Check your Phone Number ID in Meta Developer Console")
    else:
        print(f"⚠️ UNEXPECTED ERROR: {response.text}")

except Exception as e:
    print(f"❌ REQUEST FAILED: {e}")

print("\n" + "="*60)
print("📋 INSTRUCTIONS TO FIX:")
print("="*60)
print("1. Go to: https://developers.facebook.com/apps")
print("2. Select your WhatsApp app")
print("3. Go to: WhatsApp > API Setup")
print("4. Generate new 'Temporary access token' (or use System User token)")
print("5. Update token in Railway dashboard:")
print("   - Go to: https://railway.app")
print("   - Select your project")
print("   - Go to Variables tab")
print("   - Update WHATSAPP_TOKEN")
print("6. Verify webhook is configured:")
print("   - Callback URL: https://your-railway-url.up.railway.app/webhook")
print("   - Verify Token: factcheck_verify_123")
