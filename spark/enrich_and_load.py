"""
BenchMark Spark Job: Read AACT pipe-delimited files → join → classify → write to BigQuery.

Three AACT tables joined:
  studies.txt        — one row per trial: status, phase, dates, enrollment
  sponsors.txt       — filtered to lead sponsor per trial
  browse_conditions.txt — MeSH-normalized conditions, used for therapeutic area classification

Writes to BigQuery raw.trials partitioned by start_year, clustered by therapeutic_area.

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa_key.json \\
  python spark/enrich_and_load.py <AACT_DIR> <BQ_PROJECT>

  Requires: pyspark and JAVA_HOME set (JDK 17 or 21).
  Uses writeMethod=direct (BigQuery Storage Write API) — no GCS temp bucket needed.
  The BigQuery connector JAR is downloaded automatically via spark.jars.packages.
"""
import sys
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

AACT_DIR   = sys.argv[1]  # e.g. /path/to/data/aact
BQ_PROJECT = sys.argv[2]  # e.g. my-gcp-project

spark = SparkSession.builder \
    .appName("BenchMark-EnrichLoad") \
    .config("spark.jars.packages",
            "com.google.cloud.spark:spark-bigquery-with-dependencies_2.12:0.36.1") \
    .getOrCreate()

spark.sparkContext.setLogLevel("WARN")


def read_aact(filename):
    """Read a pipe-delimited AACT export file into a Spark DataFrame."""
    return spark.read \
        .option("sep", "|") \
        .option("header", "true") \
        .option("nullValue", "") \
        .option("multiLine", "true") \
        .option("escape", '"') \
        .csv(f"{AACT_DIR}/{filename}")


# ── 1. Studies — the spine of the dataset ────────────────────────────────────
print("[1/4] Reading studies.txt...")
studies = read_aact("studies.txt").select(
    "nct_id",
    "brief_title",
    "overall_status",
    "phase",
    "study_type",
    F.col("source").alias("sponsor_class"),       # Industry / NIH / Academic / Federal
    F.col("enrollment").cast("int"),
    F.to_date("start_date",              "yyyy-MM-dd").alias("start_date"),
    F.to_date("completion_date",         "yyyy-MM-dd").alias("completion_date"),
    F.to_date("study_first_posted_date", "yyyy-MM-dd").alias("first_posted_date"),
    F.year(F.to_date("start_date", "yyyy-MM-dd")).alias("start_year"),
    F.when(F.col("overall_status") == "Completed",      True).otherwise(False).alias("is_completed"),
    F.when(F.col("study_type")     == "Interventional", True).otherwise(False).alias("is_interventional"),
)

# ── 2. Lead sponsors — one per trial ─────────────────────────────────────────
# sponsors.txt has multiple rows per nct_id (lead + collaborators).
# Filter to lead_or_collaborator = 'lead', deduplicate to get exactly one per trial.
print("[2/4] Reading sponsors.txt...")
sponsors = read_aact("sponsors.txt") \
    .filter(F.col("lead_or_collaborator") == "lead") \
    .select("nct_id", F.col("name").alias("lead_sponsor_name")) \
    .dropDuplicates(["nct_id"])

# ── 3. Therapeutic area via MeSH keyword classification ──────────────────────
# browse_conditions.txt has MeSH-normalized terms, one row per condition per trial.
# We classify each condition row, then pick ONE primary therapeutic area per trial
# using a window function: prefer specific match over "Other", tiebreak alphabetically.
print("[3/4] Reading browse_conditions.txt and classifying therapeutic areas...")
browse = read_aact("browse_conditions.txt").select("nct_id", "downcase_mesh_term")

ta_classified = browse.withColumn(
    "therapeutic_area",
    F.when(F.col("downcase_mesh_term").rlike(
        r"neoplasm|carcinoma|cancer|tumor|lymphoma|leukemia|melanoma|sarcoma|glioma|malignant"),
        "Oncology")
    .when(F.col("downcase_mesh_term").rlike(
        r"heart|cardiac|coronary|hypertension|stroke|vascular|artery|atherosclerosis|atrial"),
        "Cardiovascular")
    .when(F.col("downcase_mesh_term").rlike(
        r"alzheimer|parkinson|epilepsy|multiple sclerosis|neuropathy|neurological|brain|dementia"),
        "Neurology")
    .when(F.col("downcase_mesh_term").rlike(
        r"asthma|copd|pulmonary|respiratory|lung disease|pneumonia|bronchial"),
        "Respiratory")
    .when(F.col("downcase_mesh_term").rlike(
        r"diabetes|insulin|obesity|metabolic syndrome|thyroid|endocrine|hyperglycemia"),
        "Endocrine/Metabolic")
    .when(F.col("downcase_mesh_term").rlike(
        r"hiv|aids|infection|bacterial|viral|sepsis|hepatitis|tuberculosis|covid"),
        "Infectious Disease")
    .when(F.col("downcase_mesh_term").rlike(
        r"arthritis|lupus|autoimmune|rheumatoid|inflammatory bowel|psoriasis|scleroderma"),
        "Immunology/Rheumatology")
    .when(F.col("downcase_mesh_term").rlike(
        r"depression|anxiety|schizophrenia|bipolar|psychiatric|mental disorder|addiction"),
        "Psychiatry")
    .when(F.col("downcase_mesh_term").rlike(
        r"crohn|colitis|gastric|intestinal|bowel|liver|hepatic|pancreatic|colorectal"),
        "Gastroenterology")
    .otherwise("Other")
)

# Window: rank each condition row within a trial.
# Sort key 1: specific matches (non-"Other") get 0, sink "Other" to bottom.
# Sort key 2: alphabetical tiebreak when multiple specific areas match.
# row_number() == 1 picks exactly ONE therapeutic area per trial.
w = Window.partitionBy("nct_id").orderBy(
    F.when(F.col("therapeutic_area") != "Other", 0).otherwise(1),
    F.col("downcase_mesh_term")
)
primary_ta = ta_classified \
    .withColumn("rn", F.row_number().over(w)) \
    .filter(F.col("rn") == 1) \
    .select("nct_id", "therapeutic_area")

# ── 4. Join, enrich, and write to BigQuery ────────────────────────────────────
print("[4/4] Joining and writing to BigQuery...")
enriched = studies \
    .join(sponsors,   "nct_id", "left") \
    .join(primary_ta, "nct_id", "left") \
    .withColumn(
        "therapeutic_area",
        F.coalesce(F.col("therapeutic_area"), F.lit("Other"))
    ) \
    .withColumn(
        "trial_duration_days",
        F.datediff(F.col("completion_date"), F.col("start_date"))
    )

enriched.write \
    .format("bigquery") \
    .mode("overwrite") \
    .option("table",             f"{BQ_PROJECT}:raw.trials") \
    .option("partitionField",    "start_year") \
    .option("clusteredFields",   "therapeutic_area,overall_status") \
    .option("createDisposition", "CREATE_IF_NEEDED") \
    .option("writeMethod",       "direct") \
    .save()

count = enriched.count()
print(f"\n[Done] Wrote {count:,} enriched trials → {BQ_PROJECT}:raw.trials")
print(f"       Partitioned by start_year, clustered by therapeutic_area + overall_status")
spark.stop()
