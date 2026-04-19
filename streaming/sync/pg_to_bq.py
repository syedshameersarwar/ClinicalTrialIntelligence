"""
BenchMark Sync: PostgreSQL daily_activity → BigQuery streaming.daily_activity.

Uses WRITE_APPEND: each daily run adds new rows to BigQuery, building a
time-series table of daily CT.gov update activity per therapeutic area.
After a successful sync the PostgreSQL table is truncated so the next run starts fresh.

Called by Kestra daily_streaming_flow after the PyFlink job completes.

Environment variables required:
  GCP_PROJECT_ID                — GCP project ID
  GOOGLE_APPLICATION_CREDENTIALS — path to service account key JSON file
"""
import json
import os
import sys

import pandas as pd
import psycopg2
from google.cloud import bigquery
from google.oauth2 import service_account

BQ_PROJECT = os.environ.get("GCP_PROJECT_ID")
if not BQ_PROJECT:
    print("ERROR: GCP_PROJECT_ID environment variable not set.")
    sys.exit(1)

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = os.environ.get("PG_PORT", "5433")
PG_DSN  = f"host={PG_HOST} port={PG_PORT} dbname=benchmark user=postgres password=postgres"

BQ_TABLE = f"{BQ_PROJECT}.streaming.daily_activity"

SCHEMA = [
    bigquery.SchemaField("window_start",       "TIMESTAMP"),
    bigquery.SchemaField("window_end",         "TIMESTAMP"),
    bigquery.SchemaField("therapeutic_area",   "STRING"),
    bigquery.SchemaField("update_count",       "INT64"),
    bigquery.SchemaField("industry_sponsored", "INT64"),
    bigquery.SchemaField("avg_enrollment",     "FLOAT64"),
    bigquery.SchemaField("completed_count",    "INT64"),
]

BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]


def make_bq_client() -> bigquery.Client:
    """Build a BigQuery client from the GOOGLE_APPLICATION_CREDENTIALS file."""
    creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_file:
        raise EnvironmentError("GOOGLE_APPLICATION_CREDENTIALS is not set.")
    with open(creds_file) as fh:
        # strict=False tolerates actual newlines in the private key field
        sa_info = json.loads(fh.read(), strict=False)
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=BQ_SCOPES
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def main():
    with psycopg2.connect(PG_DSN) as conn:
        df = pd.read_sql("SELECT * FROM daily_activity", conn)

    if df.empty:
        print("WARNING: daily_activity table is empty — did the PyFlink job run successfully?")
        sys.exit(0)

    print(f"Read {len(df):,} rows from PostgreSQL daily_activity.")

    bq = make_bq_client()

    # Deduplicate: skip dates already present in BigQuery so re-runs and backfill overlaps are safe.
    try:
        existing = bq.query(
            f"SELECT DISTINCT DATE(window_start) AS d FROM `{BQ_TABLE}`"
        ).to_dataframe()
        existing_dates = set(existing["d"].astype(str))
    except Exception:
        existing_dates = set()

    df["_date"] = pd.to_datetime(df["window_start"]).dt.date.astype(str)
    skipped = df[df["_date"].isin(existing_dates)]
    df = df[~df["_date"].isin(existing_dates)].drop(columns=["_date"])
    if not skipped.empty:
        print(f"Skipping {len(skipped):,} rows for already-synced dates: "
              f"{sorted(skipped['_date'].unique())}")

    if df.empty:
        print("Nothing new to sync — all dates already in BigQuery.")
    else:
        job = bq.load_table_from_dataframe(
            df,
            BQ_TABLE,
            job_config=bigquery.LoadJobConfig(
                schema=SCHEMA,
                write_disposition="WRITE_APPEND",
            ),
        )
        job.result()
        print(f"Appended {len(df):,} rows → {BQ_TABLE}")

    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE daily_activity")
        conn.commit()
    print("Truncated PostgreSQL daily_activity (ready for next run).")


if __name__ == "__main__":
    main()
