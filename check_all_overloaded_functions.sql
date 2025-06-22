-- Check for all potentially overloaded functions in the public schema
-- This will help identify any other functions that might have similar issues

-- Find all functions grouped by name with their argument counts
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

-- Specifically check our search functions
SELECT 
  p.proname as function_name,
  pg_get_function_arguments(p.oid) as arguments,
  pg_get_function_result(p.oid) as returns
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public' 
  AND p.proname LIKE '%search%'
ORDER BY p.proname;
