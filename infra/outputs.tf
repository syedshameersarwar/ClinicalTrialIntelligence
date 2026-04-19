output "bucket_name" {
  value       = google_storage_bucket.benchmark.name
  description = "GCS bucket used as Spark temp and staging area"
}

output "raw_dataset" {
  value = google_bigquery_dataset.raw.dataset_id
}

output "streaming_dataset" {
  value = google_bigquery_dataset.streaming.dataset_id
}

output "dbt_marts_dataset" {
  value = google_bigquery_dataset.dbt_marts.dataset_id
}

output "service_account_email" {
  value = google_service_account.sa.email
}
