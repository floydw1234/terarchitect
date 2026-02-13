-- pr_review_comments: Track PR comments and whether we've addressed (replied to) them.
-- Used to process only the most recent unaddressed comment from a human, not our own.
CREATE TABLE IF NOT EXISTS pr_review_comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    pr_number INTEGER NOT NULL,
    github_comment_id BIGINT NOT NULL,
    author_login VARCHAR(255),
    body TEXT,
    comment_created_at TIMESTAMP WITH TIME ZONE,
    addressed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(project_id, pr_number, github_comment_id)
);

CREATE INDEX idx_pr_review_comments_lookup ON pr_review_comments(project_id, pr_number, addressed_at);
