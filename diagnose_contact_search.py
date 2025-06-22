#!/usr/bin/env python3
"""
Diagnostic script to troubleshoot contact search issues
"""

import os
import sys
from datetime import datetime

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common.supabase import supabase
from common.contact_intelligence import search_contacts_smart, get_contact_by_identifier
from common.unified_memory import search_contacts

def diagnose_contact_search():
    print("=" * 80)
    print("CONTACT SEARCH DIAGNOSTIC TOOL")
    print("=" * 80)
    
    # 1. Check if contacts table exists and has data
    print("\n1. Checking contacts table...")
    try:
        resp = supabase.table("contacts").select("count", count="exact").execute()
        count = resp.count
        print(f"✓ Contacts table exists with {count} records")
        
        # Get sample contacts
        sample_resp = supabase.table("contacts").select("id,email,name,first_name,last_name").limit(5).execute()
        if sample_resp.data:
            print("\nSample contacts:")
            for contact in sample_resp.data:
                print(f"  - {contact.get('name', 'No name')} ({contact.get('email', 'No email')})")
    except Exception as e:
        print(f"✗ Error accessing contacts table: {e}")
        return
    
    # 2. Check if search_contacts_enhanced function exists
    print("\n2. Checking database functions...")
    try:
        # Try to call the function with a test query
        test_resp = supabase.rpc("search_contacts_enhanced", {
            "search_query": "test",
            "limit_count": 1
        }).execute()
        print("✓ search_contacts_enhanced function exists")
    except Exception as e:
        print(f"✗ search_contacts_enhanced function not found: {e}")
        print("  This is likely why contact search is failing!")
    
    # 3. Search for Max specifically
    print("\n3. Searching for 'Max'...")
    
    # Direct database search
    print("\n  a) Direct database search:")
    try:
        direct_resp = supabase.table("contacts").select("*").or_(
            f"name.ilike.%Max%,first_name.ilike.%Max%,last_name.ilike.%Max%,email.ilike.%Max%"
        ).execute()
        
        if direct_resp.data:
            print(f"  ✓ Found {len(direct_resp.data)} contacts matching 'Max':")
            for contact in direct_resp.data:
                print(f"    - ID: {contact['id']}")
                print(f"      Name: {contact.get('name', 'N/A')}")
                print(f"      Email: {contact.get('email', 'N/A')}")
                print(f"      First Name: {contact.get('first_name', 'N/A')}")
                print(f"      Last Name: {contact.get('last_name', 'N/A')}")
        else:
            print("  ✗ No contacts found matching 'Max' in database")
    except Exception as e:
        print(f"  ✗ Error in direct search: {e}")
    
    # Smart search
    print("\n  b) Smart search (search_contacts_smart):")
    try:
        smart_results = search_contacts_smart("Max", limit=5)
        if smart_results:
            print(f"  ✓ Found {len(smart_results)} contacts:")
            for contact in smart_results:
                print(f"    - {contact.get('name', 'N/A')} ({contact.get('email', 'N/A')})")
        else:
            print("  ✗ No contacts found via smart search")
    except Exception as e:
        print(f"  ✗ Error in smart search: {e}")
    
    # Unified memory search
    print("\n  c) Unified memory search (fallback):")
    try:
        unified_results = search_contacts("Max", limit=5)
        if unified_results:
            print(f"  ✓ Found {len(unified_results)} contacts:")
            for contact in unified_results:
                print(f"    - {contact.get('name', 'N/A')} ({contact.get('email', 'N/A')})")
        else:
            print("  ✗ No contacts found via unified memory search")
    except Exception as e:
        print(f"  ✗ Error in unified memory search: {e}")
    
    # 4. Check table schema
    print("\n4. Checking contacts table schema...")
    try:
        # Get one record to see the schema
        schema_resp = supabase.table("contacts").select("*").limit(1).execute()
        if schema_resp.data:
            print("  Available columns:")
            for key in schema_resp.data[0].keys():
                print(f"    - {key}")
    except Exception as e:
        print(f"  ✗ Error checking schema: {e}")
    
    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    diagnose_contact_search()
