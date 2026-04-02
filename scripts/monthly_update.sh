#!/usr/bin/env bash
# Monthly SaMD Evidence Tracker update
# Runs: product ingestion → literature search → DB load → fulltext fetch
#
# Cron: 0 3 1 * * /srv/projects/SaMD-followup/scripts/monthly_update.sh
set -euo pipefail

PROJECT_DIR="/srv/projects/SaMD-followup"
PYTHON="/usr/bin/python3.14"
LOG_DIR="${PROJECT_DIR}/data/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="${LOG_DIR}/monthly_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

exec >> "$LOG" 2>&1
echo "=== Monthly update started: $(date) ==="

# Step 1: Download latest FDA AI/ML list
echo "--- Step 1: Downloading FDA AI/ML list ---"
curl -sL -o "${PROJECT_DIR}/ai-ml-enabled-devices.csv.new" \
    "https://www.fda.gov/media/178541/download?attachment" || true
if [ -s "${PROJECT_DIR}/ai-ml-enabled-devices.csv.new" ]; then
    mv "${PROJECT_DIR}/ai-ml-enabled-devices.csv.new" "${PROJECT_DIR}/ai-ml-enabled-devices.csv"
    echo "FDA CSV updated"
else
    rm -f "${PROJECT_DIR}/ai-ml-enabled-devices.csv.new"
    echo "FDA CSV download failed, using existing"
fi

# Step 2: Run pipeline (FDA + PMDA) in batches
echo "--- Step 2: Running pipeline ---"
BATCH_SIZE=300
FDA_COUNT=$($PYTHON -c "import csv; print(sum(1 for _ in csv.DictReader(open('ai-ml-enabled-devices.csv'))))")
echo "FDA products: $FDA_COUNT"

# PMDA: fetch from web (PMDA Excel lists), fallback to CSV
echo "--- PMDA: fetching from web ---"
$PYTHON scripts/run_pipeline.py --skip-fda --pmda-web --output data/pmda_results.json || \
    $PYTHON scripts/run_pipeline.py --skip-fda --output data/pmda_results.json

# FDA in batches
OFFSET=0
BATCH=1
while [ $OFFSET -lt $FDA_COUNT ]; do
    END=$((OFFSET + BATCH_SIZE))
    if [ $END -gt $FDA_COUNT ]; then
        END=$FDA_COUNT
    fi
    echo "FDA batch $BATCH: $OFFSET - $END"
    $PYTHON scripts/run_pipeline.py --skip-pmda --resume $OFFSET --max-products $END \
        --output "data/fda_results_b${BATCH}.json"
    OFFSET=$END
    BATCH=$((BATCH + 1))
done

# Merge FDA batches
echo "--- Step 3: Merging results ---"
$PYTHON -c "
import json, glob
all_results = []
for f in sorted(glob.glob('data/fda_results_b*.json')):
    all_results.extend(json.load(open(f)))
json.dump(all_results, open('data/pipeline_results.json','w'), indent=2, ensure_ascii=False, default=str)
print(f'Merged: {len(all_results)} products')
"

# Step 3: Reload DB
echo "--- Step 4: Reloading database ---"
psql -d samd_evidence -c "
TRUNCATE product_paper_links CASCADE;
TRUNCATE papers CASCADE;
TRUNCATE product_aliases CASCADE;
TRUNCATE product_regulatory_entries CASCADE;
TRUNCATE products CASCADE;
"
$PYTHON scripts/load_to_db.py

# Step 4: Fetch full text
echo "--- Step 5: Fetching full text ---"
$PYTHON scripts/fetch_fulltext.py

# Summary
echo "--- Summary ---"
psql -d samd_evidence -c "
SELECT
    (SELECT COUNT(*) FROM products) AS products,
    (SELECT COUNT(*) FROM papers) AS papers,
    (SELECT COUNT(*) FROM papers WHERE fulltext IS NOT NULL) AS with_fulltext,
    (SELECT COUNT(*) FROM papers WHERE doi IS NOT NULL) AS with_doi,
    (SELECT COUNT(*) FROM product_paper_links) AS links;
"

echo "=== Monthly update completed: $(date) ==="
