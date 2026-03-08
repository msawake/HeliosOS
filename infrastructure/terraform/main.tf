# ============================================================================
# AI Company Infrastructure - Terraform Configuration
# ============================================================================
# Provisions all cloud infrastructure for the AI-operated company:
# - EKS Kubernetes cluster (agent runtime)
# - RDS PostgreSQL (operational database)
# - ElastiCache Redis (rate limiting, caching)
# - S3 (agent transcripts, assets, backups)
# - Secrets Manager (API keys, credentials)
# - CloudWatch (monitoring, alerting)
# ============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "ai-company-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ai-company"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ============================================================================
# Variables
# ============================================================================

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "company_domain" {
  description = "Company domain name"
  type        = string
  default     = "digitalai.corp"
}

# ============================================================================
# VPC
# ============================================================================

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "ai-company-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = var.environment != "production"
  enable_dns_hostnames = true
  enable_dns_support   = true

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }
  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }
}

# ============================================================================
# EKS Cluster (Agent Runtime)
# ============================================================================

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "ai-company-cluster"
  cluster_version = "1.31"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    # Orchestrator nodes (higher memory for opus model contexts)
    orchestrators = {
      instance_types = ["m7i.xlarge"]
      min_size       = 1
      max_size       = 3
      desired_size   = 2

      labels = {
        agent-tier = "orchestrator"
      }
    }

    # Worker nodes (standard compute for sonnet/haiku)
    workers = {
      instance_types = ["m7i.large"]
      min_size       = 2
      max_size       = 10
      desired_size   = 3

      labels = {
        agent-tier = "worker"
      }
    }

    # System nodes (monitoring, dashboard, MCP servers)
    system = {
      instance_types = ["t3.large"]
      min_size       = 1
      max_size       = 2
      desired_size   = 1

      labels = {
        role = "system"
      }
    }
  }
}

# ============================================================================
# RDS PostgreSQL (Operational Database)
# ============================================================================

resource "aws_db_subnet_group" "main" {
  name       = "ai-company-db"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "rds" {
  name_prefix = "ai-company-rds-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.cluster_security_group_id]
  }
}

resource "aws_db_instance" "main" {
  identifier = "ai-company-db"

  engine         = "postgres"
  engine_version = "16.3"
  instance_class = "db.r7g.large"

  allocated_storage     = 100
  max_allocated_storage = 500
  storage_encrypted     = true

  db_name  = "aicompany"
  username = "aicompany_admin"
  password = var.db_password

  multi_az               = var.environment == "production"
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 30
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  deletion_protection = true
  skip_final_snapshot = false
  final_snapshot_identifier = "ai-company-db-final"

  performance_insights_enabled = true

  parameter_group_name = aws_db_parameter_group.main.name
}

resource "aws_db_parameter_group" "main" {
  family = "postgres16"
  name   = "ai-company-pg16"

  parameter {
    name  = "log_statement"
    value = "all"
  }

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements,vector"
  }
}

# ============================================================================
# ElastiCache Redis (Rate Limiting, Session Cache)
# ============================================================================

resource "aws_security_group" "redis" {
  name_prefix = "ai-company-redis-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.cluster_security_group_id]
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "ai-company-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "ai-company-cache"
  description          = "AI Company rate limiting and session cache"

  node_type            = "cache.r7g.large"
  num_cache_clusters   = var.environment == "production" ? 2 : 1
  port                 = 6379

  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  automatic_failover_enabled = var.environment == "production"

  snapshot_retention_limit = 7
  snapshot_window          = "05:00-06:00"
  maintenance_window       = "Mon:06:00-Mon:07:00"
}

# ============================================================================
# S3 Buckets
# ============================================================================

resource "aws_s3_bucket" "transcripts" {
  bucket = "ai-company-agent-transcripts"
}

resource "aws_s3_bucket_lifecycle_configuration" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  rule {
    id     = "expire-old-transcripts"
    status = "Enabled"

    expiration {
      days = 90
    }

    transition {
      days          = 30
      storage_class = "GLACIER"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "transcripts" {
  bucket = aws_s3_bucket.transcripts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket" "assets" {
  bucket = "ai-company-generated-assets"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket" "backups" {
  bucket = "ai-company-backups"
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ============================================================================
# Secrets Manager
# ============================================================================

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "ai-company/anthropic-api-key"
  description = "Anthropic API key for Claude agent invocations"
}

resource "aws_secretsmanager_secret" "github_token" {
  name        = "ai-company/github-token"
  description = "GitHub token for MCP server"
}

resource "aws_secretsmanager_secret" "stripe_api_key" {
  name        = "ai-company/stripe-api-key"
  description = "Stripe API key for payment processing"
}

resource "aws_secretsmanager_secret" "slack_token" {
  name        = "ai-company/slack-bot-token"
  description = "Slack bot token for notifications"
}

resource "aws_secretsmanager_secret" "google_workspace_creds" {
  name        = "ai-company/google-workspace-credentials"
  description = "Google Workspace service account credentials"
}

# ============================================================================
# CloudWatch (Monitoring)
# ============================================================================

resource "aws_cloudwatch_log_group" "agent_logs" {
  name              = "/ai-company/agents"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "audit_logs" {
  name              = "/ai-company/audit"
  retention_in_days = 365  # Compliance requirement
}

resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "ai-company-high-agent-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "agent_errors"
  namespace           = "AICompany"
  period              = 300  # 5 minutes
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Agent error rate exceeding threshold"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "high_cost" {
  alarm_name          = "ai-company-high-daily-cost"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "daily_cost_usd"
  namespace           = "AICompany"
  period              = 86400  # 24 hours
  statistic           = "Maximum"
  threshold           = 500  # $500/day alert
  alarm_description   = "Daily agent costs exceeding budget"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_sns_topic" "alerts" {
  name = "ai-company-alerts"
}

# ============================================================================
# Outputs
# ============================================================================

output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "rds_endpoint" {
  value = aws_db_instance.main.endpoint
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "s3_transcripts_bucket" {
  value = aws_s3_bucket.transcripts.id
}

output "s3_assets_bucket" {
  value = aws_s3_bucket.assets.id
}
