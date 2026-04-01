"""SaMD Evidence Tracker — DB Browser UI.

Lightweight FastAPI + Jinja2 server-rendered UI.
All DB access goes through src.db.repositories.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is on path for imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.db.connection import get_connection
from src.db.repositories import ProductRepository, PaperRepository, StatsRepository

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="SaMD Evidence Tracker")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    conn = get_connection()
    prod_repo = ProductRepository(conn)
    paper_repo = PaperRepository(conn)
    stats = StatsRepository(conn)

    ctx = {
        "total_products": prod_repo.count(),
        "by_region": stats.products_by_region(),
        "by_pathway": stats.products_by_pathway("us"),
        "total_papers": paper_repo.count(),
        "total_links": stats.link_count(),
        "by_classification": stats.links_by_classification(),
        "total_aliases": stats.alias_count(),
        "top_products": prod_repo.get_top_by_papers(20),
    }
    conn.close()
    return _render(request, "dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Product list
# ---------------------------------------------------------------------------

@app.get("/products", response_class=HTMLResponse)
async def product_list(
    request: Request,
    region: Optional[str] = Query(None),
    pathway: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort_by: str = Query("name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    conn = get_connection()
    repo = ProductRepository(conn)
    products, total = repo.list_products(region, pathway, q, sort_by, page, per_page)
    total_pages = (total + per_page - 1) // per_page
    conn.close()

    return _render(request, "products.html", {
        "products": products, "total": total,
        "page": page, "per_page": per_page, "total_pages": total_pages,
        "region": region or "", "pathway": pathway or "", "q": q or "",
    })


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------

@app.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: str):
    conn = get_connection()
    prod_repo = ProductRepository(conn)
    paper_repo = PaperRepository(conn)

    product = prod_repo.get_by_id(product_id)
    if not product:
        conn.close()
        return HTMLResponse("<h1>Product not found</h1>", status_code=404)

    reg_entries = prod_repo.get_regulatory_entries(product_id)
    aliases = prod_repo.get_aliases(product_id)
    papers = paper_repo.get_papers_for_product(product_id)

    exact = [p for p in papers if p["link_classification"] == "exact_product"]
    family = [p for p in papers if p["link_classification"] == "product_family"]
    mfg = [p for p in papers if p["link_classification"] == "manufacturer_linked"]
    indication = [p for p in papers if p["link_classification"] == "indication_related"]

    conn.close()
    return _render(request, "product_detail.html", {
        "product": product, "reg_entries": reg_entries, "aliases": aliases,
        "exact_papers": exact, "family_papers": family,
        "mfg_papers": mfg, "indication_papers": indication,
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
    conn = get_connection()
    repo = PaperRepository(conn)
    papers, total = repo.list_papers(q, page, per_page)
    total_pages = (total + per_page - 1) // per_page
    conn.close()

    return _render(request, "papers.html", {
        "papers": papers, "total": total,
        "page": page, "per_page": per_page, "total_pages": total_pages,
        "q": q or "",
    })


# ---------------------------------------------------------------------------
# SQL Console (read-only, admin/debug)
# ---------------------------------------------------------------------------

@app.get("/sql", response_class=HTMLResponse)
async def sql_console(request: Request, query: Optional[str] = Query(None)):
    results = columns = error = None
    row_count = 0

    if query:
        try:
            conn = get_connection()
            stats = StatsRepository(conn)
            results, columns, row_count = stats.execute_readonly(query)
            conn.close()
        except (ValueError, Exception) as e:
            error = str(e)

    return _render(request, "sql.html", {
        "query": query or "", "results": results,
        "columns": columns, "error": error, "row_count": row_count,
    })
