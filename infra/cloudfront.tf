# The frontend is served over HTTPS from Vercel, so the API must be HTTPS too or
# browsers block it as mixed content. CloudFront gives us a valid certificate on a
# *.cloudfront.net name with no DNS record to create and no ACM cert to validate -
# and it stays inside the free tier at demo volume.

resource "aws_cloudfront_distribution" "api" {
  enabled      = true
  comment      = "${var.project} query API - TLS termination for the Vercel frontend"
  price_class  = "PriceClass_100"
  http_version = "http2and3"

  origin {
    origin_id   = "app-ec2"
    domain_name = aws_eip.app.public_dns

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only" # edge->origin inside AWS; see README
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # LLM calls can take ~10s
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "app-ec2"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # AWS managed policies: never cache API responses, forward what the API needs.
    cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id   = "216adef6-5c7f-47e4-b989-5492eafa07d3" # AllViewer
    response_headers_policy_id = "5cc3b908-e619-4b99-88e5-2cf7f45965bd" # CORS-With-Preflight
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  tags = { Name = "${var.project}-api-cdn" }
}

# Let CloudFront edge nodes reach the origin on :80 without opening it to the world.
data "aws_ec2_managed_prefix_list" "cloudfront_origin" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc_security_group_ingress_rule" "app_http_cloudfront" {
  security_group_id = aws_security_group.app.id
  description       = "HTTP from CloudFront origin-facing ranges"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin.id
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}
