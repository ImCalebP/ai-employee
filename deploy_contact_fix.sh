#!/bin/bash

# Deploy contact fix to Render

echo "🚀 Deploying contact fix to Render..."

# Add all changes
git add -A

# Commit with descriptive message
git commit -m "Fix contact intent errors: remove invalid 'search_contact_info' intent and fix asyncio.run() in async context"

# Push to main branch (Render auto-deploys from main)
git push origin main

echo "✅ Deployment initiated. Check Render dashboard for deployment status."
echo "📊 Monitor logs at: https://dashboard.render.com/web/srv-cu7iqdog1k6c73"
