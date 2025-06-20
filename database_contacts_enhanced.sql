-- ═══════════════════════════════════════════════════════════════════════════════
-- Enhanced Contacts System for AI Employee
-- Complete fresh setup - This will DROP and recreate the contacts table
-- ═══════════════════════════════════════════════════════════════════════════════

-- Drop existing triggers
DROP TRIGGER IF EXISTS trigger_auto_populate_names ON contacts;
DROP TRIGGER IF EXISTS update_contacts_updated_at ON contacts;

-- Drop existing contacts table and related objects
DROP TABLE IF EXISTS contact_mentions CASCADE;
DROP TABLE IF EXISTS contact_interactions CASCADE;
DROP TABLE IF EXISTS contact_relationships CASCADE;
DROP TABLE IF EXISTS contacts CASCADE;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 1. ENHANCED CONTACTS TABLE
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE contacts (
  id BIGSERIAL PRIMARY KEY,
  
  -- Basic Information
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  display_name TEXT,
  
  -- Professional Information
  role TEXT,
  department TEXT,
  company TEXT,
  title TEXT,
  
  -- Contact Details
  phone TEXT,
  mobile_phone TEXT,
  work_phone TEXT,
  
  -- Communication Preferences
  preferred_contact_method TEXT DEFAULT 'email',
  timezone TEXT,
  language TEXT DEFAULT 'en',
  
  -- Relationship Context
  relationship_type TEXT DEFAULT 'professional',
  importance_level INT DEFAULT 3,
  last_interaction TIMESTAMPTZ,
  interaction_count INT DEFAULT 0,
  
  -- Teams/Microsoft Context
  teams_id TEXT,
  conversation_id TEXT,
  
  -- AI Context
  notes TEXT,
  tags TEXT[],
  aliases TEXT[],
  
  -- Metadata
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_by TEXT,
  source TEXT DEFAULT 'manual',
  
  -- Embedding for semantic search
  embedding vector(1536),
  
  -- Additional metadata
  metadata JSONB DEFAULT '{}'
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 2. INDEXES FOR PERFORMANCE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE INDEX idx_contacts_email ON contacts(LOWER(email));
CREATE INDEX idx_contacts_name ON contacts(LOWER(name));
CREATE INDEX idx_contacts_first_name ON contacts(LOWER(first_name));
CREATE INDEX idx_contacts_last_name ON contacts(LOWER(last_name));
CREATE INDEX idx_contacts_company ON contacts(LOWER(company));
CREATE INDEX idx_contacts_conversation_id ON contacts(conversation_id);
CREATE INDEX idx_contacts_tags ON contacts USING GIN(tags);
CREATE INDEX idx_contacts_aliases ON contacts USING GIN(aliases);

-- Vector index for semantic search
CREATE INDEX idx_contacts_embedding ON contacts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 3. ENHANCED SEARCH FUNCTION (with scoring)
-- ═══════════════════════════════════════════════════════════════════════════

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
  score FLOAT
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
      END as match_score
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

-- ═══════════════════════════════════════════════════════════════════════════════
-- 4. AUTO-POPULATE NAME PARTS TRIGGER
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION auto_populate_name_parts()
RETURNS TRIGGER AS $$
DECLARE
  name_parts TEXT[];
BEGIN
  -- Only process if first_name and last_name are null but name is provided
  IF NEW.name IS NOT NULL AND NEW.first_name IS NULL AND NEW.last_name IS NULL THEN
    name_parts := string_to_array(NEW.name, ' ');
    IF array_length(name_parts, 1) >= 2 THEN
      NEW.first_name := name_parts[1];
      NEW.last_name := name_parts[array_length(name_parts, 1)];
    ELSIF array_length(name_parts, 1) = 1 THEN
      NEW.first_name := name_parts[1];
    END IF;
  END IF;
  
  -- Set display_name if not provided
  IF NEW.display_name IS NULL AND NEW.name IS NOT NULL THEN
    NEW.display_name := NEW.name;
  END IF;
  
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_auto_populate_names
BEFORE INSERT OR UPDATE ON contacts
FOR EACH ROW
EXECUTE FUNCTION auto_populate_name_parts();

-- Update timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_contacts_updated_at 
BEFORE UPDATE ON contacts 
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 5. SAMPLE DATA FOR TESTING
-- ═══════════════════════════════════════════════════════════════════════════════

-- Insert some sample contacts
INSERT INTO contacts (email, name, role, company, phone, tags, aliases) VALUES 
('max.wilson@techcorp.com', 'Max Wilson', 'Engineering Manager', 'TechCorp', '+1-555-0123', ARRAY['engineering', 'manager'], ARRAY['Maxwell', 'Max W']),
('sarah.jones@techcorp.com', 'Sarah Jones', 'Product Manager', 'TechCorp', '+1-555-0124', ARRAY['product', 'manager'], ARRAY['SJ', 'Sarah J']),
('roger@gmail.com', 'Roger Smith', 'Consultant', 'Independent', '+1-555-0125', ARRAY['consultant', 'external'], ARRAY['Rog', 'Roger S']),
('john.doe@example.com', 'John Doe', 'Developer', 'Example Inc', '+1-555-0126', ARRAY['developer', 'technical'], ARRAY['JD', 'Johnny']),
('alice.brown@startup.io', 'Alice Brown', 'CEO', 'Startup.io', '+1-555-0127', ARRAY['executive', 'leadership'], ARRAY['Alice B', 'AB']);

-- ═══════════════════════════════════════════════════════════════════════════════
-- VERIFICATION
-- ═══════════════════════════════════════════════════════════════════════════════

SELECT 'Enhanced contacts system setup complete' as status;
SELECT COUNT(*) as contact_count FROM contacts;
