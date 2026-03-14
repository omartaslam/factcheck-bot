#!/bin/bash

echo "🔑 WhatsApp Token Updater"
echo "=========================="
echo ""
read -p "Enter your new WhatsApp token: " NEW_TOKEN
echo ""

if [ -z "$NEW_TOKEN" ]; then
    echo "❌ No token provided. Exiting."
    exit 1
fi

# Update .env file
echo "📝 Updating .env file..."
sed -i "s/^WHATSAPP_TOKEN=.*/WHATSAPP_TOKEN=$NEW_TOKEN/" .env

# Test the token
echo "🧪 Testing token validity..."
python3 test_token.py

echo ""
echo "✅ Local .env updated!"
echo ""
echo "📋 NEXT STEPS:"
echo "=============="
echo "1. Go to Railway dashboard: https://railway.app"
echo "2. Select your 'factcheck-bot' project"
echo "3. Click 'Variables' tab"
echo "4. Update WHATSAPP_TOKEN with:"
echo "   $NEW_TOKEN"
echo ""
echo "5. Verify webhook in Meta Developer Console:"
echo "   - Callback URL: https://[your-railway-url].up.railway.app/webhook"
echo "   - Verify Token: factcheck_verify_123"
echo "   - Subscribe to: messages"
echo ""
echo "6. Railway will auto-redeploy. Wait 2 minutes, then test!"
