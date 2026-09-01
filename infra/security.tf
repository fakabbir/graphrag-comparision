# ── app host ────────────────────────────────────────────────────────────────
resource "aws_security_group" "app" {
  name        = "${var.project}-app-sg"
  description = "GraphRAG app host: public API, admin-only SSH and Neo4j browser"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project}-app-sg" }
}

# Port 80 is NOT open to the world. TLS terminates at CloudFront, and only the
# CloudFront origin-facing prefix list may reach the origin (see cloudfront.tf).
# Admins additionally get :80 for direct debugging.
resource "aws_vpc_security_group_ingress_rule" "app_http_admin" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "HTTP direct - admin debugging only"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_ssh" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "SSH - admin only. Also the tunnel used to load RDS."
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_neo4j_browser" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "Neo4j Browser - admin only, never public"
  cidr_ipv4         = each.value
  from_port         = 7474
  to_port           = 7474
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_neo4j_bolt" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "Bolt - admin only, used by the local loader"
  cidr_ipv4         = each.value
  from_port         = 7687
  to_port           = 7687
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_all" {
  security_group_id = aws_security_group.app.id
  description       = "Outbound: package repos, S3, the DeepSeek API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ── database ────────────────────────────────────────────────────────────────
# Not publicly accessible. Reachable only from the app host; the local loader
# gets in over an SSH tunnel through that host.
resource "aws_security_group" "db" {
  name        = "${var.project}-db-sg"
  description = "Postgres: reachable only from the app host"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project}-db-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the app host security group"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
