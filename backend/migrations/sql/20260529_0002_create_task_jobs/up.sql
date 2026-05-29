CREATE TABLE task_jobs (
    job_id VARCHAR(128) PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    queued_at TIMESTAMP WITH TIME ZONE NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    next_run_at TIMESTAMP WITH TIME ZONE,
    lease_owner VARCHAR(128) NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX ix_task_jobs_task_latest
    ON task_jobs (task_id, task_type, updated_at DESC);

CREATE INDEX ix_task_jobs_claim
    ON task_jobs (status, next_run_at, queued_at);

CREATE INDEX ix_task_jobs_lease_expires
    ON task_jobs (status, lease_expires_at);
