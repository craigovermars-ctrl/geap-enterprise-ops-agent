"""Configuration and secure Secret Manager injection for GEAP Enterprise Ops Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from google.cloud import secretmanager
except ImportError:  # pragma: no cover - optional in lightweight test environments
    secretmanager = None


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration with strategic model routing and secret references."""

    project_id: str = os.getenv("GCP_PROJECT_ID", "demo-geap-project")
    location: str = os.getenv("GCP_LOCATION", "us-central1")
    fast_model: str = os.getenv("FAST_MODEL", "gemini-2.5-flash")
    planning_model: str = os.getenv("PLANNING_MODEL", "gemini-2.5-pro")
    session_db_url: str = os.getenv("SESSION_DB_URL", "sqlite:///./agent_sessions.db")
    max_context_tokens: int = int(os.getenv("MAX_CONTEXT_TOKENS", "12000"))
    enable_cloud_dlp: bool = os.getenv("ENABLE_CLOUD_DLP", "false").lower() == "true"
    secret_resource_name: str = os.getenv(
        "MODEL_API_KEY_SECRET_NAME",
        "projects/demo-geap-project/secrets/gemini-api-key/versions/latest",
    )


def fetch_secret(resource_name: str, fallback_env_var: str | None = None) -> str:
    """Retrieve a secret from Google Cloud Secret Manager without hardcoding credentials.

    Args:
        resource_name: Full Secret Manager version resource path
            (e.g. ``projects/<project>/secrets/<name>/versions/latest``).
        fallback_env_var: Optional environment variable used only in local dev/CI.

    Returns:
        The decoded secret payload string.

    Raises:
        RuntimeError: If neither Secret Manager nor the local dev fallback provides a value.
    """
    if secretmanager is not None and resource_name.startswith("projects/"):
        try:
            client = secretmanager.SecretManagerServiceClient()
            response = client.access_secret_version(request={"name": resource_name})
            return response.payload.data.decode("utf-8")
        except Exception:
            if fallback_env_var and os.getenv(fallback_env_var):
                return os.environ[fallback_env_var]
            raise

    if fallback_env_var and os.getenv(fallback_env_var):
        return os.environ[fallback_env_var]

    raise RuntimeError(
        f"Secret '{resource_name}' could not be retrieved from Secret Manager "
        f"and fallback env '{fallback_env_var}' is not set."
    )
