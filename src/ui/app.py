"""SaMD Evidence Tracker — DB Browser UI.

Lightweight FastAPI + Jinja2 server-rendered UI for browsing
products, regulatory entries, and linked papers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

DB_DSN = os.environ.get("SAMD_DB_DSN", "dbname=samd_evidence")
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="SaMD Evidence Tracker")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS cnt FROM products")
    total_products = cur.fetchone()["cnt"]

    cur.execute("SELECT region, COUNT(*) AS cnt FROM product_regulatory_entries GROUP BY region ORDER BY cnt DESC")
    by_region = cur.fetchall()

    cur.execute("SELECT regulatory_pathway, COUNT(*) AS cnt FROM product_regulatory_entries WHERE region='us' GROUP BY regulatory_pathway ORDER BY cnt DESC")
    by_pathway = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS cnt FROM papers")
    total_papers = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) AS cnt FROM product_paper_links")
    total_links = cur.fetchone()["cnt"]

    cur.execute("SELECT link_classification, COUNT(*) AS cnt FROM product_paper_links GROUP BY link_classification ORDER BY cnt DESC")
    by_classification = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS cnt FROM product_aliases")
    total_aliases = cur.fetchone()["cnt"]

    # Products with most papers
    cur.execute("""
        SELECT p.canonical_name, p.manufacturer_name, p.product_id,
               COUNT(ppl.link_id) AS paper_count
        FROM products p
        JOIN product_paper_links ppl ON p.product_id = ppl.product_id
        GROUP BY p.product_id
        ORDER BY paper_count DESC
        LIMIT 20
    """)
    top_products = cur.fetchall()

    conn.close()
    return templates.TemplateResponse(request, "dashboard.html", {
        "total_products": total_products,
        "by_region": by_region,
        "by_pathway": by_pathway,
        "total_papers": total_papers,
        "total_links": total_links,
        "by_classification": by_classification,
        "total_aliases": total_aliases,
        "top_products": top_products,
    })


# ---------------------------------------------------------------------------
# Product list
# ---------------------------------------------------------------------------

@app.get("/products", response_class=HTMLResponse)
async def product_list(
    request: Request,
    region: Optional[str] = Query(None),
    pathway: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    conditions = []
    params = []

    if region:
        conditions.append("pre.region = %s")
        params.append(region)
    if pathway:
        conditions.append("pre.regulatory_pathway = %s")
        params.append(pathway)
    if q:
        conditions.append("(p.canonical_name ILIKE %s OR p.manufacturer_name ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * per_page

    cur.execute(f"""
        SELECT p.product_id, p.canonical_name, p.manufacturer_name,
               p.disease_area, p.modality,
               pre.region, pre.regulatory_pathway, pre.regulatory_status,
               pre.regulatory_id, pre.clearance_date,
               (SELECT COUNT(*) FROM product_paper_links ppl WHERE ppl.product_id = p.product_id) AS paper_count
        FROM products p
        LEFT JOIN product_regulatory_entries pre ON p.product_id = pre.product_id
        {where}
        ORDER BY p.canonical_name
        LIMIT %s OFFSET %s
    """, params + [per_page, offset])
    products = cur.fetchall()

    cur.execute(f"""
        SELECT COUNT(DISTINCT p.product_id)
        FROM products p
        LEFT JOIN product_regulatory_entries pre ON p.product_id = pre.product_id
        {where}
    """, params)
    total = cur.fetchone()["count"]

    total_pages = (total + per_page - 1) // per_page

    conn.close()
    return templates.TemplateResponse(request, "products.html", {
        "products": products,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "region": region or "",
        "pathway": pathway or "",
        "q": q or "",
    })


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------

@app.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM products WHERE product_id = %s", (product_id,))
    product = cur.fetchone()
    if not product:
        conn.close()
        return HTMLResponse("<h1>Product not found</h1>", status_code=404)

    cur.execute("""
        SELECT * FROM product_regulatory_entries
        WHERE product_id = %s ORDER BY clearance_date
    """, (product_id,))
    reg_entries = cur.fetchall()

    cur.execute("""
        SELECT * FROM product_aliases
        WHERE product_id = %s ORDER BY alias_type
    """, (product_id,))
    aliases = cur.fetchall()

    cur.execute("""
        SELECT pa.*, ppl.link_classification, ppl.confidence_score,
               ppl.matched_terms, ppl.rationale, ppl.human_review_needed
        FROM product_paper_links ppl
        JOIN papers pa ON pa.paper_id = ppl.paper_id
        WHERE ppl.product_id = %s
        ORDER BY ppl.link_classification, ppl.confidence_score DESC
    """, (product_id,))
    papers = cur.fetchall()

    exact_papers = [p for p in papers if p["link_classification"] == "exact_product"]
    family_papers = [p for p in papers if p["link_classification"] == "product_family"]
    mfg_papers = [p for p in papers if p["link_classification"] == "manufacturer_linked"]
    indication_papers = [p for p in papers if p["link_classification"] == "indication_related"]

    conn.close()
    return templates.TemplateResponse(request, "product_detail.html", {
        "product": product,
        "reg_entries": reg_entries,
        "aliases": aliases,
        "exact_papers": exact_papers,
        "family_papers": family_papers,
        "mfg_papers": mfg_papers,
        "indication_papers": indication_papers,
        "total_papers": len(papers),
    })


# ---------------------------------------------------------------------------
# Papers list
# ---------------------------------------------------------------------------

@app.get("/papers", response_class=HTMLResponse)
async def paper_list(
    request: Request,
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    conditions = []
    params = []
    if q:
        conditions.append("pa.title ILIKE %s")
        params.append(f"%{q}%")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * per_page

    cur.execute(f"""
        SELECT pa.paper_id, pa.title, pa.journal, pa.publication_year,
               pa.doi, pa.pmid, pa.citation_count,
               COUNT(ppl.link_id) AS linked_products
        FROM papers pa
        LEFT JOIN product_paper_links ppl ON pa.paper_id = ppl.paper_id
        {where}
        GROUP BY pa.paper_id
        ORDER BY pa.publication_year DESC NULLS LAST, pa.title
        LIMIT %s OFFSET %s
    """, params + [per_page, offset])
    papers = cur.fetchall()

    cur.execute(f"SELECT COUNT(*) FROM papers pa {where}", params)
    total = cur.fetchone()["count"]
    total_pages = (total + per_page - 1) // per_page

    conn.close()
    return templates.TemplateResponse(request, "papers.html", {
        "papers": papers,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "q": q or "",
    })


# ---------------------------------------------------------------------------
# SQL Console (read-only)
# ---------------------------------------------------------------------------

@app.get("/sql", response_class=HTMLResponse)
async def sql_console(request: Request, query: Optional[str] = Query(None)):
    results = None
    columns = None
    error = None
    row_count = 0

    if query:
        # Safety: only allow SELECT
        q_stripped = query.strip().upper()
        if not q_stripped.startswith("SELECT"):
            error = "Only SELECT queries are allowed."
        else:
            try:
                conn = get_db()
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(query)
                results = cur.fetchmany(500)
                row_count = cur.rowcount
                columns = [desc[0] for desc in cur.description] if cur.description else []
                conn.close()
            except Exception as e:
                error = str(e)

    return templates.TemplateResponse(request, "sql.html", {
        "query": query or "",
        "results": results,
        "columns": columns,
        "error": error,
        "row_count": row_count,
    })
