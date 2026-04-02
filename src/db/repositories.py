"""Database repositories — encapsulate all SQL queries.

Each repository takes a psycopg2 connection and provides typed query methods.
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg2.extras


class ProductRepository:
    """Queries for products, regulatory entries, and aliases."""

    def __init__(self, conn):
        self.conn = conn

    def _cur(self):
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def count(self) -> int:
        cur = self._cur()
        cur.execute("SELECT COUNT(*) AS cnt FROM products")
        return cur.fetchone()["cnt"]

    def list_products(
        self,
        region: Optional[str] = None,
        pathway: Optional[str] = None,
        q: Optional[str] = None,
        sort_by: str = "name",
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[dict], int]:
        """Return (products, total_count) with filtering and pagination."""
        cur = self._cur()
        conditions = []
        params: list[Any] = []

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

        order = {
            "name": "p.canonical_name",
            "date": "pre.clearance_date DESC NULLS LAST",
            "evidence_count": "paper_count DESC",
        }.get(sort_by, "p.canonical_name")

        cur.execute(f"""
            SELECT p.product_id, p.canonical_name, p.manufacturer_name,
                   p.disease_area, p.modality,
                   pre.region, pre.regulatory_pathway, pre.regulatory_status,
                   pre.regulatory_id, pre.clearance_date,
                   (SELECT COUNT(*) FROM product_paper_links ppl
                    WHERE ppl.product_id = p.product_id) AS paper_count
            FROM products p
            LEFT JOIN product_regulatory_entries pre ON p.product_id = pre.product_id
            {where}
            ORDER BY {order}
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

        return products, total

    def get_by_id(self, product_id: str) -> Optional[dict]:
        cur = self._cur()
        cur.execute("SELECT * FROM products WHERE product_id = %s", (product_id,))
        return cur.fetchone()

    def get_regulatory_entries(self, product_id: str) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT * FROM product_regulatory_entries
            WHERE product_id = %s ORDER BY clearance_date
        """, (product_id,))
        return cur.fetchall()

    def get_aliases(self, product_id: str) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT * FROM product_aliases
            WHERE product_id = %s ORDER BY alias_type
        """, (product_id,))
        return cur.fetchall()

    def get_top_by_papers(self, limit: int = 20) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT p.canonical_name, p.manufacturer_name, p.product_id,
                   COUNT(ppl.link_id) AS paper_count
            FROM products p
            JOIN product_paper_links ppl ON p.product_id = ppl.product_id
            GROUP BY p.product_id
            ORDER BY paper_count DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()

    def find_by_name_and_manufacturer(self, name: str, manufacturer: str) -> Optional[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT * FROM products
            WHERE canonical_name = %s AND manufacturer_name = %s
            LIMIT 1
        """, (name, manufacturer))
        return cur.fetchone()

    def upsert(self, product_id: str, canonical_name: str, manufacturer_name: str,
               intended_use: str = None, disease_area: str = None,
               modality: str = None) -> str:
        """Insert or find existing product. Returns the product_id."""
        existing = self.find_by_name_and_manufacturer(canonical_name, manufacturer_name)
        if existing:
            # Update metadata if new data is richer
            pid = str(existing["product_id"])
            self.update_metadata(pid, disease_area, modality)
            return pid

        cur = self._cur()
        cur.execute("""
            INSERT INTO products
                (product_id, canonical_name, manufacturer_name,
                 intended_use, disease_area, modality, standalone_samd)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        """, (product_id, canonical_name, manufacturer_name,
              intended_use, disease_area, modality))
        return product_id

    def upsert_regulatory_entry(self, product_id: str, region: str,
                                pathway: str, status_raw: str, status: str,
                                regulatory_id: str = None, clearance_date=None,
                                device_class: str = None, product_code: str = None,
                                review_panel: str = None, applicant: str = None) -> None:
        """Insert regulatory entry, skip if already exists for this product+region+reg_id."""
        cur = self._cur()
        # Check existing
        if regulatory_id:
            cur.execute("""
                SELECT 1 FROM product_regulatory_entries
                WHERE product_id = %s AND region = %s AND regulatory_id = %s
            """, (product_id, region, regulatory_id))
            if cur.fetchone():
                return

        cur.execute("""
            INSERT INTO product_regulatory_entries
                (product_id, region, regulatory_pathway, regulatory_status_raw,
                 regulatory_status, regulatory_id, clearance_date, device_class,
                 product_code, review_panel, applicant, evidence_tier)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'tier_1')
        """, (product_id, region, pathway, status_raw, status,
              regulatory_id, clearance_date, device_class, product_code,
              review_panel, applicant))

    def upsert_alias(self, product_id: str, alias_name: str,
                     alias_type: str = "trade_name", language: str = "en",
                     source: str = None) -> None:
        """Insert alias if it doesn't already exist for this product."""
        cur = self._cur()
        cur.execute("""
            SELECT 1 FROM product_aliases
            WHERE product_id = %s AND alias_name = %s
        """, (product_id, alias_name))
        if cur.fetchone():
            return
        cur.execute("""
            INSERT INTO product_aliases
                (product_id, alias_name, alias_type, language, source)
            VALUES (%s, %s, %s, %s, %s)
        """, (product_id, alias_name, alias_type, language, source))

    def update_metadata(self, product_id: str, disease_area: str = None,
                        modality: str = None) -> None:
        cur = self._cur()
        cur.execute("""
            UPDATE products SET
                disease_area = COALESCE(disease_area, %s),
                modality = COALESCE(modality, %s),
                updated_at = NOW()
            WHERE product_id = %s
        """, (disease_area, modality, product_id))


class PaperRepository:
    """Queries for papers and product-paper links."""

    def __init__(self, conn):
        self.conn = conn

    def _cur(self):
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def count(self) -> int:
        cur = self._cur()
        cur.execute("SELECT COUNT(*) AS cnt FROM papers")
        return cur.fetchone()["cnt"]

    def list_papers(
        self,
        q: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[dict], int]:
        cur = self._cur()
        conditions = []
        params: list[Any] = []
        if q:
            conditions.append("pa.title ILIKE %s")
            params.append(f"%{q}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        offset = (page - 1) * per_page

        cur.execute(f"""
            SELECT pa.paper_id, pa.title, pa.journal, pa.publication_year,
                   pa.doi, pa.pmid, pa.citation_count,
                   (pa.fulltext IS NOT NULL) AS has_fulltext,
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

        return papers, total

    def get_papers_for_product(self, product_id: str) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT pa.*, ppl.link_classification, ppl.confidence_score,
                   ppl.matched_terms, ppl.rationale, ppl.human_review_needed
            FROM product_paper_links ppl
            JOIN papers pa ON pa.paper_id = ppl.paper_id
            WHERE ppl.product_id = %s
            ORDER BY ppl.link_classification, ppl.confidence_score DESC
        """, (product_id,))
        return cur.fetchall()

    def get_by_id(self, paper_id: str) -> Optional[dict]:
        cur = self._cur()
        cur.execute("SELECT * FROM papers WHERE paper_id = %s", (paper_id,))
        return cur.fetchone()

    def get_linked_products(self, paper_id: str) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT p.product_id, p.canonical_name, p.manufacturer_name,
                   ppl.link_classification, ppl.confidence_score, ppl.matched_terms
            FROM product_paper_links ppl
            JOIN products p ON p.product_id = ppl.product_id
            WHERE ppl.paper_id = %s
            ORDER BY ppl.confidence_score DESC
        """, (paper_id,))
        return cur.fetchall()

    def find_by_doi(self, doi: str) -> Optional[dict]:
        if not doi:
            return None
        cur = self._cur()
        cur.execute("SELECT * FROM papers WHERE doi = %s", (doi,))
        return cur.fetchone()

    def find_by_pmid(self, pmid: str) -> Optional[dict]:
        if not pmid:
            return None
        cur = self._cur()
        cur.execute("SELECT * FROM papers WHERE pmid = %s", (pmid,))
        return cur.fetchone()

    def find_by_title(self, title: str) -> Optional[dict]:
        """Exact title match — fallback when no DOI/PMID."""
        cur = self._cur()
        cur.execute("SELECT * FROM papers WHERE title = %s", (title,))
        return cur.fetchone()

    def upsert(self, paper_id: str, title: str, doi: str = None,
               pmid: str = None, pmcid: str = None, openalex_id: str = None,
               journal: str = None, publication_year: int = None,
               is_open_access: bool = None, citation_count: int = None,
               source: str = None) -> str:
        """Insert or find existing paper. Returns the paper_id (existing or new)."""
        # Check for existing by DOI first, then PMID, then title
        existing = self.find_by_doi(doi) or self.find_by_pmid(pmid)
        if existing:
            return str(existing["paper_id"])

        # Check by exact title as last resort
        if not doi and not pmid:
            existing = self.find_by_title(title)
            if existing:
                return str(existing["paper_id"])

        cur = self._cur()
        cur.execute("""
            INSERT INTO papers
                (paper_id, title, doi, pmid, pmcid, openalex_id,
                 journal, publication_year, is_open_access, citation_count, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (paper_id, title, doi, pmid, pmcid, openalex_id,
              journal, publication_year, is_open_access, citation_count, source))
        return paper_id

    def insert_link(self, product_id: str, paper_id: str,
                    classification: str, confidence_score: float,
                    matched_terms: list[str] = None,
                    rationale: str = None) -> None:
        cur = self._cur()
        cur.execute("""
            INSERT INTO product_paper_links
                (product_id, paper_id, link_classification, confidence_score,
                 matched_terms, rationale)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (product_id, paper_id, classification, confidence_score,
              matched_terms, rationale))


class StatsRepository:
    """Aggregate statistics queries."""

    def __init__(self, conn):
        self.conn = conn

    def _cur(self):
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def products_by_region(self) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT region, COUNT(*) AS cnt
            FROM product_regulatory_entries GROUP BY region ORDER BY cnt DESC
        """)
        return cur.fetchall()

    def products_by_pathway(self, region: str = "us") -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT regulatory_pathway, COUNT(*) AS cnt
            FROM product_regulatory_entries WHERE region = %s
            GROUP BY regulatory_pathway ORDER BY cnt DESC
        """, (region,))
        return cur.fetchall()

    def links_by_classification(self) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT link_classification, COUNT(*) AS cnt
            FROM product_paper_links GROUP BY link_classification ORDER BY cnt DESC
        """)
        return cur.fetchall()

    def link_count(self) -> int:
        cur = self._cur()
        cur.execute("SELECT COUNT(*) AS cnt FROM product_paper_links")
        return cur.fetchone()["cnt"]

    def alias_count(self) -> int:
        cur = self._cur()
        cur.execute("SELECT COUNT(*) AS cnt FROM product_aliases")
        return cur.fetchone()["cnt"]

    def top_disease_areas(self, limit: int = 10) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT disease_area, COUNT(*) AS cnt
            FROM products WHERE disease_area IS NOT NULL AND disease_area != ''
            GROUP BY disease_area ORDER BY cnt DESC LIMIT %s
        """, (limit,))
        return cur.fetchall()

    def top_modalities(self, limit: int = 10) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT modality, COUNT(*) AS cnt
            FROM products WHERE modality IS NOT NULL AND modality != ''
            GROUP BY modality ORDER BY cnt DESC LIMIT %s
        """, (limit,))
        return cur.fetchall()

    def papers_by_year(self) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT publication_year AS year, COUNT(*) AS cnt
            FROM papers WHERE publication_year IS NOT NULL
            GROUP BY publication_year ORDER BY publication_year
        """)
        return cur.fetchall()

    def products_with_evidence_counts(self, limit: int = 30) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT p.product_id, p.canonical_name, p.manufacturer_name,
                   p.disease_area, p.modality,
                   SUM(CASE WHEN ppl.link_classification = 'exact_product' THEN 1 ELSE 0 END) AS exact_cnt,
                   SUM(CASE WHEN ppl.link_classification = 'manufacturer_linked' THEN 1 ELSE 0 END) AS mfg_cnt,
                   SUM(CASE WHEN ppl.link_classification = 'indication_related' THEN 1 ELSE 0 END) AS ind_cnt,
                   COUNT(*) AS total_cnt
            FROM products p
            JOIN product_paper_links ppl ON p.product_id = ppl.product_id
            GROUP BY p.product_id
            ORDER BY exact_cnt DESC, total_cnt DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()

    def fulltext_stats(self) -> dict:
        cur = self._cur()
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(fulltext) AS with_fulltext,
                   COUNT(doi) AS with_doi,
                   COUNT(pmid) AS with_pmid
            FROM papers
        """)
        return cur.fetchone()

    def recent_papers(self, limit: int = 10) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT pa.paper_id, pa.title, pa.journal, pa.publication_year, pa.doi,
                   COUNT(ppl.link_id) AS linked_products
            FROM papers pa
            LEFT JOIN product_paper_links ppl ON pa.paper_id = ppl.paper_id
            WHERE pa.publication_year IS NOT NULL
            GROUP BY pa.paper_id
            ORDER BY pa.publication_year DESC, pa.title
            LIMIT %s
        """, (limit,))
        return cur.fetchall()

    def review_queue(self, status: str = "pending", limit: int = 50) -> list[dict]:
        cur = self._cur()
        cur.execute("""
            SELECT ppl.link_id, p.canonical_name AS product_name,
                   p.product_id, pa.paper_id,
                   pa.title AS paper_title, pa.doi, pa.pmid,
                   ppl.link_classification, ppl.confidence_score,
                   ppl.matched_terms, ppl.rationale,
                   ppl.review_status, ppl.reviewed_by, ppl.review_notes,
                   ppl.created_at
            FROM product_paper_links ppl
            JOIN products p ON p.product_id = ppl.product_id
            JOIN papers pa ON pa.paper_id = ppl.paper_id
            WHERE ppl.human_review_needed = TRUE
              AND ppl.review_status = %s
            ORDER BY ppl.confidence_score DESC
            LIMIT %s
        """, (status, limit))
        return cur.fetchall()

    def review_stats(self) -> dict:
        cur = self._cur()
        cur.execute("""
            SELECT review_status, COUNT(*) AS cnt
            FROM product_paper_links
            WHERE human_review_needed = TRUE
            GROUP BY review_status
        """)
        return {r["review_status"]: r["cnt"] for r in cur.fetchall()}

    def submit_review(self, link_id: str, status: str,
                      new_classification: str = None,
                      reviewer: str = "admin",
                      notes: str = None) -> None:
        cur = self._cur()
        if new_classification:
            cur.execute("""
                UPDATE product_paper_links
                SET review_status = %s, link_classification = %s,
                    reviewed_by = %s, review_notes = %s,
                    reviewed_at = NOW(), updated_at = NOW()
                WHERE link_id = %s
            """, (status, new_classification, reviewer, notes, link_id))
        else:
            cur.execute("""
                UPDATE product_paper_links
                SET review_status = %s, reviewed_by = %s,
                    review_notes = %s, reviewed_at = NOW(), updated_at = NOW()
                WHERE link_id = %s
            """, (status, reviewer, notes, link_id))
        self.conn.commit()

    def execute_readonly(self, query: str) -> tuple[list[dict], list[str], int]:
        """Execute a read-only query. Returns (rows, columns, row_count).

        Raises ValueError if query is not SELECT.
        """
        if not query.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed.")
        cur = self._cur()
        cur.execute(query)
        rows = cur.fetchmany(500)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        return rows, columns, cur.rowcount
