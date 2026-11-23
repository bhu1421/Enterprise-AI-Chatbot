
-- ============================================================
-- 1. DOCUMENTS TABLE
-- ============================================================
-- Stores document metadata, tenant ownership, access control,
-- S3 storage location, processing status, and version information.
-- ============================================================

CREATE TABLE IF NOT EXISTS documents (
    id              uuid PRIMARY KEY,
    tenant_id       text NOT NULL,
    title           text NOT NULL,
    owner_sub       text NOT NULL,
    allowed_groups  text[] NOT NULL,
    s3_key          text NOT NULL UNIQUE,
    status          text NOT NULL CHECK (
                        status IN (
                            'pending',
                            'processing',
                            'ready',
                            'failed',
                            'retired'
                        )
                    ),
    current_version integer NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Index for tenant-based document filtering and status queries.
CREATE INDEX IF NOT EXISTS documents_acl_idx
    ON documents(tenant_id, status);


-- ============================================================
-- 2. FEEDBACK TABLE
-- ============================================================
-- Stores user feedback associated with a tenant.
-- Ratings are restricted to values between -1 and 1.
-- ============================================================

CREATE TABLE IF NOT EXISTS feedback (
    id          bigserial PRIMARY KEY,
    tenant_id   text NOT NULL,
    actor       text NOT NULL,
    rating      integer NOT NULL CHECK (
                    rating BETWEEN -1 AND 1
                ),
    note        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);


-- ============================================================
-- 3. AUDIT EVENTS TABLE
-- ============================================================
-- Records system and user actions for auditing and tracking.
-- Event details are stored in JSONB format.
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_events (
    id          bigserial PRIMARY KEY,
    actor       text NOT NULL,
    tenant_id   text NOT NULL,
    action      text NOT NULL,
    detail      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Index for retrieving tenant-specific audit events,
-- ordered by creation time (newest first).
CREATE INDEX IF NOT EXISTS audit_tenant_time
    ON audit_events(tenant_id, created_at DESC);


-- ============================================================
-- 4. APPROVALS TABLE
-- ============================================================
-- Stores approval requests, requester information,
-- review status, reviewer identity, and timestamps.
-- ============================================================

CREATE TABLE IF NOT EXISTS approvals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   text NOT NULL,
    requester   text NOT NULL,
    question    text NOT NULL,
    status      text NOT NULL DEFAULT 'pending',
    reviewer    text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);

ALTER TABLE approvals ADD COLUMN IF NOT EXISTS action text NOT NULL DEFAULT 'general';
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS document_id uuid;

-- Index for filtering approval requests by tenant and status.
CREATE INDEX IF NOT EXISTS approvals_tenant_idx
    ON approvals(tenant_id, status);


-- ============================================================
-- 5. CHAT MESSAGES TABLE
-- ============================================================
-- Persistent short-term conversation memory stored in Aurora.
-- No local database is used.
--
-- Stores tenant-specific conversation messages,
-- including conversation ID, message role, and content.
-- ============================================================

CREATE TABLE IF NOT EXISTS chat_messages (
    id              bigserial PRIMARY KEY,
    tenant_id       text NOT NULL,
    conversation_id uuid NOT NULL,
    role            text NOT NULL CHECK (
                        role IN ('user', 'assistant')
                    ),
    content         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Index for efficiently retrieving messages belonging to
-- a specific tenant and conversation.
-- Messages are indexed by ID in descending order.
CREATE INDEX IF NOT EXISTS chat_messages_session_idx
    ON chat_messages(tenant_id, conversation_id, id DESC);
