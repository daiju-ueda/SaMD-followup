"""Application configuration via environment variables.

All settings are read from env vars prefixed with SAMD_ or from .env file.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    db_dsn: str = "dbname=samd_evidence"  # psycopg2 DSN for sync access
    database_url: str = "postgresql+asyncpg://samd:samd@localhost:5432/samd_evidence"

    # FDA / openFDA
    openfda_base_url: str = "https://api.fda.gov"
    openfda_api_key: str = ""

    # NCBI / PubMed
    ncbi_api_key: str = ""
    ncbi_email: str = ""
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # Europe PMC
    europe_pmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    # OpenAlex
    openalex_base_url: str = "https://api.openalex.org"
    openalex_email: str = ""

    # Rate limiting
    pubmed_requests_per_second: float = 3.0
    openfda_requests_per_second: float = 4.0
    openalex_requests_per_second: float = 10.0

    # Scoring thresholds
    exact_product_min_score: float = 50.0
    product_family_min_score: float = 30.0
    manufacturer_linked_min_score: float = 20.0
    indication_related_min_score: float = 10.0
    human_review_score_low: float = 20.0
    human_review_score_high: float = 50.0

    # Ingestion
    batch_size: int = 100
    max_papers_per_query: int = 500

    # Similarity thresholds for deduplication
    product_name_similarity: float = 0.85
    manufacturer_similarity: float = 0.80

    model_config = {"env_prefix": "SAMD_", "env_file": ".env"}


settings = Settings()
