# ============================================================================
# Helios OS SaaS — Google Cloud Platform Infrastructure
# ============================================================================
#
# Provisions:
# - Cloud SQL (PostgreSQL 16 with pgvector)
# - Memorystore (Redis 7.x)
# - Cloud Run (API server + agent workers)
# - Secret Manager (API keys, MCP credentials)
# - Cloud Storage (artifacts, reports)
# - VPC with private services access
# - Cloud Logging + Monitoring
# ============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "forgeos-terraform-state"
    prefix = "terraform/state"
  }
}

# ── Variables ────────────────────────────────────────────────────────────

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "db_tier" {
  description = "Cloud SQL machine type"
  type        = string
  default     = "db-custom-2-8192" # 2 vCPU, 8GB RAM
}

variable "redis_memory_gb" {
  description = "Redis memory in GB"
  type        = number
  default     = 1
}

variable "daily_budget_usd" {
  description = "Daily budget alert threshold"
  type        = number
  default     = 500
}

# ── Provider ─────────────────────────────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

# ── Enable APIs ──────────────────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ── VPC ──────────────────────────────────────────────────────────────────

resource "google_compute_network" "vpc" {
  name                    = "forgeos-${var.environment}"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "main" {
  name          = "forgeos-${var.environment}-main"
  ip_cidr_range = "10.0.0.0/20"
  network       = google_compute_network.vpc.id
  region        = var.region

  private_ip_google_access = true
}

resource "google_compute_global_address" "private_ip" {
  name          = "forgeos-${var.environment}-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

# ── Cloud SQL (PostgreSQL) ───────────────────────────────────────────────

resource "google_sql_database_instance" "postgres" {
  name             = "forgeos-${var.environment}"
  database_version = "POSTGRES_16"
  region           = var.region

  depends_on = [google_service_networking_connection.private_vpc]

  settings {
    tier              = var.db_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    database_flags {
      name  = "max_connections"
      value = "200"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = var.environment == "prod"
      start_time                     = "03:00"
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = true
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 4
      update_track = "stable"
    }
  }

  deletion_protection = var.environment == "prod"
}

resource "google_sql_database" "forgeos" {
  name     = "forgeos"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "forgeos" {
  name     = "forgeos"
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

# ── Memorystore (Redis) ─────────────────────────────────────────────────

resource "google_redis_instance" "cache" {
  name           = "forgeos-${var.environment}"
  tier           = var.environment == "prod" ? "STANDARD_HA" : "BASIC"
  memory_size_gb = var.redis_memory_gb
  region         = var.region
  redis_version  = "REDIS_7_0"

  authorized_network = google_compute_network.vpc.id

  depends_on = [google_project_service.apis]
}

# ── Secret Manager ───────────────────────────────────────────────────────

resource "google_secret_manager_secret" "db_password" {
  secret_id = "forgeos-${var.environment}-db-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "forgeos-${var.environment}-anthropic-key"
  replication {
    auto {}
  }
}

# ── Cloud Storage ────────────────────────────────────────────────────────

resource "google_storage_bucket" "artifacts" {
  name          = "forgeos-${var.environment}-artifacts-${var.project_id}"
  location      = var.region
  force_destroy = var.environment != "prod"

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
}

# ── Artifact Registry (Docker images) ───────────────────────────────────

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "forgeos"
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

# ── Cloud Run (API Server) ──────────────────────────────────────────────

resource "google_cloud_run_v2_service" "api" {
  name     = "forgeos-api-${var.environment}"
  location = var.region

  template {
    scaling {
      min_instance_count = var.environment == "prod" ? 1 : 0
      max_instance_count = 10
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/forgeos/api:latest"

      ports {
        container_port = 5000
      }

      env {
        name  = "CLOUD_SQL_INSTANCE"
        value = google_sql_database_instance.postgres.connection_name
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.cache.host}:${google_redis_instance.cache.port}"
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      startup_probe {
        http_get {
          path = "/api/health"
        }
        initial_delay_seconds = 10
        period_seconds        = 5
      }

      liveness_probe {
        http_get {
          path = "/api/health"
        }
        period_seconds = 30
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.name
        subnetwork = google_compute_subnetwork.main.name
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
  }

  depends_on = [google_project_service.apis]
}

# ── Budget Alert ─────────────────────────────────────────────────────────

resource "google_billing_budget" "daily" {
  billing_account = data.google_billing_account.account.id
  display_name    = "Helios OS ${var.environment} Daily Budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.daily_budget_usd * 30) # Monthly
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}

data "google_billing_account" "account" {
  open = true
}

# ── Outputs ──────────────────────────────────────────────────────────────

output "cloud_sql_connection" {
  value = google_sql_database_instance.postgres.connection_name
}

output "redis_host" {
  value = google_redis_instance.cache.host
}

output "cloud_run_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "artifacts_bucket" {
  value = google_storage_bucket.artifacts.name
}

output "docker_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/forgeos"
}
