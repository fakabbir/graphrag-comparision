output "api_base_url" {
  description = "HTTPS API base URL. Set this as VITE_API_BASE in Vercel."
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}

output "app_public_ip" {
  description = "Elastic IP of the app host."
  value       = aws_eip.app.public_ip
}

output "ssh_command" {
  description = "SSH to the app host."
  value       = "ssh -i infra/.secrets/${var.project}-${var.environment}.pem ec2-user@${aws_eip.app.public_ip}"
}

output "rds_endpoint" {
  description = "Private RDS endpoint - reachable only from the VPC."
  value       = aws_db_instance.main.address
}

output "pg_tunnel_command" {
  description = <<-EOT
    Open a local tunnel to the private RDS instance through the app host, then
    connect to 127.0.0.1:55432 as if it were local.
  EOT
  value = join(" ", [
    "ssh -i infra/.secrets/${var.project}-${var.environment}.pem",
    "-N -L 55432:${aws_db_instance.main.address}:5432",
    "ec2-user@${aws_eip.app.public_ip}",
  ])
}

output "neo4j_bolt_url" {
  description = "Bolt endpoint for the local loader (admin CIDRs only)."
  value       = "bolt://${aws_eip.app.public_ip}:7687"
}

output "neo4j_browser_url" {
  value = "http://${aws_eip.app.public_ip}:7474"
}

output "data_bucket" {
  description = "S3 bucket for raw filings and the staged CSVs RDS imports."
  value       = aws_s3_bucket.data.id
}

output "db_password_ssm_path" {
  description = "Read with: aws ssm get-parameter --with-decryption --name <this>"
  value       = aws_ssm_parameter.db_password.name
}

output "rds_s3_import_role_arn" {
  description = "Role RDS assumes to read staged CSVs from S3."
  value       = aws_iam_role.rds_s3_import.arn
}

output "estimated_monthly_usd" {
  description = "On-demand estimate from the AWS Pricing API for ap-south-1."
  value = {
    rds_instance  = "61.32  (db.t4g.medium @ $0.084/hr)"
    rds_storage   = "9.12   (100 GB gp3)"
    ec2_instance  = "85.12  (m7g.xlarge @ $0.1166/hr)"
    ec2_storage   = "9.12   (100 GB gp3)"
    s3_cloudfront = "~0.50  (free tier at demo volume)"
    nat_gateway   = "0.00   (deliberately not used)"
    total         = "~165/month"
  }
}
