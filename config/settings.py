"""Foreman settings. Everything comes from .env or the environment."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Meta Model API (OpenAI-compatible)
    model_api_key: str = Field(default="", alias="MODEL_API_KEY")
    model_api_base_url: str = Field(default="https://api.meta.ai/v1", alias="MODEL_API_BASE_URL")

    # Models: Reviewer on Muse Wonder, Fixer on Muse Spark 1.3
    reviewer_model: str = Field(default="muse-wonder-1.0", alias="REVIEWER_MODEL")
    fixer_model: str = Field(default="muse-spark-1.3", alias="FIXER_MODEL")
    reviewer_reasoning_effort: str = Field(default="medium", alias="REVIEWER_REASONING_EFFORT")
    fixer_reasoning_effort: str = Field(default="low", alias="FIXER_REASONING_EFFORT")

    # FOREMAN_MOCK=1 runs both agents on a local stand-in model. No API calls, no key needed.
    mock: bool = Field(default=False, alias="FOREMAN_MOCK")
    model_timeout: int = Field(default=120, alias="FOREMAN_MODEL_TIMEOUT")  # seconds per model call

    # Store: MongoDB if MONGO_URI is set, otherwise JSON files under data/db/.
    # Same database as mongo_store.py (health_monitor). His collections: system_metrics,
    # health_results, log_summaries, logs. Ours: reports, findings, fixes. No overlap.
    mongo_uri: str = Field(default="", alias="MONGO_URI")
    mongo_db: str = Field(default="health_monitor", alias="MONGO_DB")

    # Where the Reviewer reads Chip and Chuck from: auto (Mongo, then files), mongo, file
    input_source: str = Field(default="auto", alias="FOREMAN_INPUT")

    # Execution: "shell" runs fix commands directly, "muse" hands them to `muse exec`
    executor: str = Field(default="shell", alias="FOREMAN_EXECUTOR")
    execute_timeout: int = Field(default=120, alias="FOREMAN_EXECUTE_TIMEOUT")

    # History window the Reviewer reads before writing
    history_days: int = Field(default=7, alias="FOREMAN_HISTORY_DAYS")

    # Built-in scheduler: run a review every N minutes when new Chip/Chuck data appears. 0 = off.
    review_interval_min: int = Field(default=0, alias="FOREMAN_REVIEW_INTERVAL_MIN")
    server_name: str = Field(default="", alias="FOREMAN_SERVER_NAME")  # label on reports; hostname if empty

    # Paths
    prompts_dir: Path = ROOT / "prompts"
    data_dir: Path = ROOT / "data"
    inbox_dir: Path = ROOT / "data" / "inbox"
    reports_dir: Path = ROOT / "data" / "reports"
    db_dir: Path = ROOT / "data" / "db"
    logs_dir: Path = ROOT / "logs"
    chip_file: Path = ROOT / "data" / "inbox" / "health.json"   # chip.py --output
    chuck_file: Path = ROOT / "data" / "inbox" / "logs.json"    # chuck.py --output

    # API
    api_host: str = Field(default="0.0.0.0", alias="FOREMAN_API_HOST")
    api_port: int = Field(default=8000, alias="FOREMAN_API_PORT")


settings = Settings()
if not settings.server_name:
    import platform

    settings.server_name = platform.node().split(".")[0] or "local"

# These folders are gitignored and empty on a fresh clone. Create them so nothing crashes on first run.
for _d in (settings.inbox_dir, settings.reports_dir, settings.db_dir, settings.logs_dir, settings.data_dir / "samples"):
    _d.mkdir(parents=True, exist_ok=True)
