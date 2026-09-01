variable "region" {
  description = "AWS region."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "graphrag"
}

variable "environment" {
  type    = string
  default = "demo"
}

variable "vpc_cidr" {
  description = "Deliberately not 172.31/16 - the account's default VPC uses that."
  type        = string
  default     = "10.42.0.0/16"
}

variable "admin_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach SSH and the Neo4j browser. Defaults to the operator's
    current public IP (see scripts/tf.sh, which refreshes it). Never 0.0.0.0/0.
  EOT
  type        = list(string)
}

# ── database ────────────────────────────────────────────────────────────────
variable "db_engine_version" {
  description = "RDS PostgreSQL. 17.10+ bundles pgvector 0.8.2, which has hnsw.iterative_scan."
  type        = string
  default     = "17.11"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  description = "GB. Sized for ~5 years of filings (~4.4M vector chunks, ~20 GB) plus headroom."
  type        = number
  default     = 100
}

variable "db_name" {
  type    = string
  default = "secedgar"
}

variable "db_username" {
  type    = string
  default = "sec"
}

# ── application host ────────────────────────────────────────────────────────
variable "app_instance_type" {
  description = "Graviton. Runs Neo4j + the query API; no NAT gateway required."
  type        = string
  default     = "m7g.large"
}

variable "app_root_volume_gb" {
  type    = number
  default = 100
}

variable "neo4j_password" {
  description = "Neo4j password. Supplied via TF_VAR_neo4j_password, never committed."
  type        = string
  sensitive   = true
}

variable "deepseek_api_key" {
  description = <<-EOT
    Stored in SSM Parameter Store as a SecureString; the instance reads it at
    boot via its IAM role. Supplied via TF_VAR_deepseek_api_key.
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

variable "allowed_web_origins" {
  description = "CORS origins for the query API (the Vercel deployments)."
  type        = list(string)
  default = [
    "https://trussk.com",
    "https://www.trussk.com",
  ]
}
