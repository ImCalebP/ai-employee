# Enhanced Contact Intelligence System - Setup & Usage Guide

## Overview

The enhanced contact intelligence system provides natural language understanding for contact queries, proactive contact information retrieval, and automatic contact management. Your AI employee will now be able to:

- Automatically recognize when someone asks about contact information (e.g., "What's Max's email?")
- Proactively provide contact details when people are mentioned
- Remember and track all contacts with rich metadata
- Handle various query formats naturally

## Setup Instructions

### 1. Database Setup

Run the SQL script in your Supabase SQL editor:

```bash
# Run the contents of database_contacts_enhanced.sql in Supabase SQL editor
```

This will:
- Drop and recreate the contacts table with enhanced fields
- Create smart search functions
- Add sample contacts for testing
- Set up automatic name parsing and triggers

### 2. Deploy Code Changes

The following files have been updated:
- `common/contact_intelligence.py` - New contact intelligence module
- `common/enhanced_memory.py` - Fixed imports and removed old contact code
- `services/intent_api/reply_agent.py` - Integrated new contact system
- `services/intent_api/intent_v2.py` - Added contact action handlers

Deploy to Render:
```bash
git add .
git commit -m "Enhanced contact intelligence system"
git push origin main
```

### 3. Test the System

After deployment, test these scenarios in Teams:

#### Basic Contact Queries:
- "What's Max's email?"
- "Max email"
- "Roger's email?"
- "How can I contact Sarah?"
- "What's John's phone number?"

#### Contact Information Requests:
- "Who is Alice Brown?"
- "What's Roger's role?"
- "Tell me about Max Wilson"

#### Adding New Contacts:
- "Add contact: Jane Smith jane@example.com"
- "New contact: Bob Johnson bob@company.com, he's the CTO"

## How It Works

### 1. Natural Language Understanding
The system uses advanced regex patterns to understand various ways people ask about contacts:
- Direct queries: "Max's email?"
- Questions: "What's Sarah's phone?"
- Commands: "Contact info for Roger"
- Context: "How do I reach John?"

### 2. Smart Search
The enhanced search function scores matches based on:
- Exact email matches (100 points)
- Exact name matches (95 points)
- First/last name matches (90 points)
- Partial matches (70-85 points)
- Alias matches (70 points)
- Tag matches (50 points)

### 3. Proactive Response
When a contact query is detected with high confidence, the system:
1. Immediately searches for the contact
2. Formats a natural response
3. Sends it without waiting for GPT
4. This makes responses faster and more accurate

### 4. Contact Enrichment
The system automatically:
- Parses full names into first/last names
- Tracks interaction counts
- Maintains conversation history
- Supports aliases and tags
- Stores professional information

## Sample Contacts in Database

After running the SQL script, you'll have these test contacts:
1. **Max Wilson** - max.wilson@techcorp.com (Engineering Manager)
2. **Sarah Jones** - sarah.jones@techcorp.com (Product Manager)
3. **Roger Smith** - roger@gmail.com (Consultant)
4. **John Doe** - john.doe@example.com (Developer)
5. **Alice Brown** - alice.brown@startup.io (CEO)

## Troubleshooting

### If contacts aren't being found:
1. Check if the SQL script ran successfully
2. Verify contacts exist in the database
3. Check Render logs for any errors
4. Ensure the search_contacts_enhanced function exists

### If you get database errors:
1. Make sure you have the vector extension enabled
2. Check that all columns were created properly
3. Verify the trigger functions exist

### Common Issues:
- **"No contacts found"** - The search query might be too specific
- **Slow responses** - Check if the vector index was created
- **Missing information** - Some fields might be null in the database

## Advanced Features

### Tags and Aliases
Contacts support tags and aliases for better searchability:
```sql
-- Example: Max can be found by "Maxwell" or "Max W"
aliases: ['Maxwell', 'Max W']
tags: ['engineering', 'manager']
```

### Relationship Tracking
The system tracks:
- Last interaction date
- Total interaction count
- Relationship type (professional, personal, client, vendor)
- Importance level (1-5 scale)

### Multi-field Search
The search function checks:
- Email addresses
- Full names
- First/last names separately
- Company names
- Aliases
- Tags

## Future Enhancements

Consider adding:
1. Contact interaction history table
2. Relationship mapping between contacts
3. Automatic contact extraction from emails
4. Calendar integration for meeting participants
5. Contact groups and categories

## Testing Checklist

- [ ] SQL script executed successfully
- [ ] Sample contacts appear in database
- [ ] "Max email?" returns Max Wilson's email
- [ ] "What's Roger's phone?" works correctly
- [ ] Adding new contacts via chat works
- [ ] Contact information appears in responses
- [ ] No errors in Render logs

## Support

If you encounter issues:
1. Check the Render logs for detailed error messages
2. Verify the database schema matches the SQL script
3. Test the search_contacts_enhanced function directly in Supabase
4. Ensure all Python imports are correct

The system is designed to feel natural and proactive, automatically providing contact information when relevant without requiring explicit commands.
