#!/usr/bin/env bash
# Monthly SaMD Evidence Tracker update
# Fetches latest data from FDA + PMDA websites, runs literature search, loads DB.
#
# All downloads happen inside Python (no curl), so FDA/PMDA URL changes
# only need to be updated in src/ingestion/*_scraper.py.
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

# Step 1: PMDA (web scraping, fallback to CSV)
echo "--- Step 1: PMDA ingestion + literature search ---"
$PYTHON scripts/run_pipeline.py --skip-fda --pmda-web --output data/pmda_results.json || \
    $PYTHON scripts/run_pipeline.py --skip-fda --output data/pmda_results.json

# Step 2: FDA (web download, fallback to local CSV)
echo "--- Step 2: FDA ingestion + literature search ---"
# Get product count from web download
FDA_COUNT=$($PYTHON -c "
from src.ingestion.fda_scraper import fetch_fda_aiml_csv
import csv, io
text = fetch_fda_aiml_csv()
print(sum(1 for _ in csv.DictReader(io.StringIO(text))))
" 2>/dev/null || echo "0")

if [ "$FDA_COUNT" -eq "0" ]; then
    echo "FDA web download failed, using local CSV"
    FDA_COUNT=$($PYTHON -c "import csv; print(sum(1 for _ in csv.DictReader(open('ai-ml-enabled-devices.csv'))))")
    FDA_WEB_FLAG=""
else
    echo "FDA products from web: $FDA_COUNT"
    FDA_WEB_FLAG="--fda-web"
fi

BATCH_SIZE=300
OFFSET=0
BATCH=1
while [ $OFFSET -lt $FDA_COUNT ]; do
    END=$((OFFSET + BATCH_SIZE))
    if [ $END -gt $FDA_COUNT ]; then
        END=$FDA_COUNT
    fi
    echo "FDA batch $BATCH: $OFFSET - $END"
    $PYTHON scripts/run_pipeline.py --skip-pmda $FDA_WEB_FLAG \
        --resume $OFFSET --max-products $END \
        --output "data/fda_results_b${BATCH}.json"
    OFFSET=$END
    BATCH=$((BATCH + 1))
done

# Step 3: Merge FDA batches
echo "--- Step 3: Merging results ---"
$PYTHON -c "
import json, glob
all_results = []
for f in sorted(glob.glob('data/fda_results_b*.json')):
    all_results.extend(json.load(open(f)))
json.dump(all_results, open('data/pipeline_results.json','w'), indent=2, ensure_ascii=False, default=str)
print(f'Merged: {len(all_results)} products')
"

# Step 4: Reload DB
echo "--- Step 4: Reloading database ---"
psql -d samd_evidence -c "
TRUNCATE product_paper_links CASCADE;
TRUNCATE papers CASCADE;
TRUNCATE product_aliases CASCADE;
TRUNCATE product_regulatory_entries CASCADE;
TRUNCATE products CASCADE;
"
$PYTHON scripts/load_to_db.py

# Step 5: Fetch full text
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
