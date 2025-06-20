#!/bin/bash

echo "🚀 Deploying fixes to Render..."

# Stage and commit the fixes
git add services/intent_api/intent_v2.py
git commit -m "Fix: Handle search_contact_info intent enum value and improve intent detection"

# Push to trigger Render deployment
git push

echo "✅ Code pushed to Render"
echo ""
echo "⚠️  IMPORTANT: You also need to run this SQL in your Supabase dashboard:"
echo ""
cat fix_database_function.sql
echo ""
echo "📋 Steps:"
echo "1. Go to your Supabase dashboard"
echo "2. Navigate to SQL Editor"
echo "3. Copy and paste the SQL above"
echo "4. Click 'Run'"
echo ""
echo "Your deployment should be working after both steps are complete!"
