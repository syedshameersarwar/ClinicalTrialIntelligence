-- PostgreSQL sink table for PyFlink registration pulse job.
-- One row per (therapeutic_area, day) appended per daily Kestra run.
-- Table is truncated by pg_to_bq.py after each successful BigQuery sync.
CREATE TABLE IF NOT EXISTS daily_activity (
    window_start       TIMESTAMP,
    window_end         TIMESTAMP,
    therapeutic_area   TEXT,
    update_count       BIGINT,
    industry_sponsored BIGINT,
    avg_enrollment     FLOAT,
    completed_count    BIGINT
);
