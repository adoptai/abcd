import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (one level above backend/)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


@dataclass
class Settings:
    app_name: str = "elicitation-agent-backend"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    cors_origins: list[str] = field(default_factory=lambda: [
        "chrome-extension://*",
        "http://localhost:3000",
        "http://localhost:8002",
    ])

    data_dir: str = ""
    db_url: str = ""

    # Claude API
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    def __post_init__(self):
        if not self.data_dir:
            self.data_dir = str(Path(__file__).resolve().parent.parent / "data")
        if not self.db_url:
            self.db_url = f"sqlite+aiosqlite:///{self.data_dir}/elicitation.db"


settings = Settings(
    debug=os.environ.get("ELIC_DEBUG", "true").lower() == "true",
    port=int(os.environ.get("ELIC_PORT", "8002")),
    anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
)
