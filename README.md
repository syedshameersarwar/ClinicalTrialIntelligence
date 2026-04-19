# BenchMark: Clinical Trial Research Intelligence Platform

A production-grade data engineering capstone that ingests, processes, and visualizes clinical trial data from ClinicalTrials.gov — the same public registry used by pharmaceutical companies, academic institutions, and regulators worldwide to register clinical trials.

---

## Problem Statement

ClinicalTrials.gov maintains over 375,000 registered clinical trials. Research teams, biotech investors, and healthcare regulators need two kinds of insight that existing public tools don't provide together:

1. **Historical landscape** — How are trials distributed across therapeutic areas, phases, and sponsor types over the past decade? Are oncology trials dominated by academic or industry sponsors? What proportion of Phase 3 trials complete successfully?

2. **Recent activity signals** — Which therapeutic areas are seeing a surge in trial registrations or updates this week compared to historical norms? Are infectious disease updates spiking?

BenchMark answers both questions by combining:

- A **monthly batch pipeline** that processes the full AACT snapshot (~375K trials) through Apache Spark into BigQuery, then transforms it with dbt into a partitioned, clustered mart for categorical analysis.
- A **daily streaming pipeline** that fetches real CT.gov API updates, routes them through Redpanda (Kafka) and PyFlink, and appends aggregated daily counts to BigQuery — building a time-series mart for temporal trend analysis.

Both pipelines are orchestrated by Kestra and visualized in a two-tile Looker Studio dashboard.

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  BATCH PIPELINE  (Kestra: monthly, 1st of each month at 02:00 UTC)           ║
║                                                                              ║
║  AACT Export (~375K trials)                                                  ║
║  https://aact.ctti-clinicaltrials.org                                        ║
║         │                                                                    ║
║         ▼                                                                    ║
║  data/download_aact.sh                                                       ║
║  (wget + unzip → pipe-delimited .txt files)                                  ║
║         │                                                                    ║
║         ▼                                                                    ║
║  spark/enrich_and_load.py                                                    ║
║  ├─ read: studies.txt │ sponsors.txt │ browse_conditions.txt                 ║
║  ├─ join: 3-way on nct_id                                                    ║
║  ├─ classify: MeSH keyword → therapeutic_area (9 areas)                      ║
║  └─ write: BigQuery raw.trials                                               ║
║            (partitioned by start_year, clustered by therapeutic_area)        ║
║         │                                                                    ║
║         ▼                                                                    ║
║  dbt: stg_trials (view) → mart_trial_landscape (table)                       ║
║       partitioned by start_year, clustered by therapeutic_area + phase       ║
║         │                                                                    ║
║         ▼                                                                    ║
║  Looker Studio: Tile 1 — Clinical Trial Distribution (Stacked Bar)           ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║  STREAMING PIPELINE  (Kestra: daily at 06:00 UTC)                            ║
║                                                                              ║
║  ClinicalTrials.gov API v2                                                   ║
║  (filtered by LastUpdatePostDate = yesterday)                                ║
║         │                                                                    ║
║         ▼                                                                    ║
║  streaming/producers/producer.py                                             ║
║  ├─ fetch: ~500–1,000 trial updates per day                                  ║
║  ├─ classify: conditions → therapeutic_area                                  ║
║  └─ produce: JSON events → Redpanda topic "trial-updates"                    ║
║         │                                                                    ║
║         ▼                                                                    ║
║  Redpanda (Kafka-compatible broker)                                          ║
║  topic: trial-updates                                                        ║
║         │                                                                    ║
║         ▼                                                                    ║
║  PyFlink: registration_pulse_job.py                                          ║
║  ├─ 1-day tumbling window per therapeutic_area                               ║
║  ├─ aggregate: update_count, industry_sponsored, avg_enrollment              ║
║  └─ sink: PostgreSQL daily_activity table                                    ║
║         │                                                                    ║
║         ▼                                                                    ║
║  PostgreSQL (intermediate sink)                                              ║
║         │                                                                    ║
║         ▼                                                                    ║
║  streaming/sync/pg_to_bq.py                                                  ║
║  ├─ deduplicate: skip dates already in BigQuery                              ║
║  ├─ append: → BigQuery streaming.daily_activity (WRITE_APPEND)               ║
║  └─ truncate: PostgreSQL table for next run                                  ║
║         │                                                                    ║
║         ▼                                                                    ║
║  dbt: stg_daily_activity (view) → mart_activity_trends (table)               ║
║         │                                                                    ║
║         ▼                                                                    ║
║  Looker Studio: Tile 2 — Daily Activity Trend (Line Chart)                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════╗
║  ORCHESTRATION & INFRASTRUCTURE                                              ║
║                                                                              ║
║  Kestra ──────── monthly_batch_flow.yml  (cron: 0 2 1 * *)                   ║
║                  daily_streaming_flow.yml (cron: 0 6 * * *)                  ║
║                                                                              ║
║  Terraform ───── GCS bucket (Spark temp)                                     ║
║                  BigQuery datasets: raw │ streaming │ dbt_marts              ║
║                  Service account with BQ Admin + Storage Admin               ║
║                                                                              ║
║  Docker Compose  Redpanda │ Flink (JM+TM) │ PostgreSQL │ Kestra              ║
║                                                                              ║
║  GitHub Actions  terraform fmt/validate │ dbt parse (CI on every push)       ║
╚══════════════════════════════════════════════════════════════════════════════╝

BigQuery Data Model:
┌─────────────────────────────────────┐   ┌──────────────────────────────────┐
│  raw.trials                         │   │  streaming.daily_activity        │
│  (Spark → partitioned + clustered)  │   │  (pg_to_bq → WRITE_APPEND)       │
│  nct_id │ therapeutic_area │ phase  │   │  window_start │ window_end       │
│  start_year (partition) │ ...       │   │  therapeutic_area │ update_count  │
└─────────────────────────────────────┘   └──────────────────────────────────┘
           │                                         │
           ▼  dbt staging (views)                    ▼
    stg_trials                               stg_daily_activity
           │                                         │
           ▼  dbt marts (partitioned tables)         ▼
    mart_trial_landscape                    mart_activity_trends
    (partitioned by start_year,             (one row per
     clustered by therapeutic_area+phase)    therapeutic_area × activity_date)
           │                                         │
           └──────────── Looker Studio ──────────────┘
                   Tile 1 (categorical)  Tile 2 (temporal)
```

---

## Technology Stack

| Layer | Technology | Role |
|-------|-----------|------|
| Infrastructure | **Terraform** | Provisions GCS bucket, 3 BigQuery datasets, service account + IAM |
| Batch ingestion | **Apache Spark** (PySpark, local mode) | Reads 3 AACT tables, joins, classifies, writes to BigQuery |
| Streaming broker | **Redpanda** (Kafka-compatible) | Durable topic buffer between producer and Flink |
| Stream processing | **PyFlink** | 1-day tumbling window aggregation; bounded mode (`latest-offset`) |
| Intermediate sink | **PostgreSQL** | Flink sink; avoids Flink-BigQuery connector JAR conflicts |
| BQ sync | **pg_to_bq.py** | Reads PostgreSQL → appends to BigQuery; deduplicates on re-runs |
| Data warehouse | **BigQuery** | Partitioned + clustered tables; hosts all three datasets |
| Transformations | **dbt** (dbt-bigquery) | 4 models: 2 staging views + 2 mart tables |
| Orchestration | **Kestra** | Monthly batch flow + daily streaming flow; runs inside Docker |
| Dashboard | **Looker Studio** | 2-tile report: batch mart + streaming mart |
| CI/CD | **GitHub Actions** | `terraform fmt -check` + validate; `dbt parse` (no GCP creds) on every push |
| Package manager | **uv** | Python environment management (local dev/backfill) |

---

## Dataset

**AACT** (Aggregate Analysis of ClinicalTrials.gov)
- Maintained by Duke Clinical Trials Transformation Initiative (CTTI)
- Daily pipe-delimited exports of all 375K+ registered trials
- Files used: `studies.txt` (one row per trial), `sponsors.txt` (filtered to lead sponsors), `browse_conditions.txt` (MeSH normalized condition terms)
- No authentication required; publicly available at https://aact.ctti-clinicaltrials.org

**ClinicalTrials.gov API v2**
- Public REST API at `https://clinicaltrials.gov/api/v2/studies`
- Filtered by `LastUpdatePostDate` to fetch real daily updates
- No authentication required; returns ~500–1,000 updates per day
- Granularity is DATE (not DATETIME) — daily is the finest meaningful resolution

---

## Data Warehouse Design

### Partitioning and Clustering

**`raw.trials`** (Spark write):
- **Partitioned by**: `start_year` (INT64) — partition pruning on year-range queries
- **Clustered by**: `therapeutic_area`, `overall_status` — co-locates rows for the most common filter combinations

**`dbt_marts.mart_trial_landscape`** (dbt):
- **Partitioned by**: `start_year` (INT64) — dashboard queries filter by year range; avoids full table scans
- **Clustered by**: `therapeutic_area`, `phase` — both are used as chart dimensions; clustering eliminates sorted scans

This means a query like `WHERE start_year BETWEEN 2015 AND 2024 AND therapeutic_area = 'Oncology'` reads only 10 year-partitions and scans only the Oncology cluster within each — BigQuery estimated scan drops from ~2 GB (full table) to ~80 MB.

**`streaming.daily_activity`** and **`dbt_marts.mart_activity_trends`**: not partitioned — the streaming table grows slowly (~365 rows/year per therapeutic area), and the mart aggregates to one row per `(therapeutic_area, activity_date)`, making full table scans cheap.

### dbt Models

```
dbt/benchmark/models/
├── staging/
│   ├── _sources.yml              ← declares raw.trials and streaming.daily_activity as sources
│   ├── stg_trials.sql            ← view: casts types, derives phase_numeric + status_category
│   └── stg_daily_activity.sql   ← view: adds industry_share + activity_date columns
└── marts/
    ├── mart_trial_landscape.sql  ← table: group by TA + year + phase, compute completion_rate
    └── mart_activity_trends.sql  ← table: group by TA + date, aggregate daily metrics
```

`stg_trials` adds two derived columns:
- `phase_numeric` — maps phase text to 1/2/3/4 for ordered visualization
- `status_category` — collapses 30+ status values into Active / Completed / Discontinued / Other

`mart_trial_landscape` computes:
- `trial_count`, `completion_rate` (% completed), `avg_enrollment`, `avg_duration_days`

`mart_activity_trends` computes:
- `daily_updates`, `industry_updates`, `avg_enrollment`, `industry_share` per therapeutic area per day

---

## Kestra Flows

Both Kestra flows run the pipeline inside the `benchmark-pipeline` Docker image (built by `make stream-up`), which bundles PySpark, dbt-bigquery, kafka-python, and all dependencies. GCP credentials are mounted from `/tmp/benchmark_sa_key.json` on the host — bypassing Kestra's Pebble template engine which corrupts RSA private key newlines when rendering secrets inline.

### monthly_batch (cron: `0 2 1 * *`)

| Task | Type | What it does |
|------|------|-------------|
| `download_and_spark` | Shell + Docker | Downloads AACT export for `trigger.date`, runs Spark enrich+load → `raw.trials` |
| `dbt_batch_run` | Shell + Docker | `dbt deps && dbt run` — materializes `stg_trials` + `mart_trial_landscape` |
| `dbt_test` | Shell + Docker | `dbt deps && dbt test` — validates not-null + unique constraints |

### daily_streaming (cron: `0 6 * * *`)

| Task | Type | What it does |
|------|------|-------------|
| `fetch_and_produce` | Docker Run | Calls CT.gov API for yesterday, produces events to Redpanda |
| `flink_pulse` | Shell + Process | `docker exec benchmark-jobmanager-1 flink run -py /opt/jobs/registration_pulse_job.py` |
| `sync_to_bq` | Shell + Docker | Reads PostgreSQL → deduplicates → appends to BigQuery `streaming.daily_activity` |
| `dbt_streaming_run` | Shell + Docker | `dbt deps && dbt run` — materializes `stg_daily_activity` + `mart_activity_trends` |

---

## Prerequisites

Install these on your host machine before starting.

| Tool | Minimum version | Install |
|------|----------------|---------|
| Docker + Docker Compose v2 | Latest | [docs.docker.com](https://docs.docker.com/get-docker/) |
| gcloud CLI | Any | [cloud.google.com/sdk](https://cloud.google.com/sdk/docs/install) |
| Terraform | >= 1.9 | [developer.hashicorp.com/terraform](https://developer.hashicorp.com/terraform/downloads) |
| Python | 3.12+ | [python.org](https://python.org) |
| Java JDK | 17 or 21 | `sudo apt install openjdk-21-jdk` |
| uv | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Verify:
```bash
docker compose version     # v2.x
gcloud --version
terraform version
java -version              # openjdk 17 or 21
uv --version
```

---

## Quick Start — Kestra-Orchestrated Setup

The pipeline is designed so that a reviewer can validate the full end-to-end flow through the Kestra UI with minimal manual steps. After the one-time infrastructure setup, both pipelines are triggered and monitored from Kestra.

### Step 1 — Clone the Repository

```bash
git clone https://github.com/syedshameersarwar/benchmark-clinical-trials.git
cd benchmark-clinical-trials
```

### Step 2 — Authenticate with GCP

```bash
gcloud auth login
gcloud auth application-default login

# Create or select a project (billing must be enabled)
export PROJECT_ID=your-gcp-project-id
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable bigquery.googleapis.com \
                       storage.googleapis.com \
                       iam.googleapis.com
```

### Step 3 — Provision GCP Infrastructure

```bash
export PROJECT_ID=$(gcloud config get-value project)
make infra
```

This runs `terraform apply` and writes `credentials/sa_key.json` to the repo root.

Verify:
```bash
gsutil ls gs://benchmark-$PROJECT_ID    # GCS bucket exists
bq ls --project_id=$PROJECT_ID          # Should list: raw, streaming, dbt_marts
```

### Step 4 — Configure Kestra Environment and dbt Profile

**Edit `kestra.env`** in the repo root and set your GCP project ID:

```bash
GCP_PROJECT_ID=your-gcp-project-id
```

This is the only variable required. Kestra flow tasks mount GCP credentials directly from `/tmp/benchmark_sa_key.json` (set in Step 5) — no secrets or additional env vars needed.

**Edit `dbt/benchmark/profiles.yml`** and set your GCP project ID. The Kestra Docker tasks use this file for BigQuery authentication:

```bash
cp dbt/benchmark/profiles.yml.example dbt/benchmark/profiles.yml
```

Then open `dbt/benchmark/profiles.yml` and replace the placeholder project:

```yaml
benchmark:
  target: prod
  outputs:
    prod:
      type: bigquery
      method: service-account
      project: your-gcp-project-id      # ← set this
      dataset: dbt_marts
      location: US
      keyfile: "{{ env_var('GOOGLE_APPLICATION_CREDENTIALS') }}"
      threads: 4
      timeout_seconds: 300
```

### Step 5 — Copy Service Account Key to /tmp

Kestra tasks mount credentials from `/tmp/benchmark_sa_key.json` on the host. This path has no symlinks or spaces, which is required for Docker volume mounts:

```bash
cp credentials/sa_key.json /tmp/benchmark_sa_key.json
```

### Step 6 — Start All Services and Upload Kestra Flows

```bash
make stream-up
```

This single command:
1. Builds the `benchmark-pipeline` Docker image (PySpark + dbt + Kafka deps)
2. Starts Docker Compose with all 5 services: Redpanda, PostgreSQL, Flink JobManager, Flink TaskManager, Kestra
3. Waits for Kestra to become healthy
4. Uploads `monthly_batch_flow.yml` and `daily_streaming_flow.yml` via Kestra REST API

Services after startup:

| Service | Port | UI |
|---------|------|----|
| Kestra | 8080 | http://localhost:8080 |
| Flink JobManager | 8081 | http://localhost:8081 |
| PostgreSQL | 5433 | `psql -h localhost -p 5433 -U postgres benchmark` |
| Redpanda (Kafka) | 9092 | — |

Verify:
```bash
# All 5 containers running
docker ps --filter name=benchmark

# Flows uploaded (should show monthly_batch and daily_streaming)
curl -s -u admin@benchmark.io:Admin1234! http://localhost:8080/api/v1/flows/benchmark \
  | python3 -m json.tool | grep '"id"'
```

### Step 7 — Access the Kestra UI

Open **http://localhost:8080** in your browser. You will be presented with a login screen.

**Credentials:**
- **Email**: `admin@benchmark.io`
- **Password**: `Admin1234!`

After logging in, navigate as follows:

- Left sidebar → **Flows** icon (workflow diagram icon)
- In the namespace filter, select or type **benchmark**
- You should see two flows listed:
  - **`monthly_batch`** — the batch pipeline (scheduled 1st of every month, 02:00 UTC)
  - **`daily_streaming`** — the streaming pipeline (scheduled daily at 06:00 UTC)

Click any flow name to open its detail page, which shows the flow's task graph, recent executions, triggers, and logs.

### Step 8 — Run the Batch Pipeline via Kestra Backfill

Rather than waiting for the 1st-of-month cron, use Kestra's **Backfill** feature to trigger the `monthly_batch` flow for the 1st of the current month:

1. In the Kestra UI, navigate to **Flows → benchmark → monthly_batch**
2. Click the **Triggers** tab (top of the flow detail page)
3. Next to **`monthly_schedule`**, click **Backfill executions**
4. Set the date window:
   - **Start date**: last day of the previous month — e.g. `2026-03-31T00:00:00`
   - **End date**: 2nd of the current month — e.g. `2026-04-02T00:00:00`
5. Click **Execute backfill**

Kestra evaluates the cron `0 2 1 * *` within this window and fires exactly one execution for April 1, 2026 at 02:00 UTC, passing that date as `trigger.date` to the flow (used by the AACT download script).

The flow runs three tasks sequentially:
- `download_and_spark` — downloads the AACT export for the trigger date, then runs the Spark enrichment job (~15–20 min)
- `dbt_batch_run` — materializes `stg_trials` and `mart_trial_landscape` (~2 min)
- `dbt_test` — runs data quality tests (~1 min)

Monitor progress in the **Gantt** or **Logs** tab of the execution. All three tasks turn green on success.

Verify the output in BigQuery:
```sql
-- Row count and therapeutic area distribution
SELECT therapeutic_area, COUNT(*) AS trial_count
FROM `your_project.raw.trials`
GROUP BY 1
ORDER BY 2 DESC;

-- dbt mart materialized and partitioned
SELECT therapeutic_area, start_year, SUM(trial_count) AS n
FROM `your_project.dbt_marts.mart_trial_landscape`
GROUP BY 1, 2
ORDER BY 2 DESC, 3 DESC
LIMIT 20;
```

### Step 9 — Backfill Streaming Data via Kestra

Use Kestra's Backfill feature to populate the streaming pipeline for all days from the 2nd of the current month to today. This gives the temporal dashboard tile enough history to show a meaningful trend.

1. In the Kestra UI, navigate to **Flows → benchmark → daily_streaming**
2. Click the **Triggers** tab
3. Next to **`daily_schedule`**, click **Backfill executions**
4. Set the date window:
   - **Start date**: 2nd of the current month — e.g. `2026-04-02T00:00:00`
   - **End date**: today's date — e.g. `2026-04-19T00:00:00`
5. Click **Execute backfill**

Kestra evaluates the cron `0 6 * * *` within this window and fires one execution per day — April 2 through April 18 in the example above (17 runs total). Each execution:
- Fetches real CT.gov API updates for its `trigger.date` minus one day (`yesterday` variable)
- Produces events to Redpanda
- Runs the PyFlink 1-day tumbling window job
- Syncs PostgreSQL → BigQuery (with deduplication — re-runs are safe)
- Runs dbt to refresh `mart_activity_trends`

Backfill executions run sequentially inside Kestra. Monitor them under **Executions** — each shows the date it processed and which tasks succeeded.

Verify streaming data in BigQuery:
```sql
SELECT activity_date, SUM(daily_updates) AS total
FROM `your_project.dbt_marts.mart_activity_trends`
GROUP BY 1
ORDER BY 1 DESC;
```

The daily flow automatically runs at 06:00 UTC each day thereafter, appending the previous day's CT.gov updates.

---

## Verification Checklist

After completing the Quick Start, confirm all components are working:

- [ ] `credentials/sa_key.json` exists (created by `make infra`)
- [ ] `/tmp/benchmark_sa_key.json` exists (copied in Step 5)
- [ ] All 5 Docker containers running: `docker ps --filter name=benchmark`
- [ ] Kestra UI accessible: http://localhost:8080 (login: `admin@benchmark.io` / `Admin1234!`)
- [ ] Both flows visible: **benchmark/monthly_batch** and **benchmark/daily_streaming**
- [ ] Flink UI accessible: http://localhost:8081
- [ ] `raw.trials` has ~375K rows in BigQuery
- [ ] `dbt_marts.mart_trial_landscape` is materialized (partitioned by start_year)
- [ ] `streaming.daily_activity` has rows for each backfilled date
- [ ] `dbt_marts.mart_activity_trends` has rows for each backfilled date

---

## Looker Studio Dashboard Setup

**Live dashboard:** [BenchMark — Looker Studio (view)](https://datastudio.google.com/s/kxcK7Cl4t2Q)

Use that link to open the published report as a viewer. The instructions below walk through recreating the same layout and charts in your own Looker Studio workspace (connected to your BigQuery project) if you want to customize or rebuild it from scratch.

### Prerequisites

Both pipelines must have run at least once:
- `raw.trials` and `dbt_marts.mart_trial_landscape` populated (Step 7)
- `dbt_marts.mart_activity_trends` populated (Step 8)

### Step 1 — Create a New Report

Go to [lookerstudio.google.com](https://lookerstudio.google.com) → **Create** → **Report**

When prompted to add a data source, click **Add data** → **BigQuery** → select your GCP project → dataset **dbt_marts** → table **mart_trial_landscape** → click **Add**.

This connects the first data source (Tile 1 / batch mart).

### Step 2 — Connect the Second Data Source

On the toolbar: **Resource** → **Manage added data sources** → **Add a data source** → **BigQuery** → select **dbt_marts** → **mart_activity_trends** → **Add**.

You now have two data sources connected to the report.

### Step 3 — Rename Fields for Human-Readable Labels

Looker Studio uses raw column names from BigQuery by default (e.g. `therapeutic_area`, `trial_count`). Renaming them here means every chart, axis label, legend entry, and filter control automatically shows the friendly name without any per-chart configuration.

**Rename fields in `mart_trial_landscape`:**

1. Toolbar → **Resource** → **Manage added data sources**
2. Next to `mart_trial_landscape`, click **Edit**
3. In the field list, click on each field name to rename it:

| Original field name | Rename to |
|---------------------|-----------|
| `therapeutic_area` | Therapeutic Area |
| `start_year` | Start Year |
| `phase` | Phase |
| `phase_numeric` | Phase (Numeric) |
| `sponsor_class` | Sponsor |
| `status_category` | Status |
| `trial_count` | Trial Count |
| `completion_rate` | Completion Rate |
| `avg_enrollment` | Avg Enrollment |
| `avg_duration_days` | Avg Duration (Days) |

4. Click **Done** to save, then **Close**

**Rename fields in `mart_activity_trends`:**

1. Back in **Manage added data sources**, click **Edit** next to `mart_activity_trends`
2. Rename:

| Original field name | Rename to |
|---------------------|-----------|
| `activity_date` | Activity Date |
| `therapeutic_area` | Therapeutic Area |
| `daily_updates` | Daily Updates |
| `industry_updates` | Industry Updates |
| `avg_enrollment` | Avg Enrollment |
| `industry_share` | Industry Share |

3. Click **Done** → **Close**

These renamed labels will appear automatically in all chart axes, legends, and filter control dropdowns.

### Step 4 — Build Tile 1: Clinical Trial Distribution (Stacked Bar Chart)

**Add the chart:**

1. Toolbar → **Insert** → **Bar chart** (horizontal bar icon)
2. Draw a rectangle on the canvas for the chart area
3. In the right panel, **Setup** tab, configure:

**Data source**: `mart_trial_landscape`

**Dimension (Y-axis)**: `Therapeutic Area`
- Click the dimension field → select `Therapeutic Area` (renamed in Step 3)

**Breakdown dimension**: `Phase`
- Click **"Add dimension"** under the breakdown section → select `Phase`
- This creates one stacked color segment per phase value

**Metric (X-axis)**: `Trial Count`
- Click the metric field → select `Trial Count` → aggregation: **Sum**

**Sort**: `Trial Count` — **Descending**
- In the Sort section, select `Trial Count`, toggle to **Descending**
- This orders therapeutic areas from most trials (top) to fewest (bottom)

**Add a static filter (baked into the chart):**

1. Right panel → **Setup** tab → scroll to bottom → **"Filters on this chart"** → **Add filter** → **Create a filter**
2. Name it `Start Year Updated`
3. Set a single clause: **Include** | `Start Year` | **Greater than or equal to (>=)** | `2010`
4. Click **Save**

This removes pre-2010 trials (sparse, noisy) from the chart. There is no upper bound — trials from any future year are automatically included.

**Set the chart title:**

1. Right panel → **Style** tab → scroll to **Chart title** → toggle **Show title** on
2. Type: `Clinical Trial Distribution by Therapeutic Area (2010–Present)`

**Axes configuration (Style tab):**
- **Show axes**: ON
- **Reverse Y axis direction**: OFF
- **Reverse X axis direction**: OFF
- **Align both axes to 0**: ON
- **Show Y axis labels**: ON
- **Show X axis labels**: ON

**Legend (Style tab):**
- **Display legend**: ON
- **Position**: Top
- **Alignment**: Left

**Bar chart layout (Style tab):**
- **Stacked bars**: ON — absolute counts stacked (not 100%)
- **Grouped bars**: OFF

### Step 5 — Add Tile 1 Interactive Filter Controls

Place these two controls above the bar chart on the canvas. Because both controls use the `mart_trial_landscape` data source, they automatically filter Tile 1 without any additional linking.

**Start Year dropdown:**

1. Toolbar → **Add a control** → **Drop-down list**
2. Right panel → **Control field**: `Start Year`
3. **Metric**: `Trial Count` (Sum)
4. **Sort**: `Start Year (Aggregation: MAX)` — **Descending** (most recent years appear at the top of the dropdown)
5. **Default selection**: Auto (no pre-selection — all years shown by default)

**Sponsor dropdown:**

1. Toolbar → **Add a control** → **Drop-down list**
2. Right panel → **Control field**: `Sponsor`
3. **Metric**: `Trial Count` (Sum)
4. **Sort**: `Trial Count` — **Descending** (most common sponsor class at top)
5. **Default selection**: Auto

Both controls automatically affect only Tile 1 because Tile 2 uses `mart_activity_trends` — a different data source.

### Step 6 — Build Tile 2: Daily CT.gov Activity Trend (Line Chart)

**Add the chart:**

1. Toolbar → **Insert** → **Time series chart** (or **Line chart**)
2. Draw a rectangle below the bar chart for the line chart area
3. Right panel → **Setup** tab, configure:

**Data source**: `mart_activity_trends`
- Click the data source selector → switch to `mart_activity_trends`

**Dimension (X-axis)**: `Activity Date`
- Click the dimension field → select `Activity Date` (renamed in Step 3)
- Looker Studio automatically sets the field type to Date
- **Date range dimension**: `Activity Date` (auto-set)

**Breakdown dimension**: `Therapeutic Area`
- Click **"Add dimension"** → select `Therapeutic Area`
- This creates one line per therapeutic area (up to the series limit)

**Metric (Y-axis)**: `Daily Updates`
- Click the metric field → select `Daily Updates` → aggregation: **Sum**

**Sort**: `Daily Updates` — **Descending**

**Default date range (Setup tab):**
- **Default date range filter**: Custom → **This month to date**
- This scopes the chart to the current calendar month by default (matching the date range control below)

**Set the chart title (Style tab):**
1. Toggle **Show title** on
2. Type: `Daily CT.gov Trial Update Activity by Therapeutic Area`

**Series settings (Style tab):**
- **Number of Series**: 7 (covers all 9 therapeutic areas with the remainder grouped)
- **Group the rest as "Others"**: ON (rolls up remaining areas into a single grey line)
- **Missing data**: `Line to Zero` (fills gaps when a therapeutic area has no updates on a given day, preventing broken lines)
- **Smooth**: ON (smooths jagged daily fluctuations for cleaner trend reading)
- **Series type**: Line

**Left Y Axis (Style tab):**
- **Show axis title**: ON — title text: `Daily Updates`
- **Show axis labels**: ON
- **Axis min**: 0

**Bottom X Axis (Style tab):**
- **Show axis**: ON
- **Show axis labels**: ON

### Step 7 — Add Tile 2 Interactive Filter Controls

**Date range control:**

1. Toolbar → **Add a control** → **Date range**
2. Right panel → **Default date range**: Custom → **This month to date**
3. The control automatically links to Tile 2 because `Activity Date` is a recognized Date dimension

This control does NOT affect Tile 1 because `Start Year` is an integer (INT64), not a Date type — correct behavior.

### Step 8 — Final Layout and Sharing

Arrange the report canvas:

```
┌─────────────────────────────────────────────────────────────────┐
│  BenchMark: Clinical Trial Research Intelligence Platform        │
├─────────────────────────────────────────────────────────────────┤
│  [Start Year ▼]   [Sponsor ▼]                                   │
│                                                                  │
│  Clinical Trial Distribution by Therapeutic Area (2010–Present) │
│  ████████████████████████████████ Oncology                      │
│  ████████████████████ Cardiovascular                            │
│  ████████████████ Neurology          (stacked by phase)         │
│  ...                                                            │
├─────────────────────────────────────────────────────────────────┤
│  [📅 This month to date ▼]                                      │
│                                                                  │
│  Daily CT.gov Trial Update Activity by Therapeutic Area          │
│   800 ┤ ╭──╮                                                    │
│   400 ┤╭╯  ╰─────────╮    (one line per area)                  │
│     0 └──────────────────────────────────────────────           │
│       Apr 2          Apr 10          Apr 19                      │
└─────────────────────────────────────────────────────────────────┘
```

**Share the dashboard:**
1. Click **Share** → **Manage access**
2. Set to **Anyone with the link can view**
3. Copy the link and document it in your README (for example next to **Live dashboard** under [Looker Studio Dashboard Setup](#looker-studio-dashboard-setup))

### Dashboard Reference

A PDF export of the fully configured dashboard (both tiles, all filters applied, with data populated) is included in this repository:

**[benchmark-clinical-trials.pdf](benchmark-clinical-trials.pdf)**

Open this file to see exactly how the report should look after following the setup above — including the stacked bar chart for Tile 1 and the multi-line time series for Tile 2.

---

## CI/CD

GitHub Actions runs on every push to `main` and every pull request:

```yaml
Jobs:
  terraform-validate:
    - terraform init -backend=false
    - terraform fmt -check
    - terraform validate
    (confirms formatting, provider configuration, and HCL syntax)

  dbt-parse:
    - pip install dbt-bigquery
    - write stub profiles.yml (placeholder project; not used for auth during parse)
    - dbt deps && dbt parse
    (loads the full project graph and manifest without querying BigQuery)
```

`dbt compile` talks to the warehouse for some adapters; `dbt parse` only parses project files and is the right check for a credential-free runner. `GCP_PROJECT_ID` is set so `{{ env_var('GCP_PROJECT_ID') }}` in sources resolves during parsing.

---

## Running Components Manually (Development)

For local development and debugging, each pipeline component can be run independently:

```bash
# Provision infrastructure
make infra

# Download AACT data only
make data-download

# Run Spark job + dbt batch models (requires JAVA_HOME set)
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
make batch

# Start streaming services
make stream-up

# Backfill 14 days of streaming history
make stream-backfill

# Re-run dbt batch models only
make dbt-batch

# Re-run dbt streaming models only
make dbt-streaming

# Run all dbt models + tests
make dbt

# Re-upload Kestra flows after editing
make kestra-upload

# Tear down (keeps GCP resources)
make clean

# Destroy GCP resources
terraform -chdir=infra destroy -var="project_id=$PROJECT_ID"
```

**Trigger Kestra flows via curl:**
```bash
# Trigger batch flow
curl -X POST http://localhost:8080/api/v1/executions/benchmark/monthly_batch \
  -u admin@benchmark.io:Admin1234!

# Trigger streaming flow (yesterday's date auto-computed)
curl -X POST http://localhost:8080/api/v1/executions/benchmark/daily_streaming \
  -u admin@benchmark.io:Admin1234! \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

## Common Issues

**AACT download fails for today's date**
The AACT export for today may not be published yet (published ~08:00 UTC). The script falls back automatically to `2026-04-12`. Check https://aact.ctti-clinicaltrials.org/snapshots for available dates.

**Spark job fails with "Java not found" or OOM**
Ensure `JAVA_HOME` is set: `export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64`. For OOM, add `--driver-memory 4g` to the spark-submit command in the Makefile.

**Kestra task fails with "FileNotFoundError: /tmp/sa_key.json"**
The `/tmp/benchmark_sa_key.json` file on the host must exist. Re-run: `cp credentials/sa_key.json /tmp/benchmark_sa_key.json`

**Flink job exits immediately with no rows in PostgreSQL**
This is expected behavior — `scan.bounded.mode = 'latest-offset'` means the job processes all queued messages and stops cleanly. Verify the producer ran first: `docker exec benchmark-redpanda-1 rpk topic describe trial-updates`

**Container name not found for `docker exec`**
The Makefile starts Compose with `-p benchmark` so container names follow the pattern `benchmark-jobmanager-1`, `benchmark-kestra-1`, etc. If started without the project flag, stop and restart: `make clean && make stream-up`

**dbt test fails with "0 packages installed"**
Each `docker run` is a fresh container — `dbt deps` output does not persist between tasks. The Kestra flows already include `dbt deps &&` before every dbt command. If running manually, always run `dbt deps` first.

**Kestra flow fails with `GCP_PROJECT_ID` not set**
Ensure `kestra.env` in the repo root contains `GCP_PROJECT_ID=your-gcp-project-id` and that `make stream-up` was run after editing it (Kestra reads this file on startup).

---

## Known Limitations

1. **Therapeutic area classification is keyword-based (~85% coverage)**: Uses MeSH term regex matching. ~15% of trials fall into "Other". A production system would traverse the full MeSH tree hierarchy from NLM.

2. **Spark runs in local mode** (`local[*]`): Uses all cores on the host machine. Production would use Dataproc (GCP) or EMR (AWS) for distributed processing.

3. **Streaming granularity is daily**: The CT.gov API filters by `LastUpdatePostDate` which is a DATE field (not DATETIME). Daily is the finest meaningful granularity available from this source.

4. **PostgreSQL as an intermediate Flink sink**: Avoids JAR version conflicts between the Flink BigQuery connector and the Kafka connector. The `pg_to_bq.py` sync script handles the final append to BigQuery.

5. **Kestra runs Spark inside a Docker container**: The `benchmark-pipeline` image has PySpark installed. This avoids requiring Java/Spark on the reviewer's host machine for the Kestra-triggered batch flow.

