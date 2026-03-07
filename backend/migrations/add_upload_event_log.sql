-- Upload Observability: upload_event_log table
-- Records each stage of the upload pipeline for diagnostics

CREATE TABLE IF NOT EXISTS upload_event_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    video_id        VARCHAR(36)  NOT NULL,
    upload_id       VARCHAR(36)  NULL,
    user_id         INT          NULL,
    stage           VARCHAR(50)  NOT NULL COMMENT 'Pipeline stage: validate | db_record | sas_generate | enqueue | persist_evidence | cleanup | blob_verify',
    status          VARCHAR(20)  NOT NULL COMMENT 'ok | error | skipped',
    duration_ms     INT          NULL     COMMENT 'Time taken for this stage in milliseconds',
    error_message   TEXT         NULL     COMMENT 'Error details if status=error',
    error_type      VARCHAR(100) NULL     COMMENT 'Exception class name or error category',
    metadata_json   JSON         NULL     COMMENT 'Additional context (file size, upload_type, etc.)',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_upload_event_video (video_id),
    INDEX idx_upload_event_user (user_id),
    INDEX idx_upload_event_stage (stage, status),
    INDEX idx_upload_event_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Add upload_stage column to videos table for quick status check
ALTER TABLE videos ADD COLUMN IF NOT EXISTS upload_last_stage VARCHAR(50) NULL COMMENT 'Last completed upload pipeline stage';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS upload_error_stage VARCHAR(50) NULL COMMENT 'Stage where upload failed (if any)';
ALTER TABLE videos ADD COLUMN IF NOT EXISTS upload_error_message TEXT NULL COMMENT 'Error message from failed stage';
