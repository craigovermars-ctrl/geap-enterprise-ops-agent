variable "project_id" {
  description = "GCP project ID hosting the GEAP Enterprise Ops Agent."
  type        = string
}

variable "region" {
  description = "Primary GCP region for Agent Engine, Cloud Run, and Secret Manager."
  type        = string
  default     = "us-central1"
}
