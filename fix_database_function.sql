-- Fix the search_contacts_enhanced function to return DOUBLE PRECISION instead of FLOAT
CREATE OR REPLACE FUNCTION search_contacts_enhanced(
  search_query TEXT,
  limit_count INT DEFAULT 10
)
RETURNS TABLE (
  id BIGINT,
  email TEXT,
  name TEXT,
  first_name TEXT,
  last_name TEXT,
  display_name TEXT,
  role TEXT,
  company TEXT,
  phone TEXT,
  score DOUBLE PRECISION  -- Changed from FLOAT to DOUBLE PRECISION
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  WITH search_terms AS (
    SELECT LOWER(TRIM(search_query)) as term
  ),
  scored_results AS (
    SELECT 
      c.id,
      c.email,
      c.name,
      c.first_name,
      c.last_name,
      c.display_name,
      c.role,
      c.company,
      c.phone,
      CASE
        -- Exact email match
        WHEN LOWER(c.email) = (SELECT term FROM search_terms) THEN 100.0
        -- Exact name match
        WHEN LOWER(c.name) = (SELECT term FROM search_terms) THEN 95.0
        WHEN LOWER(c.display_name) = (SELECT term FROM search_terms) THEN 95.0
        -- Exact first or last name match
        WHEN LOWER(c.first_name) = (SELECT term FROM search_terms) THEN 90.0
        WHEN LOWER(c.last_name) = (SELECT term FROM search_terms) THEN 90.0
        -- Starts with match
        WHEN LOWER(c.email) LIKE (SELECT term FROM search_terms) || '%' THEN 85.0
        WHEN LOWER(c.name) LIKE (SELECT term FROM search_terms) || '%' THEN 80.0
        WHEN LOWER(c.first_name) LIKE (SELECT term FROM search_terms) || '%' THEN 75.0
        WHEN LOWER(c.last_name) LIKE (SELECT term FROM search_terms) || '%' THEN 75.0
        -- Contains match
        WHEN LOWER(c.email) LIKE '%' || (SELECT term FROM search_terms) || '%' THEN 70.0
        WHEN LOWER(c.name) LIKE '%' || (SELECT term FROM search_terms) || '%' THEN 65.0
        WHEN LOWER(c.company) LIKE '%' || (SELECT term FROM search_terms) || '%' THEN 60.0
        -- Alias match
        WHEN EXISTS (
          SELECT 1 FROM unnest(c.aliases) AS alias 
          WHERE LOWER(alias) LIKE '%' || (SELECT term FROM search_terms) || '%'
        ) THEN 70.0
        -- Tag match
        WHEN EXISTS (
          SELECT 1 FROM unnest(c.tags) AS tag 
          WHERE LOWER(tag) LIKE '%' || (SELECT term FROM search_terms) || '%'
        ) THEN 50.0
        ELSE 0.0
      END::DOUBLE PRECISION as match_score  -- Explicitly cast to DOUBLE PRECISION
    FROM contacts c
  )
  SELECT 
    sr.id,
    sr.email,
    sr.name,
    sr.first_name,
    sr.last_name,
    sr.display_name,
    sr.role,
    sr.company,
    sr.phone,
    sr.match_score as score
  FROM scored_results sr
  WHERE sr.match_score > 0
  ORDER BY sr.match_score DESC, sr.name ASC
  LIMIT limit_count;
END;
$$;
