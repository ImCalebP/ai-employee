#!/bin/bash

echo "=== Fixing Resource Exhaustion Issues ==="
echo ""
echo "This script addresses two issues:"
echo "1. Database function overloading error"
echo "2. Resource exhaustion (Errno 11) from unlimited thread creation"
echo ""

# Set the environment variable for max workers
echo "Setting INTENT_API_MAX_WORKERS environment variable..."
export INTENT_API_MAX_WORKERS=10

echo ""
echo "To make this permanent, add the following to your deployment configuration:"
echo "INTENT_API_MAX_WORKERS=10"
echo ""
echo "For Render, add this in your environment variables section."
echo ""
echo "The code changes have been made to:"
echo "1. services/intent_api/executor_pool.py - New shared thread pool"
echo "2. services/intent_api/parallel_executor.py - Updated to use shared pool"
echo ""
echo "These changes limit concurrent threads to prevent resource exhaustion."
echo ""
echo "Remember to also run the SQL fix for the database function overloading:"
echo "DROP FUNCTION IF EXISTS search_documents_semantic(vector(1536), INT, FLOAT);"
echo ""
echo "Done!"
