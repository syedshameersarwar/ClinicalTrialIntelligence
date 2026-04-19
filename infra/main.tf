terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_storage_bucket" "benchmark" {
  name          = "benchmark-${var.project_id}"
  location      = var.region
  force_destroy = true

  lifecycle_rule {
    condition { age = 90 }
    action { type = "Delete" }
  }
}

resource "google_bigquery_dataset" "raw" {
  dataset_id = "raw"
  location   = var.location
}

resource "google_bigquery_dataset" "streaming" {
  dataset_id = "streaming"
  location   = var.location
}

resource "google_bigquery_dataset" "dbt_marts" {
  dataset_id = "dbt_marts"
  location   = var.location
}

resource "google_service_account" "sa" {
  account_id   = "benchmark-sa"
  display_name = "BenchMark Service Account"
}

resource "google_project_iam_member" "bq_admin" {
  project = var.project_id
  role    = "roles/bigquery.admin"
  member  = "serviceAccount:${google_service_account.sa.email}"
}

resource "google_project_iam_member" "gcs_admin" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.sa.email}"
}

resource "google_service_account_key" "sa_key" {
  service_account_id = google_service_account.sa.name
}

output "service_account_key" {
  value     = google_service_account_key.sa_key.private_key
  sensitive = true
}

output "gcs_bucket_name" {
  value = google_storage_bucket.benchmark.name
}
