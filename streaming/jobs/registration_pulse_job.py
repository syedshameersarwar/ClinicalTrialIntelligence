"""
BenchMark PyFlink Job: 1-day tumbling window per therapeutic area.

Reads from Redpanda 'trial-updates' topic (bounded mode — processes all current
messages then stops). Computes daily update counts per therapeutic area.
Writes results to PostgreSQL 'daily_activity' table.
"""
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.table import StreamTableEnvironment, EnvironmentSettings

env = StreamExecutionEnvironment.get_execution_environment()
env.set_parallelism(1)

t_env = StreamTableEnvironment.create(
    env,
    EnvironmentSettings.new_instance().in_streaming_mode().build()
)

# ── Source: Kafka (Redpanda) ──────────────────────────────────────────────────
# event_time is computed from event_date (YYYY-MM-DD string → TIMESTAMP).
# All events from one daily run share the same event_date (yesterday's date),
# so they all fall in the same 1-day tumbling window.
# scan.bounded.mode = 'latest-offset': process all messages in topic then stop.
t_env.execute_sql("""
    CREATE TABLE trial_updates (
        nct_id           STRING,
        overall_status   STRING,
        phase            STRING,
        therapeutic_area STRING,
        sponsor_class    STRING,
        enrollment       INT,
        event_date       STRING,
        lead_sponsor     STRING,
        event_time AS TO_TIMESTAMP(event_date, 'yyyy-MM-dd'),
        WATERMARK FOR event_time AS event_time - INTERVAL '5' SECONDS
    ) WITH (
        'connector'                    = 'kafka',
        'topic'                        = 'trial-updates',
        'properties.bootstrap.servers' = 'redpanda:29092',
        'properties.group.id'          = 'pulse-consumer',
        'scan.startup.mode'            = 'group-offsets',
        'properties.auto.offset.reset' = 'earliest',
        'scan.bounded.mode'            = 'latest-offset',
        'format'                       = 'json'
    )
""")

# ── Sink: PostgreSQL via JDBC ─────────────────────────────────────────────────
# init.sql creates this table on container startup.
# pg_to_bq.py reads it, appends to BigQuery, then TRUNCATES it.
t_env.execute_sql("""
    CREATE TABLE daily_activity (
        window_start       TIMESTAMP(3),
        window_end         TIMESTAMP(3),
        therapeutic_area   STRING,
        update_count       BIGINT,
        industry_sponsored BIGINT,
        avg_enrollment     DOUBLE,
        completed_count    BIGINT
    ) WITH (
        'connector'  = 'jdbc',
        'url'        = 'jdbc:postgresql://postgres:5432/benchmark',
        'table-name' = 'daily_activity',
        'username'   = 'postgres',
        'password'   = 'postgres',
        'driver'     = 'org.postgresql.Driver'
    )
""")

# ── Query: 1-day tumbling window, group by therapeutic_area ──────────────────
# Each daily run produces one row per therapeutic area: window_start = yesterday 00:00,
# window_end = today 00:00.
t_env.execute_sql("""
    INSERT INTO daily_activity
    SELECT
        window_start,
        window_end,
        therapeutic_area,
        COUNT(*)                                                       AS update_count,
        SUM(CASE WHEN sponsor_class = 'INDUSTRY' THEN 1 ELSE 0 END)   AS industry_sponsored,
        AVG(CAST(enrollment AS DOUBLE))                                AS avg_enrollment,
        SUM(CASE WHEN overall_status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_count
    FROM TABLE(
        TUMBLE(TABLE trial_updates,
               DESCRIPTOR(event_time), INTERVAL '1' DAY)
    )
    GROUP BY window_start, window_end, therapeutic_area
""").wait()
