"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://samd:samd@localhost:5432/samd_evidence"
    database_url_sync: str = "postgresql://samd:samd@localhost:5432/samd_evidence"

    # FDA / openFDA
    openfda_base_url: str = "https://api.fda.gov"
    openfda_api_key: str = ""  # optional, raises rate limit

    # NCBI / PubMed
    ncbi_api_key: str = ""  # optional, raises rate limit from 3/s to 10/s
    ncbi_email: str = ""    # required by NCBI policy
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    # Europe PMC
    europe_pmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    # OpenAlex
    openalex_base_url: str = "https://api.openalex.org"
    openalex_email: str = ""  # polite pool

    # Rate limiting
    pubmed_requests_per_second: float = 3.0
    openfda_requests_per_second: float = 4.0
    openalex_requests_per_second: float = 10.0

    # Scoring
    human_review_score_low: float = 20.0
    human_review_score_high: float = 50.0

    # Ingestion
    batch_size: int = 100
    max_papers_per_query: int = 500

    model_config = {"env_prefix": "SAMD_", "env_file": ".env"}


settings = Settings()
