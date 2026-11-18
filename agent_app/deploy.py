"""Programmatic Infrastructure as Code (IaC) and ADK Agent CLI deployment helper."""

from __future__ import annotations

import subprocess
from pathlib import Path

TERRAFORM_CONFIG = """
resource "google_service_account" "agent_runtime_sa" {
  account_id   = "geap-ops-agent-sa"
  display_name = "GEAP Enterprise Ops Agent Runtime Service Account"
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
}
""".strip()


def provision_infrastructure(project_id: str, region: str = "us-central1", dry_run: bool = True) -> list[str]:
    """Provision GCP resources with Terraform IaC and deploy via ADK / Agents CLI."""
    tf_dir = Path(__file__).resolve().parent.parent / "terraform"
    commands = [
        f"terraform -chdir={tf_dir} init",
        f"terraform -chdir={tf_dir} apply -auto-approve -var=project_id={project_id} -var=region={region}",
        f"adk deploy agent_engine --project={project_id} --region={region} --display_name=geap-enterprise-ops-coordinator agent_app",
    ]
    if not dry_run:
        for cmd in commands:
            subprocess.run(cmd.split(), check=True)
    return commands
