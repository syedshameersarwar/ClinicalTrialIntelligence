#!/bin/bash
set -e

mkdir -p data/aact

# Use provided date argument ($1); fall back to today if not supplied
EXPORT_DATE="${1:-$(date +%Y-%m-%d)}"
FALLBACK_DATE="2026-04-12"

echo "Downloading AACT export for ${EXPORT_DATE}..."

wget -q --show-progress -O data/aact/export.zip \
  "https://aact.ctti-clinicaltrials.org/static/exported_files/daily/${EXPORT_DATE}?source=web" \
  || {
    echo "Export for ${EXPORT_DATE} not available, falling back to ${FALLBACK_DATE}..."
    wget -q --show-progress -O data/aact/export.zip \
      "https://aact.ctti-clinicaltrials.org/static/exported_files/daily/${FALLBACK_DATE}?source=web"
  }

echo "Unzipping..."
unzip -o data/aact/export.zip -d data/aact/

echo "Done. Key files:"
ls -lh data/aact/studies.txt data/aact/sponsors.txt data/aact/browse_conditions.txt 2>/dev/null \
  || echo "WARNING: expected files not found — check the unzipped contents:"
ls data/aact/
