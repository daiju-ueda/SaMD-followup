#!/usr/bin/env bash
# Monthly SaMD Evidence Tracker update
#
# Data sources:
#   FDA  — openFDA REST API (no download, no bot detection)
#   PMDA — direct Excel download from pmda.go.jp
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

# Step 1: PMDA (web scraping from PMDA Excel, fallback to CSV)
echo "--- Step 1: PMDA ---"
$PYTHON scripts/run_pipeline.py --skip-fda --pmda-web \
    --output data/pmda_results.json || \
$PYTHON scripts/run_pipeline.py --skip-fda \
    --output data/pmda_results.json

# Step 2: FDA (openFDA API, fallback to local CSV)
echo "--- Step 2: FDA via openFDA API ---"
BATCH_SIZE=300
FDA_COUNT=$($PYTHON -c "
import asyncio, sys; sys.path.insert(0,'.')
from src.ingestion.fda_scraper import fetch_fda_aiml_products
products = asyncio.run(fetch_fda_aiml_products())
print(len(products))
" 2>/dev/null || echo "0")

if [ "$FDA_COUNT" -eq "0" ]; then
    echo "openFDA API failed, falling back to local CSV"
    if [ -f "ai-ml-enabled-devices.csv" ]; then
        FDA_COUNT=$($PYTHON -c "import csv; print(sum(1 for _ in csv.DictReader(open('ai-ml-enabled-devices.csv'))))")
        FDA_FLAG=""
    else
        echo "No FDA data available, skipping"
        FDA_COUNT=0
    fi
else
    echo "FDA products from API: $FDA_COUNT"
    FDA_FLAG="--fda-api"
fi

if [ "$FDA_COUNT" -gt "0" ]; then
    OFFSET=0
    BATCH=1
    while [ $OFFSET -lt $FDA_COUNT ]; do
        END=$((OFFSET + BATCH_SIZE))
        [ $END -gt $FDA_COUNT ] && END=$FDA_COUNT
        echo "FDA batch $BATCH: $OFFSET - $END"
        $PYTHON scripts/run_pipeline.py --skip-pmda ${FDA_FLAG:-} \
            --resume $OFFSET --max-products $END \
            --output "data/fda_results_b${BATCH}.json"
        OFFSET=$END
        BATCH=$((BATCH + 1))
    done

    # Merge batches
    echo "--- Merging FDA batches ---"
    $PYTHON -c "
import json, glob
all_results = []
for f in sorted(glob.glob('data/fda_results_b*.json')):
    all_results.extend(json.load(open(f)))
json.dump(all_results, open('data/pipeline_results.json','w'), indent=2, ensure_ascii=False, default=str)
print(f'Merged: {len(all_results)} products')
"
fi

# Step 3: Reload DB
echo "--- Step 3: Reloading database ---"
psql -d samd_evidence -c "
TRUNCATE product_paper_links CASCADE;
TRUNCATE papers CASCADE;
TRUNCATE product_aliases CASCADE;
TRUNCATE product_regulatory_entries CASCADE;
TRUNCATE products CASCADE;
"
$PYTHON scripts/load_to_db.py

# Step 4: Fetch full text
echo "--- Step 4: Fetching full text ---"
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
