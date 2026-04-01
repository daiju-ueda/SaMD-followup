"""FastAPI route definitions.

All endpoints are defined here. In production, these would connect to
a database via SQLAlchemy/asyncpg. For the MVP scaffold, we show the
route structure and response shapes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query

from src.api.schemas import (
    IngestionStatus,
    PaginatedResponse,
    ProductDetail,
    ProductListItem,
    ProductListResponse,
    ProductPapersResponse,
    ReviewDecision,
    ReviewQueueItem,
    SystemStats,
)

app = FastAPI(
    title="SaMD Evidence Tracker",
    description="Product-centric SaMD regulatory & literature database for US, JP, EU",
    version="0.1.0",
)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Product endpoints
# ---------------------------------------------------------------------------

@router.get("/products", response_model=ProductListResponse)
async def list_products(
    region: Optional[str] = Query(None, description="Filter by region: us, jp, eu"),
    disease_area: Optional[str] = Query(None),
    modality: Optional[str] = Query(None),
    manufacturer: Optional[str] = Query(None),
    regulatory_pathway: Optional[str] = Query(None),
    has_exact_evidence: Optional[bool] = Query(None),
    q: Optional[str] = Query(None, description="Free-text search"),
    sort_by: str = Query("name", description="Sort: name, date, evidence_count"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List SaMD products with filtering and pagination."""
    # TODO: implement DB query with filters
    return ProductListResponse(items=[], total=0, page=page, per_page=per_page, pages=0)


@router.get("/products/{product_id}", response_model=ProductDetail)
async def get_product(product_id: str):
    """Get detailed product information including regulatory entries."""
    # TODO: implement DB lookup
    raise HTTPException(status_code=404, detail="Product not found")


@router.get("/products/{product_id}/papers", response_model=ProductPapersResponse)
async def get_product_papers(
    product_id: str,
    include_indication_related: bool = Query(True, description="Include broad indication papers"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
):
    """Get papers linked to a product, grouped by link classification.

    Papers are returned in separate arrays:
    - exact_product: Papers that explicitly mention this product
    - product_family: Papers about the product family
    - manufacturer_linked: Papers linked via manufacturer + indication
    - indication_related: Broad indication papers (optional)
    """
    # TODO: implement
    raise HTTPException(status_code=404, detail="Product not found")


@router.get("/products/{product_id}/evidence-summary")
async def get_evidence_summary(product_id: str):
    """Get evidence summary for a product, including gap analysis."""
    # TODO: implement
    raise HTTPException(status_code=404, detail="Product not found")


# ---------------------------------------------------------------------------
# Paper endpoints
# ---------------------------------------------------------------------------

@router.get("/papers/{paper_id}")
async def get_paper(paper_id: str):
    """Get detailed paper information."""
    # TODO: implement
    raise HTTPException(status_code=404, detail="Paper not found")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search(
    q: str = Query(..., description="Search query"),
    type: str = Query("all", description="Search type: all, products, papers"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """Full-text search across products and papers."""
    # TODO: implement
    return {"results": [], "total": 0}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/api/v1/admin")


@admin_router.post("/ingest/{source}", response_model=IngestionStatus)
async def trigger_ingestion(
    source: str,
):
    """Trigger data ingestion for a specific source.

    Sources: fda_510k, fda_pma, fda_denovo, fda_aiml, pmda
    """
    valid_sources = {"fda_510k", "fda_pma", "fda_denovo", "fda_aiml", "pmda"}
    if source not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Invalid source. Must be one of: {valid_sources}")
    # TODO: trigger async ingestion job
    return IngestionStatus(source=source, status="started")


@admin_router.post("/search-papers/{product_id}")
async def trigger_paper_search(product_id: str):
    """Trigger literature search for a specific product."""
    # TODO: implement
    return {"status": "started", "product_id": product_id}


@admin_router.get("/review-queue", response_model=list[ReviewQueueItem])
async def get_review_queue(
    limit: int = Query(50, ge=1, le=200),
):
    """Get items pending human review, ordered by confidence score."""
    # TODO: implement
    return []


@admin_router.post("/review/{link_id}")
async def submit_review(link_id: str, decision: ReviewDecision):
    """Submit a human review decision for a product-paper link."""
    # TODO: implement
    return {"status": "ok", "link_id": link_id}


@admin_router.get("/stats", response_model=SystemStats)
async def get_stats():
    """Get system-wide statistics."""
    # TODO: implement
    return SystemStats()


# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------

app.include_router(router)
app.include_router(admin_router)
