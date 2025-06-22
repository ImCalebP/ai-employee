-- Fix for search_documents_semantic function overloading issue
-- This removes the base version and keeps only the extended version with optional doc_type_filter

-- Drop the base version of the function (3 parameters only)
DROP FUNCTION IF EXISTS search_documents_semantic(vector(1536), INT, FLOAT);

-- The extended version with doc_type_filter already exists and handles both cases:
-- - When doc_type_filter is NULL (behaves like the base version)  
-- - When doc_type_filter is provided (filters by document type)

-- Verify remaining functions
SELECT 
  p.proname as function_name,
  pg_get_function_arguments(p.oid) as arguments
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public' 
  AND p.proname = 'search_documents_semantic';
