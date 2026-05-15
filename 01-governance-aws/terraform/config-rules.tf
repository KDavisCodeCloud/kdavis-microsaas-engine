resource "aws_s3_bucket" "config_logs" {
  bucket        = "aws-config-logs-402916653765"
  force_destroy = true
}

resource "aws_s3_bucket_ownership_controls" "config_logs" {
  bucket = aws_s3_bucket.config_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
  depends_on = [aws_s3_bucket.config_logs]
}

resource "aws_s3_bucket_acl" "config_logs" {
  bucket     = aws_s3_bucket.config_logs.id
  acl        = "private"
  depends_on = [aws_s3_bucket_ownership_controls.config_logs]
}

resource "aws_s3_bucket_policy" "config_logs" {
  bucket     = aws_s3_bucket.config_logs.id
  depends_on = [aws_s3_bucket_acl.config_logs]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSConfigBucketPermissionsCheck"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = "arn:aws:s3:::aws-config-logs-402916653765"
      },
      {
        Sid       = "AWSConfigBucketDelivery"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "arn:aws:s3:::aws-config-logs-402916653765/AWSLogs/402916653765/Config/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role" "config" {
  name = "aws-config-role-governance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "config.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_config_configuration_recorder" "main" {
  name     = "governance-recorder"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported = false
    resource_types = [
      "AWS::SecretsManager::Secret"
    ]
  }
}

resource "aws_config_delivery_channel" "main" {
  name           = "governance-delivery-channel"
  s3_bucket_name = aws_s3_bucket.config_logs.bucket
  depends_on     = [
    aws_config_configuration_recorder.main,
    aws_s3_bucket_policy.config_logs
  ]
}

resource "aws_config_configuration_recorder_status" "main" {
  name       = aws_config_configuration_recorder.main.name
  is_enabled = true
  depends_on = [aws_config_delivery_channel.main]
}

resource "aws_config_config_rule" "secrets_rotation" {
  name        = "secretsmanager-rotation-enabled"
  description = "Checks that Secrets Manager secrets have rotation enabled"

  source {
    owner             = "AWS"
    source_identifier = "SECRETSMANAGER_ROTATION_ENABLED_CHECK"
  }

  depends_on = [aws_config_configuration_recorder_status.main]
}
