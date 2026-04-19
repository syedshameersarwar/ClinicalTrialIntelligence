"""
BenchMark Kafka Producer: Fetch CT.gov trial updates for a given date → emit to Kafka.

Calls ClinicalTrials.gov API v2 for trials with LastUpdatePostDate = --date.
Applies the same therapeutic area keyword classification as the Spark batch job.
Emits each trial as a JSON message to the 'trial-updates' topic.

Usage:
  python producer.py --date 2026-04-11
  python producer.py --date 2026-04-11 --bootstrap-servers localhost:9092
"""
import argparse
import json
import re
import time
from dataclasses import dataclass, asdict

import requests
from kafka import KafkaProducer

TOPIC      = "trial-updates"
CT_GOV_API = "https://clinicaltrials.gov/api/v2/studies"

# Same keyword rules as Spark batch job — keeps therapeutic_area consistent
# across the batch pipeline (MeSH-based) and streaming pipeline (condition text).
TA_RULES = {
    "Oncology":               r"neoplasm|carcinoma|cancer|tumor|lymphoma|leukemia|melanoma|sarcoma|glioma",
    "Cardiovascular":         r"heart|cardiac|coronary|hypertension|stroke|vascular|artery|atrial",
    "Neurology":              r"alzheimer|parkinson|epilepsy|multiple sclerosis|neuropathy|dementia|brain",
    "Respiratory":            r"asthma|copd|pulmonary|respiratory|lung disease|pneumonia|bronchial",
    "Endocrine/Metabolic":    r"diabetes|insulin|obesity|metabolic syndrome|thyroid|endocrine|hyperglycemia",
    "Infectious Disease":     r"hiv|aids|infection|bacterial|viral|sepsis|hepatitis|tuberculosis|covid",
    "Immunology/Rheumatology":r"arthritis|lupus|autoimmune|rheumatoid|inflammatory bowel|psoriasis",
    "Psychiatry":             r"depression|anxiety|schizophrenia|bipolar|psychiatric|mental disorder",
    "Gastroenterology":       r"crohn|colitis|gastric|intestinal|bowel|liver|hepatic|colorectal",
}


def classify_ta(conditions: list[str]) -> str:
    """Classify a list of condition strings into a therapeutic area."""
    if not conditions:
        return "Other"
    text = " ".join(conditions).lower()
    for ta, pattern in TA_RULES.items():
        if re.search(pattern, text):
            return ta
    return "Other"


@dataclass
class TrialUpdate:
    nct_id:           str
    overall_status:   str
    phase:            str
    therapeutic_area: str
    sponsor_class:    str
    enrollment:       int
    event_date:       str   # YYYY-MM-DD, used as event_time in PyFlink
    lead_sponsor:     str


def fetch_updates(date: str) -> list[TrialUpdate]:
    """Fetch all CT.gov trials with LastUpdatePostDate == date."""
    records, token = [], None
    page = 0

    while True:
        params = {
            "query.term": f"AREA[LastUpdatePostDate]RANGE[{date},{date}]",
            "pageSize": 1000,
        }
        if token:
            params["pageToken"] = token

        resp = requests.get(CT_GOV_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        studies = data.get("studies", [])
        for s in studies:
            ps = s.get("protocolSection", {})
            conditions = ps.get("conditionsModule", {}).get("conditions") or []
            records.append(TrialUpdate(
                nct_id=ps.get("identificationModule", {}).get("nctId", ""),
                overall_status=ps.get("statusModule", {}).get("overallStatus", "UNKNOWN"),
                phase=(ps.get("designModule", {}).get("phases") or ["N/A"])[0],
                therapeutic_area=classify_ta(conditions),
                sponsor_class=ps.get("sponsorCollaboratorsModule", {}).get(
                                   "leadSponsor", {}).get("class", "Other"),
                enrollment=ps.get("designModule", {}).get(
                               "enrollmentInfo", {}).get("count") or 0,
                event_date=date,
                lead_sponsor=ps.get("sponsorCollaboratorsModule", {}).get(
                                  "leadSponsor", {}).get("name", ""),
            ))

        token = data.get("nextPageToken")
        page += 1
        print(f"\r  Fetched {len(records):,} records (page {page})...", end="", flush=True)

        if not token:
            break
        time.sleep(0.1)  # respect ~10 req/sec rate limit

    print()  # newline after progress output
    return records


def main():
    parser = argparse.ArgumentParser(description="Produce CT.gov trial updates to Kafka.")
    parser.add_argument("--date", required=True,
                        help="Date of CT.gov updates to fetch (YYYY-MM-DD). "
                             "Pass yesterday's date for daily production runs.")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    args = parser.parse_args()

    print(f"Fetching CT.gov updates for {args.date}...")
    records = fetch_updates(args.date)
    print(f"Fetched {len(records):,} trial update events.")

    if not records:
        print("No updates found for this date. Exiting without producing.")
        return

    producer = KafkaProducer(
        bootstrap_servers=args.bootstrap_servers,
        value_serializer=lambda x: json.dumps(asdict(x)).encode("utf-8"),
    )

    for msg in records:
        producer.send(TOPIC, value=msg)

    producer.flush()
    producer.close()
    print(f"Produced {len(records):,} events to topic '{TOPIC}'.")


if __name__ == "__main__":
    main()
