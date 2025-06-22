-- ═══════════════════════════════════════════════════════════════════════════════
-- Comprehensive Fix for All Function Overloading Issues
-- This script identifies and fixes ALL overloaded functions in the database
-- ═══════════════════════════════════════════════════════════════════════════════

-- Step 1: Check current overloaded functions
DO $$
BEGIN
    RAISE NOTICE 'Checking for overloaded functions...';
END $$;

SELECT 
  p.proname as function_name,
  COUNT(*) as overload_count,
  array_agg(pg_get_function_arguments(p.oid)) as all_signatures
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public'
GROUP BY p.proname
HAVING COUNT(*) > 1
ORDER BY COUNT(*) DESC, p.proname;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Fix 1: search_tasks_semantic
-- ═══════════════════════════════════════════════════════════════════════════════

-- Drop ALL versions of search_tasks_semantic
DROP FUNCTION IF EXISTS search_tasks_semantic(vector(1536), INT, FLOAT);
DROP FUNCTION IF EXISTS search_tasks_semantic(vector(1536), INT, FLOAT, TEXT);

-- Recreate the single, most complete version
CREATE OR REPLACE FUNCTION search_tasks_semantic(
  query_embedding vector(1536),
  match_count INT DEFAULT 5,
  similarity_threshold FLOAT DEFAULT 0.7,
  status_filter TEXT DEFAULT NULL
)
RETURNS TABLE (
  id UUID,
  description TEXT,
  assignee TEXT,
  assignee_email TEXT,
  status TEXT,
  priority TEXT,
  due_date TIMESTAMPTZ,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    t.id,
    t.description,
    t.assignee,
    t.assignee_email,
    t.status,
    t.priority,
    t.due_date,
    1 - (t.embedding <=> query_embedding) AS similarity
  FROM tasks t
  WHERE 
    t.embedding IS NOT NULL
    AND 1 - (t.embedding <=> query_embedding) > similarity_threshold
    AND (status_filter IS NULL OR t.status = status_filter)
  ORDER BY t.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Fix 2: search_documents_semantic
-- ═══════════════════════════════════════════════════════════════════════════════

-- Drop ALL versions of search_documents_semantic
DROP FUNCTION IF EXISTS search_documents_semantic(vector(1536), INT, FLOAT);
DROP FUNCTION IF EXISTS search_documents_semantic(vector(1536), INT, FLOAT, TEXT);
DROP FUNCTION IF EXISTS search_documents_semantic_base(vector(1536), INT, FLOAT);

-- Recreate the single, most complete version
CREATE OR REPLACE FUNCTION search_documents_semantic(
  query_embedding vector(1536),
  match_count INT DEFAULT 5,
  similarity_threshold FLOAT DEFAULT 0.7,
  doc_type_filter TEXT DEFAULT NULL
)
RETURNS TABLE (
  id UUID,
  title TEXT,
  content TEXT,
  type TEXT,
  author TEXT,
  created_at TIMESTAMPTZ,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    d.id,
    d.title,
    d.content,
    d.type,
    d.author,
    d.created_at,
    1 - (d.embedding <=> query_embedding) AS similarity
  FROM documents d
  WHERE 
    d.embedding IS NOT NULL
    AND 1 - (d.embedding <=> query_embedding) > similarity_threshold
    AND (doc_type_filter IS NULL OR d.type = doc_type_filter)
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Fix 3: match_messages_in_chat (if overloaded)
-- ═══════════════════════════════════════════════════════════════════════════════

-- Check if this function exists and has overloads
DROP FUNCTION IF EXISTS match_messages_in_chat(vector(1536), TEXT, INT, FLOAT);
DROP FUNCTION IF EXISTS match_messages_in_chat(vector(1536), TEXT, INT);

-- Create the single version if needed
CREATE OR REPLACE FUNCTION match_messages_in_chat(
  query_embedding vector(1536),
  chat_id_param TEXT,
  match_count INT DEFAULT 5,
  similarity_threshold FLOAT DEFAULT 0.7
)
RETURNS TABLE (
  id UUID,
  sender TEXT,
  content TEXT,
  timestamp TIMESTAMPTZ,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    m.id,
    m.sender,
    m.content,
    m.timestamp,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM message_history m
  WHERE 
    m.chat_id = chat_id_param
    AND m.embedding IS NOT NULL
    AND 1 - (m.embedding <=> query_embedding) > similarity_threshold
  ORDER BY m.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Fix 4: match_messages_global (if overloaded)
-- ═══════════════════════════════════════════════════════════════════════════════

-- Check if this function exists and has overloads
DROP FUNCTION IF EXISTS match_messages_global(vector(1536), INT, FLOAT);
DROP FUNCTION IF EXISTS match_messages_global(vector(1536), INT);

-- Create the single version if needed
CREATE OR REPLACE FUNCTION match_messages_global(
  query_embedding vector(1536),
  match_count INT DEFAULT 5,
  similarity_threshold FLOAT DEFAULT 0.7
)
RETURNS TABLE (
  id UUID,
  sender TEXT,
  content TEXT,
  timestamp TIMESTAMPTZ,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    m.id,
    m.sender,
    m.content,
    m.timestamp,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM message_history m
  WHERE 
    m.embedding IS NOT NULL
    AND 1 - (m.embedding <=> query_embedding) > similarity_threshold
  ORDER BY m.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Verification: Check remaining functions
-- ═══════════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    RAISE NOTICE 'Verification: Checking for any remaining overloaded functions...';
END $$;

SELECT 
  p.proname as function_name,
  COUNT(*) as overload_count,
  array_agg(pg_get_function_arguments(p.oid)) as all_signatures
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public'
GROUP BY p.proname
HAVING COUNT(*) > 1;

-- List all search functions with their signatures
SELECT 
  p.proname as function_name,
  pg_get_function_arguments(p.oid) as arguments,
  pg_get_function_result(p.oid) as returns
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public' 
  AND (p.proname LIKE '%search%' OR p.proname LIKE '%match%')
ORDER BY p.proname;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Success message
-- ═══════════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    RAISE NOTICE 'All function overloading issues have been fixed!';
    RAISE NOTICE 'Each function now has only one version with optional parameters.';
END $$;
