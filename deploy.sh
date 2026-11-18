#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID before deploying}"
REGION="${GCP_LOCATION:-us-central1}"

echo "==> 1. Provisioning GCP infrastructure via Terraform (Secret Manager, IAM, Artifact Registry)"
terraform -chdir=terraform init
terraform -chdir=terraform apply -auto-approve -var="project_id=${PROJECT_ID}" -var="region=${REGION}"

echo "==> 2. Deploying GEAP Enterprise Ops Agent to Vertex AI Agent Engine via ADK / Agents CLI"
adk deploy agent_engine \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --display_name="geap-enterprise-ops-coordinator" \
  agent_app
