"""
Configuration for the Code Review Agent.
All values can be overridden via environment variables or .env file.

Quick start:
  export OPENAI_API_KEY=sk-...           # required (Anthropic-compatible API key)
  export OPENAI_MODEL=claude-sonnet-4-6  # optional
  export OPENAI_BASE_URL=https://...     # optional, for custom endpoints
  export GITHUB_TOKEN=ghp_...            # required for GitHub access

Custom endpoint examples:
  # DeepSeek Anthropic-compatible
  OPENAI_BASE_URL=https://api.deepseek.com/anthropic
  # Official Anthropic
  OPENAI_BASE_URL=https://api.anthropic.com
"""
import os
from pathlib import Path

# Auto-load .env file if present
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass


class Config:
    # LLM (OpenAI-compatible)
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "")
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "claude-sonnet-4-6")
    # Legacy alias
    ANTHROPIC_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    ANTHROPIC_MODEL: str = os.environ.get("OPENAI_MODEL", "claude-sonnet-4-6")
    THINKING_BUDGET: int = int(os.environ.get("THINKING_BUDGET", "8000"))
    MAX_TOKENS: int = int(os.environ.get("MAX_TOKENS", "16000"))
    ENABLE_THINKING: bool = os.environ.get("ENABLE_THINKING", "false").lower() != "false"

    # GitHub
    GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

    # Paths
    TRAJECTORIES_DIR: Path = Path(os.environ.get("TRAJECTORIES_DIR", "./trajectories"))
    EXPORTS_DIR: Path = Path(os.environ.get("EXPORTS_DIR", "./exports"))

    # Execution
    DRY_RUN: bool = os.environ.get("DRY_RUN", "false").lower() == "true"
    MAX_WORKERS: int = int(os.environ.get("MAX_WORKERS", "1"))

    # Feishu (Lark) integration
    FEISHU_ENABLED: bool = os.environ.get("FEISHU_ENABLED", "").lower() in ("1", "true", "yes")
    FEISHU_CHAT_ID: str = os.environ.get("FEISHU_CHAT_ID", "")
    FEISHU_BOT_NAME: str = os.environ.get("FEISHU_BOT_NAME", "Code Review Agent")
