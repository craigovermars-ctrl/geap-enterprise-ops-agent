terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

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

resource "google_secret_manager_secret_iam_member" "agent_secret_accessor" {
  secret_id = google_secret_manager_secret.gemini_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_runtime_sa.email}"
}

resource "google_artifact_registry_repository" "agent_images" {
  location      = var.region
  repository_id = "geap-ops-agents"
  format        = "DOCKER"
  description   = "Container images for GEAP Enterprise Ops ADK agents"
}

resource "google_project_iam_member" "agent_telemetry_writer" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.agent_runtime_sa.email}"
}
