# ── S3: raw EDGAR documents + the staged CSVs that RDS imports ──────────────
resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.project}-edgar-data-${random_id.suffix.hex}"
  tags   = { Name = "${var.project}-edgar-data" }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  # Staged CSVs are disposable once RDS has imported them.
  rule {
    id     = "expire-staging"
    status = "Enabled"
    filter { prefix = "staging/" }
    expiration { days = 14 }
  }

  # Raw filings are the re-extract source; keep but move to cheaper storage.
  rule {
    id     = "archive-raw"
    status = "Enabled"
    filter { prefix = "raw/" }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

# ── secrets ─────────────────────────────────────────────────────────────────
resource "random_password" "db" {
  length  = 32
  special = false # RDS rejects several punctuation chars in master passwords
}

resource "aws_ssm_parameter" "db_password" {
  name        = "/${var.project}/${var.environment}/db_password"
  description = "RDS master password"
  type        = "SecureString"
  value       = random_password.db.result
}

resource "aws_ssm_parameter" "db_dsn" {
  name        = "/${var.project}/${var.environment}/pg_dsn"
  description = "Postgres DSN used by the query API"
  type        = "SecureString"
  value       = "postgresql://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}"
}

resource "aws_ssm_parameter" "neo4j_password" {
  name        = "/${var.project}/${var.environment}/neo4j_password"
  description = "Neo4j password"
  type        = "SecureString"
  value       = var.neo4j_password
}

resource "aws_ssm_parameter" "deepseek_api_key" {
  count       = var.deepseek_api_key == "" ? 0 : 1
  name        = "/${var.project}/${var.environment}/deepseek_api_key"
  description = "DeepSeek API key for the query API"
  type        = "SecureString"
  value       = var.deepseek_api_key
}

# ── SSH key ─────────────────────────────────────────────────────────────────
resource "tls_private_key" "app" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "app" {
  key_name   = "${var.project}-${var.environment}-key"
  public_key = tls_private_key.app.public_key_openssh
}

resource "local_sensitive_file" "app_key" {
  content         = tls_private_key.app.private_key_openssh
  filename        = "${path.module}/.secrets/${var.project}-${var.environment}.pem"
  file_permission = "0600"
}

# Bedrock inference-profile ARNs are account-scoped, so the policy needs the id.
data "aws_caller_identity" "me" {}
