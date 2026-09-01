resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${var.project}-db-subnets" }
}

resource "aws_db_parameter_group" "main" {
  name   = "${var.project}-${var.environment}-pg17"
  family = "postgres17"

  # HNSW index builds are memory-hungry. The 2.6M-row build is far faster with
  # headroom here, and this instance has 4 GB.
  parameter {
    name  = "maintenance_work_mem"
    value = "1048576" # kB = 1 GB
  }

  parameter {
    name  = "max_parallel_maintenance_workers"
    value = "2"
  }

  # Surface slow queries without drowning the log.
  parameter {
    name  = "log_min_duration_statement"
    value = "2000" # ms
  }

  lifecycle {
    create_before_destroy = true
  }
}

# IAM role that lets RDS read the staged CSVs from S3 (aws_s3 extension).
resource "aws_iam_role" "rds_s3_import" {
  name = "${var.project}-${var.environment}-rds-s3-import"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "rds_s3_import" {
  name = "s3-read"
  role = aws_iam_role.rds_s3_import.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.data.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.data.arn
      },
    ]
  })
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project}-${var.environment}"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 3 # autoscale headroom
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false

  multi_az                   = false # demo; flip for production
  backup_retention_period    = 7
  backup_window              = "18:00-19:00" # ~23:30 IST, off-peak
  maintenance_window         = "sun:19:30-sun:20:30"
  auto_minor_version_upgrade = true

  performance_insights_enabled    = true
  enabled_cloudwatch_logs_exports = ["postgresql"]

  # Demo posture: allow `terraform destroy` to actually work.
  deletion_protection = false
  skip_final_snapshot = true
  apply_immediately   = true

  tags = { Name = "${var.project}-postgres" }
}

# Attach the S3-import role after the instance exists.
resource "aws_db_instance_role_association" "s3_import" {
  db_instance_identifier = aws_db_instance.main.identifier
  feature_name           = "s3Import"
  role_arn               = aws_iam_role.rds_s3_import.arn
}
