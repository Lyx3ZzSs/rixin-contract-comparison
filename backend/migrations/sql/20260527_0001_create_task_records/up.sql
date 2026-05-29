CREATE TABLE task_records (
    task_id VARCHAR(64) PRIMARY KEY,
    task_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    stage VARCHAR(128) NOT NULL,
    progress_percent INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    filename VARCHAR(512) NOT NULL,
    original_filename VARCHAR(512) NOT NULL,
    compare_filename VARCHAR(512) NOT NULL,
    schema_version INTEGER NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX ix_task_records_task_type_updated_at
    ON task_records (task_type, updated_at);

CREATE INDEX ix_task_records_status
    ON task_records (status);
