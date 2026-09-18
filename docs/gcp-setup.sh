#!/usr/bin/env bash
# One-time GCP setup for the IC Factory Database pipeline. Run as a project owner/editor.
# NOTE (2026-09): the warehouse is Neon Postgres and raw archives are Vercel Blob; this script is kept
# for the later Cloud Run Jobs / Cloud SQL move. The bucket and BigQuery steps are no longer on the path.
# Usage: PROJECT=my-gcp-project REPO=pcsmith2000/IC_Factory_Database bash docs/gcp-setup.sh
set -euo pipefail
: "${PROJECT:?set PROJECT}"; : "${REPO:?set REPO owner/name}"
REGION=${REGION:-us-central1}; BUCKET=${BUCKET:-ic-factory-database}; DATASET=${DATASET:-ic_factory}
SA=ic-pipeline; SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

gcloud config set project "$PROJECT"
gcloud services enable storage.googleapis.com bigquery.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com run.googleapis.com

gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access 2>/dev/null || true
bq --location="$REGION" mk -d "$PROJECT:$DATASET" 2>/dev/null || true
gcloud iam service-accounts create "$SA" --display-name="IC pipeline" 2>/dev/null || true

gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA_EMAIL" --role=roles/storage.objectAdmin
bq add-iam-policy-binding --member="serviceAccount:$SA_EMAIL" --role=roles/bigquery.dataEditor "$PROJECT:$DATASET" >/dev/null || \
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA_EMAIL" --role=roles/bigquery.dataEditor
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA_EMAIL" --role=roles/bigquery.jobUser
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA_EMAIL" --role=roles/secretmanager.secretAccessor

# Workload Identity Federation for GitHub Actions — no stored key
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub" 2>/dev/null || true
gcloud iam workload-identity-pools providers create-oidc github-oidc --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'" 2>/dev/null || true
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

echo
echo "Set these GitHub repo variables:"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc"
echo "  GCP_SERVICE_ACCOUNT=$SA_EMAIL"
echo "  BQ_DATASET=$PROJECT.$DATASET"
echo "And secrets: AI_GATEWAY_API_KEY, CENSUS_API_KEY (also: gcloud secrets create ... for Cloud Run)"
