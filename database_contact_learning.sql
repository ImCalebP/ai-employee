-- ═══════════════════════════════════════════════════════════════════════════════
-- Contact Learning System Tables
-- For tracking pending contacts and mentions
-- ═══════════════════════════════════════════════════════════════════════════════

-- Table for tracking contacts that have been mentioned but need more information
CREATE TABLE IF NOT EXISTS pending_contacts (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    mentioned_at TIMESTAMPTZ DEFAULT NOW(),
    context TEXT,
    known_info JSONB DEFAULT '{}',
    missing_info TEXT[] DEFAULT '{}',
    confidence FLOAT DEFAULT 0.0,
    status TEXT DEFAULT 'pending', -- pending, gathering, complete, abandoned
    completed_at TIMESTAMPTZ,
    contact_id BIGINT REFERENCES contacts(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Table for tracking when existing contacts are mentioned in conversations
CREATE TABLE IF NOT EXISTS contact_mentions (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT REFERENCES contacts(id),
    chat_id TEXT NOT NULL,
    message_id TEXT,
    context TEXT,
    mentioned_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_pending_contacts_chat_status 
ON pending_contacts(chat_id, status);

CREATE INDEX IF NOT EXISTS idx_pending_contacts_name 
ON pending_contacts(LOWER(name));

CREATE INDEX IF NOT EXISTS idx_contact_mentions_contact_chat 
ON contact_mentions(contact_id, chat_id);

CREATE INDEX IF NOT EXISTS idx_contact_mentions_chat 
ON contact_mentions(chat_id);

-- Update timestamp trigger for pending_contacts
CREATE OR REPLACE FUNCTION update_pending_contacts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_pending_contacts_updated_at
BEFORE UPDATE ON pending_contacts
FOR EACH ROW
EXECUTE FUNCTION update_pending_contacts_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════════
-- Helper function to get pending contacts summary
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION get_pending_contacts_summary(p_chat_id TEXT DEFAULT NULL)
RETURNS TABLE (
    chat_id TEXT,
    pending_count BIGINT,
    oldest_pending TIMESTAMPTZ,
    names TEXT[]
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        pc.chat_id,
        COUNT(*)::BIGINT as pending_count,
        MIN(pc.mentioned_at) as oldest_pending,
        ARRAY_AGG(pc.name ORDER BY pc.mentioned_at) as names
    FROM pending_contacts pc
    WHERE pc.status = 'pending'
    AND (p_chat_id IS NULL OR pc.chat_id = p_chat_id)
    GROUP BY pc.chat_id;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Function to complete a pending contact
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION complete_pending_contact(
    p_pending_id BIGINT,
    p_contact_id BIGINT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE pending_contacts
    SET 
        status = 'complete',
        completed_at = NOW(),
        contact_id = p_contact_id,
        updated_at = NOW()
    WHERE id = p_pending_id
    AND status = 'pending';
    
    RETURN FOUND;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Function to get contact mention history
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION get_contact_mention_history(
    p_contact_id BIGINT,
    p_limit INT DEFAULT 10
)
RETURNS TABLE (
    chat_id TEXT,
    message_id TEXT,
    context TEXT,
    mentioned_at TIMESTAMPTZ
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        cm.chat_id,
        cm.message_id,
        cm.context,
        cm.mentioned_at
    FROM contact_mentions cm
    WHERE cm.contact_id = p_contact_id
    ORDER BY cm.mentioned_at DESC
    LIMIT p_limit;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- VERIFICATION
-- ═══════════════════════════════════════════════════════════════════════════════

SELECT 'Contact learning system tables created successfully' as status;

-- Show table structure
SELECT 
    table_name,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name IN ('pending_contacts', 'contact_mentions')
ORDER BY table_name, ordinal_position;
