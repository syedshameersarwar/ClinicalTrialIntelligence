variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for GCS bucket"
  type        = string
  default     = "us-central1"
}

variable "location" {
  description = "BigQuery dataset location"
  type        = string
  default     = "US"
}
