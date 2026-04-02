"""PMDA SaMD product scraper.

Downloads official Excel lists from PMDA website:
- Approved SaMD (承認品目): dedicated SaMD page
- Certified devices (認証品目): full list filtered for SaMD keywords

Based on PMDA monitor script. Outputs normalized product data
compatible with the pipeline ingestion format.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.ingestion.jp_mappings import map_manufacturer
from src.ingestion.normalizer import enrich_product
from src.models.product import (
    AliasType,
    EvidenceTier,
    Product,
    ProductAlias,
    RegionCode,
    RegulatoryEntry,
    RegulatoryPathway,
    RegulatoryStatusNormalized,
)
from src.utils import parse_date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PMDA URLs and constants
# ---------------------------------------------------------------------------

APPROVAL_PAGE = "https://www.pmda.go.jp/review-services/drug-reviews/about-reviews/devices/0052.html"
CERT_PAGE = "https://www.pmda.go.jp/review-services/drug-reviews/about-reviews/devices/0026.html"

UA = "Mozilla/5.0 (compatible; PMDA-SaMD-Monitor/1.0)"

APPROVAL_LINK_TEXT = "製造販売承認品目の一覧情報はこちら"
CERT_LINK_TEXT = "認証品目リスト"
CERT_EXCEL_HINT = "エクセル版"

# Step 1: Keywords to find software medical devices in the certification list
SOFTWARE_INCLUDE_KEYWORDS = [
    "プログラム",
    "software",
    "SaMD",
    "アプリ",
]

# Step 2: Exclude non-software devices that false-match step 1
SOFTWARE_EXCLUDE_KEYWORDS = [
    "プログラム式補聴器",
    "プログラム式洗浄",
    "プログラム式消毒",
    "プログラム式滅菌",
    "プログラム付き電子体温計",
    "プログラム式電解水",
    "プログラム式電位治療",
    "電解水生成器",
]

# Step 3: From software devices, keep only AI/ML SaMD
# Must match at least one of these in product name or generic name
AIML_INCLUDE_KEYWORDS = [
    "AI", "ＡＩ",
    "解析",          # analysis
    "検出",          # detection
    "診断支援",      # diagnostic support
    "検知",          # detection/sensing
    "定量",          # quantification
    "分類",          # classification
    "予測",          # prediction
    "アルゴリズム",  # algorithm
    "ディープ",      # deep (learning)
    "ニューラル",    # neural
    "セグメンテーション",  # segmentation
    "スコアリング",  # scoring
    "トリアージ",    # triage
    "計測",          # measurement (automated)
    "自動",          # automatic
]

# Manufacturer JP→EN mappings: shared from src.ingestion.jp_mappings

# Data directory for caching raw files and snapshots
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "pmda_raw"


# ---------------------------------------------------------------------------
# HTML / download helpers
# ---------------------------------------------------------------------------

def _fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    html = r.text
    if "メンテナンス" in html and "PMDA" in html:
        raise RuntimeError(f"PMDA maintenance page detected: {url}")
    return html


def _find_link(page_url: str, html: str, required_text: str,
               extra_text: str | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # First pass: find .xlsx links matching the text
    for a in soup.select("a[href]"):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href", "").strip()
        if not href:
            continue
        if required_text in text and (extra_text is None or extra_text in text):
            return urljoin(page_url, href)
    # Second pass: find any .xlsx link containing the required text (ignoring size annotations)
    for a in soup.select("a[href]"):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href", "").strip()
        if not href or not href.endswith((".xlsx", ".xls")):
            continue
        # Strip size annotations like [50.1KB] before matching
        clean_text = re.sub(r"\[[\d.]+[KMG]?B\]", "", text).strip()
        if required_text in clean_text and (extra_text is None or extra_text in clean_text):
            return urljoin(page_url, href)
    raise RuntimeError(f"Link not found: {required_text!r} on {page_url}")


def _download(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Excel parsing
# ---------------------------------------------------------------------------

def _read_excel_all_sheets(data: bytes) -> pd.DataFrame:
    """Read Excel, auto-detecting header row (PMDA files have notes at the top)."""
    xls = pd.ExcelFile(io.BytesIO(data))
    frames = []
    for sheet in xls.sheet_names:
        # Read without header first to find the header row
        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        header_row = _find_header_row(raw)
        df = pd.read_excel(xls, sheet_name=sheet, header=header_row)
        df["__sheet__"] = sheet
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _find_header_row(df: pd.DataFrame) -> int:
    """Find the row that looks like a header (has multiple non-empty cells with known column names)."""
    known_headers = {"販売名", "一般的名称", "承認番号", "認証番号", "申請者", "No."}
    for i in range(min(30, len(df))):
        row_values = {str(v).strip() for v in df.iloc[i] if pd.notna(v)}
        matches = row_values & known_headers
        if len(matches) >= 2:
            return i
    return 0  # fallback to first row


def _normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_normalize_text(c) for c in df.columns]
    return df


def _filter_samd(df: pd.DataFrame) -> pd.DataFrame:
    """Filter certification list for AI/ML SaMD only.

    3-step filter:
    1. Include rows with software keywords (プログラム, software, etc.)
    2. Exclude known non-software devices (programmable hearing aids, etc.)
    3. Keep only AI/ML devices (解析, 検出, 診断支援, AI, etc.)
    """
    df = _normalize_columns(df)
    text_cols = [c for c in df.columns
                 if any(k in c for k in ["一般的名称", "販売名", "類別", "名称"])]
    if not text_cols:
        return df.iloc[0:0].copy()

    def _match(keywords):
        mask = pd.Series(False, index=df.index)
        for col in text_cols:
            s = df[col].astype(str).fillna("")
            for kw in keywords:
                mask |= s.str.contains(kw, case=False, na=False)
        return mask

    # Step 1: software devices
    software_mask = _match(SOFTWARE_INCLUDE_KEYWORDS)
    # Step 2: exclude non-software
    exclude_mask = _match(SOFTWARE_EXCLUDE_KEYWORDS)
    # Step 3: AI/ML only
    aiml_mask = _match(AIML_INCLUDE_KEYWORDS)

    result = df.loc[software_mask & ~exclude_mask & aiml_mask].copy()
    logger.info("PMDA certification filter: software=%d, excluded=%d, AI/ML=%d → %d",
                software_mask.sum(), exclude_mask.sum(), aiml_mask.sum(), len(result))
    return result


# ---------------------------------------------------------------------------
# Manufacturer name mapping
# ---------------------------------------------------------------------------

def _map_manufacturer(jp_name: str) -> str:
    en, _ = map_manufacturer(jp_name)
    return en


# ---------------------------------------------------------------------------
# DataFrame → Product conversion
# ---------------------------------------------------------------------------

def _find_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """Find the first matching column name."""
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    # Partial match
    for cand in candidates:
        for col in df.columns:
            if cand in col:
                return col
    return None


def _df_to_products(
    df: pd.DataFrame,
    pathway: RegulatoryPathway,
    status: RegulatoryStatusNormalized,
) -> list[tuple[Product, RegulatoryEntry, list[ProductAlias]]]:
    """Convert a PMDA DataFrame to Product objects."""
    df = _normalize_columns(df)
    for col in df.columns:
        if df[col].dtype == "object":
            df.loc[:, col] = df[col].map(_normalize_text)

    # Find columns flexibly
    name_col = _find_column(df, ["販売名", "販売名称", "品目名"])
    number_col = _find_column(df, ["承認番号", "認証番号"])
    date_col = _find_column(df, ["承認年月日", "認証年月日", "承認日", "認証日"])
    mfg_col = _find_column(df, ["製造販売業者", "申請者", "届出者"])
    generic_col = _find_column(df, ["一般的名称"])
    use_col = _find_column(df, ["使用目的", "効能又は効果", "使用目的又は効果"])
    class_col = _find_column(df, ["クラス", "分類"])

    results = []
    for _, row in df.iterrows():
        ja_name = str(row.get(name_col, "")).strip() if name_col else ""
        if not ja_name or ja_name == "nan":
            continue

        reg_number = str(row.get(number_col, "")).strip() if number_col else ""
        date_str = str(row.get(date_col, "")).strip() if date_col else ""
        mfg_raw = str(row.get(mfg_col, "")).strip() if mfg_col else ""
        generic_name = str(row.get(generic_col, "")).strip() if generic_col else ""
        intended_use = str(row.get(use_col, "")).strip() if use_col else ""
        device_class = str(row.get(class_col, "")).strip() if class_col else ""

        # Clean nan strings
        for var_name in ["reg_number", "date_str", "mfg_raw", "generic_name",
                         "intended_use", "device_class"]:
            val = locals()[var_name]
            if val == "nan":
                locals()[var_name] = ""

        manufacturer_en = _map_manufacturer(mfg_raw)

        product = Product(
            canonical_name=ja_name,  # Will use JA name as canonical; EN alias added if found
            manufacturer_name=manufacturer_en,
            intended_use=intended_use or None,
            standalone_samd=True,
        )

        entry = RegulatoryEntry(
            product_id=product.product_id,
            region=RegionCode.JP,
            regulatory_pathway=pathway,
            regulatory_status_raw=pathway.value,
            regulatory_status=status,
            regulatory_id=reg_number or None,
            clearance_date=parse_date(date_str),
            device_class=device_class or None,
            applicant=mfg_raw,
            evidence_tier=EvidenceTier.TIER_1,
        )

        aliases = [
            ProductAlias(
                product_id=product.product_id,
                alias_name=ja_name,
                alias_type=AliasType.JAPANESE_NAME,
                language="ja",
                is_primary=True,
                source="pmda_excel",
            ),
        ]
        if generic_name:
            aliases.append(ProductAlias(
                product_id=product.product_id,
                alias_name=generic_name,
                alias_type=AliasType.GENERIC_NAME,
                language="ja",
                source="pmda_excel",
            ))

        results.append((product, entry, aliases))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_pmda_approval_list(
    ai_only: bool = True,
) -> list[tuple[Product, RegulatoryEntry, list[ProductAlias]]]:
    """Fetch the PMDA approved SaMD products Excel list.

    The approval Excel has an 'AI活用医療機器' column with ○ for AI devices.
    If ai_only=True, only products with this flag are returned.
    If ai_only=False, all program medical devices are returned.
    """
    logger.info("Fetching PMDA approval page: %s", APPROVAL_PAGE)
    html = _fetch_html(APPROVAL_PAGE)
    file_url = _find_link(APPROVAL_PAGE, html, APPROVAL_LINK_TEXT)
    logger.info("Downloading approval Excel: %s", file_url)
    data = _download(file_url)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw_path = DATA_DIR / f"approval_{ts}.xlsx"
    raw_path.write_bytes(data)

    df = _read_excel_all_sheets(data)
    df = _normalize_columns(df)

    # Filter for AI/ML devices
    if ai_only:
        ai_col = _find_column(df, ["AI活用医療機器", "AI"])
        before = len(df)

        # Primary: official AI flag (○)
        if ai_col:
            ai_flag_mask = df[ai_col].fillna("").astype(str).str.contains("○", na=False)
        else:
            ai_flag_mask = pd.Series(False, index=df.index)

        # Supplementary: keyword match in product name / generic name
        # (PMDA note says the AI flag is NOT exhaustive)
        text_cols = [c for c in df.columns if any(k in c for k in ["販売名", "一般的名称"])]
        kw_mask = pd.Series(False, index=df.index)
        for col in text_cols:
            s = df[col].astype(str).fillna("")
            for kw in AIML_INCLUDE_KEYWORDS:
                kw_mask |= s.str.contains(kw, case=False, na=False)

        df = df[ai_flag_mask | kw_mask].copy()
        ai_count = ai_flag_mask.sum()
        kw_extra = len(df) - ai_count
        logger.info("PMDA approval AI filter: %d → %d (flag=%d, keyword supplement=%d)",
                    before, len(df), ai_count, kw_extra)

    products = _df_to_products(df, RegulatoryPathway.APPROVAL, RegulatoryStatusNormalized.APPROVED)
    logger.info("PMDA approval: %d products from Excel (ai_only=%s)", len(products), ai_only)
    return products


def fetch_pmda_certification_list() -> list[tuple[Product, RegulatoryEntry, list[ProductAlias]]]:
    """Fetch the PMDA certification list and filter for SaMD."""
    logger.info("Fetching PMDA certification page: %s", CERT_PAGE)
    html = _fetch_html(CERT_PAGE)
    file_url = _find_link(CERT_PAGE, html, CERT_LINK_TEXT, CERT_EXCEL_HINT)
    logger.info("Downloading certification Excel: %s", file_url)
    data = _download(file_url)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw_path = DATA_DIR / f"certification_{ts}.xlsx"
    raw_path.write_bytes(data)

    df = _read_excel_all_sheets(data)
    df = _filter_samd(df)
    products = _df_to_products(df, RegulatoryPathway.CERTIFICATION, RegulatoryStatusNormalized.CERTIFIED)
    logger.info("PMDA certification: %d SaMD candidates from Excel", len(products))
    return products


def fetch_all_pmda_products() -> list[tuple[Product, list[RegulatoryEntry]]]:
    """Fetch both approval and certification lists, enrich, and return.

    Returns the same format as other ingestion functions in src/pipeline.py.
    """
    all_raw = []

    try:
        approval = fetch_pmda_approval_list()
        for product, entry, aliases in approval:
            product = enrich_product(product)
            product.aliases = aliases
            entry.product_id = product.product_id
            product.regulatory_entries = [entry]
            all_raw.append((product, [entry]))
    except Exception:
        logger.exception("Failed to fetch PMDA approval list")

    try:
        certification = fetch_pmda_certification_list()
        for product, entry, aliases in certification:
            product = enrich_product(product)
            product.aliases = aliases
            entry.product_id = product.product_id
            product.regulatory_entries = [entry]
            all_raw.append((product, [entry]))
    except Exception:
        logger.exception("Failed to fetch PMDA certification list")

    logger.info("PMDA total: %d products (approval + certification)", len(all_raw))
    return all_raw
