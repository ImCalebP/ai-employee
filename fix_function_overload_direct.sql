-- Direct fix for the search_documents_semantic overloading error
-- Run this SQL in your Supabase SQL editor

-- Drop the base version that's causing the conflict
DROP FUNCTION IF EXISTS search_documents_semantic(vector(1536), INT, FLOAT);

-- That's it! The extended version with 4 parameters will remain and handle all cases.
-- When doc_type_filter is not provided, it defaults to NULL and behaves like the base version.
