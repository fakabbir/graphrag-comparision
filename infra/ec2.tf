data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# ── instance role: read secrets, read/write the data bucket, SSM Session Mgr ─
resource "aws_iam_role" "app" {
  name = "${var.project}-${var.environment}-app"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "app_inline" {
  name = "graphrag-app"
  role = aws_iam_role.app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.data.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.data.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.region}:*:parameter/${var.project}/${var.environment}/*"
      },
      # Bifrost reaches Bedrock through the instance profile, so no AWS keys are
      # written into its config. Invoke only - it never needs to manage models.
      # Inference profiles ("apac.*", "global.*") route across regions, so the
      # resource list has to cover the foundation models they front as well.
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.me.account_id}:inference-profile/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:ListFoundationModels", "bedrock:ListInferenceProfiles"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project}-${var.environment}-app"
  role = aws_iam_role.app.name
}

# ── the host ────────────────────────────────────────────────────────────────
resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.app_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  key_name               = aws_key_pair.app.key_name
  iam_instance_profile   = aws_iam_instance_profile.app.name

  root_block_device {
    volume_size           = var.app_root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    project      = var.project
    environment  = var.environment
    region       = var.region
    data_bucket  = aws_s3_bucket.data.id
    cors_origins = join(",", var.allowed_web_origins)
  })

  tags = { Name = "${var.project}-app" }

  # RDS must exist before the SSM DSN parameter the host reads at boot.
  depends_on = [aws_ssm_parameter.db_dsn, aws_ssm_parameter.neo4j_password]
}

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"
  tags     = { Name = "${var.project}-app-eip" }
}
