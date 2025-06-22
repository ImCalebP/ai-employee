#!/bin/bash

# ═══════════════════════════════════════════════════════════════════════════════
# Contact Search Fix Deployment Script
# This script fixes the contact search issues and deploys the updates
# ═══════════════════════════════════════════════════════════════════════════════

echo "═══════════════════════════════════════════════════════════════════════════════"
echo "CONTACT SEARCH FIX DEPLOYMENT"
echo "═══════════════════════════════════════════════════════════════════════════════"

# 1. First, backup the current contact_intelligence.py
echo ""
echo "1. Backing up current contact_intelligence.py..."
cp common/contact_intelligence.py common/contact_intelligence_backup_$(date +%Y%m%d_%H%M%S).py
echo "✓ Backup created"

# 2. Replace with the fixed version
echo ""
echo "2. Applying fixed contact intelligence module..."
cp common/contact_intelligence_fixed.py common/contact_intelligence.py
echo "✓ Fixed module applied"

# 3. Run the diagnostic tool locally
echo ""
echo "3. Running diagnostic tool..."
python diagnose_contact_search.py

# 4. Ask user if they want to proceed with deployment
echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
echo "Review the diagnostic results above."
echo "Do you want to proceed with deployment to Render? (y/n)"
read -r response

if [[ "$response" != "y" ]]; then
    echo "Deployment cancelled."
    exit 0
fi

# 5. Commit and push changes
echo ""
echo "4. Committing changes..."
git add common/contact_intelligence.py
git add fix_all_function_overloads.sql
git add diagnose_contact_search.py
git add fix_contact_search_deploy.sh
git commit -m "Fix contact search with robust fallback strategies"

echo ""
echo "5. Pushing to GitHub..."
git push origin main

echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
echo "DEPLOYMENT COMPLETE"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "1. The code has been pushed to GitHub"
echo "2. Render will automatically deploy the changes"
echo "3. Run the SQL fix in your Supabase SQL editor:"
echo "   - Go to your Supabase dashboard"
echo "   - Navigate to SQL Editor"
echo "   - Copy and paste the contents of fix_all_function_overloads.sql"
echo "   - Execute the SQL"
echo ""
echo "4. After deployment, test the contact search:"
echo "   - Try searching for 'Max' in Teams"
echo "   - The AI should now find Max in the contacts"
echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
