-- ═══════════════════════════════════════════════════════════════════════════════
-- AI Employee Database Setup for Supabase
-- Enhanced with semantic search and document intelligence
-- Vector dimensions: 1536 (text-embedding-ada-002)
-- ═══════════════════════════════════════════════════════════════════════════════

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 1. CONTACT MENTIONS TABLE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS contact_mentions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID,  -- You might want to reference the contacts table here
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    mention_type TEXT,  -- e.g., 'name', 'email', 'alias'
    context TEXT,       -- Additional context for the mention
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 2. ENHANCED DOCUMENTS TABLE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  type TEXT NOT NULL, -- 'meeting_summary', 'report', 'conversation_summary', etc.
  content TEXT NOT NULL,
  file_path TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  chat_id TEXT,
  meeting_id TEXT,
  author TEXT,
  metadata JSONB DEFAULT '{}',
  embedding vector(1536) -- OpenAI text-embedding-ada-002
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 2. ENHANCED TASKS TABLE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  description TEXT NOT NULL,
  assignee TEXT,
  assignee_email TEXT,
  due_date TIMESTAMPTZ,
  priority TEXT DEFAULT 'medium', -- low, medium, high, critical
  status TEXT DEFAULT 'pending', -- pending, in_progress, completed, cancelled
  chat_id TEXT,
  project_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  metadata JSONB DEFAULT '{}',
  embedding vector(1536)
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 3. DOCUMENT-MESSAGE LINKING TABLE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS document_mentions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
  message_id UUID,
  chat_id TEXT NOT NULL,
  relevance_score FLOAT DEFAULT 0.0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 4. PERFORMANCE INDEXES
-- ═══════════════════════════════════════════════════════════════════════════════

-- Document indexes
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(type);
CREATE INDEX IF NOT EXISTS idx_documents_chat_id ON documents(chat_id);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_author ON documents(author);
CREATE INDEX IF NOT EXISTS idx_documents_meeting_id ON documents(meeting_id);

-- Task indexes
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_email);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_chat_id ON tasks(chat_id);

-- Document mention indexes
CREATE INDEX IF NOT EXISTS idx_document_mentions_chat_id ON document_mentions(chat_id);
CREATE INDEX IF NOT EXISTS idx_document_mentions_document_id ON document_mentions(document_id);
CREATE INDEX IF NOT EXISTS idx_document_mentions_relevance ON document_mentions(relevance_score DESC);

-- Vector indexes for semantic search (using ivfflat)
CREATE INDEX IF NOT EXISTS idx_documents_embedding ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_tasks_embedding ON tasks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 5. PENDING ACTIONS TABLE
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS pending_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id TEXT NOT NULL,
    action_sequence JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 6. SEMANTIC SEARCH FUNCTIONS
-- ═══════════════════════════════════════════════════════════════════════════════

-- Document semantic search function (base version)
CREATE OR REPLACE FUNCTION search_documents_semantic_base(
  query_embedding vector(1536),
  match_count INT DEFAULT 5,
  similarity_threshold FLOAT DEFAULT 0.7
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
    1 - (d.embedding <=> query_embedding) > similarity_threshold
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- Document semantic search function
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
    1 - (d.embedding <=> query_embedding) > similarity_threshold
    AND (doc_type_filter IS NULL OR d.type = doc_type_filter)
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- Task semantic search function
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
    1 - (t.embedding <=> query_embedding) > similarity_threshold
    AND (status_filter IS NULL OR t.status = status_filter)
  ORDER BY t.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- Combined search across all entities
CREATE OR REPLACE FUNCTION search_all_entities(
  query_embedding vector(1536),
  match_count INT DEFAULT 10,
  similarity_threshold FLOAT DEFAULT 0.6
)
RETURNS TABLE (
  entity_type TEXT,
  entity_id UUID,
  title TEXT,
  content TEXT,
  similarity FLOAT,
  created_at TIMESTAMPTZ
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  (
    SELECT 
      'document'::TEXT as entity_type,
      d.id as entity_id,
      d.title,
      d.content,
      1 - (d.embedding <=> query_embedding) AS similarity,
      d.created_at
    FROM documents d
    WHERE 1 - (d.embedding <=> query_embedding) > similarity_threshold
    
    UNION ALL
    
    SELECT 
      'task'::TEXT as entity_type,
      t.id as entity_id,
      t.description as title,
      t.description as content,
      1 - (t.embedding <=> query_embedding) AS similarity,
      t.created_at
    FROM tasks t
    WHERE 1 - (t.embedding <=> query_embedding) > similarity_threshold
  )
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 6. UTILITY FUNCTIONS
-- ═══════════════════════════════════════════════════════════════════════════════

-- Get documents mentioned in a conversation
CREATE OR REPLACE FUNCTION get_conversation_documents(
  chat_id_param TEXT,
  limit_count INT DEFAULT 5
)
RETURNS TABLE (
  document_id UUID,
  title TEXT,
  type TEXT,
  relevance_score FLOAT,
  mention_count BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    dm.document_id,
    d.title,
    d.type,
    AVG(dm.relevance_score) as relevance_score,
    COUNT(*) as mention_count
  FROM document_mentions dm
  JOIN documents d ON dm.document_id = d.id
  WHERE dm.chat_id = chat_id_param
  GROUP BY dm.document_id, d.title, d.type
  ORDER BY AVG(dm.relevance_score) DESC, COUNT(*) DESC
  LIMIT limit_count;
END;
$$;

-- Get overdue tasks
CREATE OR REPLACE FUNCTION get_overdue_tasks()
RETURNS TABLE (
  id UUID,
  description TEXT,
  assignee_email TEXT,
  due_date TIMESTAMPTZ,
  days_overdue INT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    t.id,
    t.description,
    t.assignee_email,
    t.due_date,
    EXTRACT(DAY FROM NOW() - t.due_date)::INT as days_overdue
  FROM tasks t
  WHERE 
    t.status = 'pending' 
    AND t.due_date < NOW()
  ORDER BY t.due_date ASC;
END;
$$;

-- Get task statistics by assignee
CREATE OR REPLACE FUNCTION get_task_stats_by_assignee()
RETURNS TABLE (
  assignee_email TEXT,
  total_tasks BIGINT,
  pending_tasks BIGINT,
  completed_tasks BIGINT,
  overdue_tasks BIGINT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    t.assignee_email,
    COUNT(*) as total_tasks,
    COUNT(*) FILTER (WHERE t.status = 'pending') as pending_tasks,
    COUNT(*) FILTER (WHERE t.status = 'completed') as completed_tasks,
    COUNT(*) FILTER (WHERE t.status = 'pending' AND t.due_date < NOW()) as overdue_tasks
  FROM tasks t
  WHERE t.assignee_email IS NOT NULL
  GROUP BY t.assignee_email
  ORDER BY total_tasks DESC;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 7. TRIGGERS FOR AUTOMATIC UPDATES
-- ═══════════════════════════════════════════════════════════════════════════════

-- Update timestamp trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply triggers to tables
DROP TRIGGER IF EXISTS update_documents_updated_at ON documents;
CREATE TRIGGER update_documents_updated_at 
    BEFORE UPDATE ON documents 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tasks_updated_at ON tasks;
CREATE TRIGGER update_tasks_updated_at 
    BEFORE UPDATE ON tasks 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 8. ROW LEVEL SECURITY (Optional - uncomment if needed)
-- ═══════════════════════════════════════════════════════════════════════════════

-- Enable RLS on tables (uncomment if you want row-level security)
-- ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE document_mentions ENABLE ROW LEVEL SECURITY;

-- Example policies (uncomment and modify as needed)
-- CREATE POLICY "Users can view all documents" ON documents FOR SELECT USING (true);
-- CREATE POLICY "Users can insert documents" ON documents FOR INSERT WITH CHECK (true);
-- CREATE POLICY "Users can update their documents" ON documents FOR UPDATE USING (true);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 9. SAMPLE DATA (Optional - for testing)
-- ═══════════════════════════════════════════════════════════════════════════════

-- Insert sample document (uncomment for testing)
-- INSERT INTO documents (title, type, content, author) VALUES 
-- ('Sample Meeting Notes', 'meeting_summary', 'This is a sample meeting summary for testing purposes.', 'AI Assistant');

-- Insert sample task (uncomment for testing)
-- INSERT INTO tasks (description, assignee_email, priority, status) VALUES 
-- ('Test task for system validation', 'test@example.com', 'medium', 'pending');

-- ═══════════════════════════════════════════════════════════════════════════════
-- SETUP COMPLETE
-- ═══════════════════════════════════════════════════════════════════════════════

-- Verify setup
SELECT 'Documents table created' as status, COUNT(*) as row_count FROM documents
UNION ALL
SELECT 'Tasks table created' as status, COUNT(*) as row_count FROM tasks
UNION ALL
SELECT 'Document mentions table created' as status, COUNT(*) as row_count FROM document_mentions;

-- Show available functions
SELECT 
  routine_name as function_name,
  routine_type
FROM information_schema.routines 
WHERE routine_schema = 'public' 
  AND routine_name LIKE '%search%' 
  OR routine_name LIKE '%get_%'
ORDER BY routine_name;
