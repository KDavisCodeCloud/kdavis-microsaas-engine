data "archive_file" "rotator" {
  type        = "zip"
  source_file = "${path.module}/../lambda/rotator.py"
  output_path = "${path.module}/../lambda/rotator.zip"
}

resource "aws_lambda_function" "rotator" {
  filename         = data.archive_file.rotator.output_path
  function_name    = "secrets-rotator-governance-demo"
  role             = aws_iam_role.lambda_rotation.arn
  handler          = "rotator.lambda_handler"
  runtime          = "python3.12"
  source_code_hash = data.archive_file.rotator.output_base64sha256
  timeout          = 30
}

resource "aws_iam_role" "lambda_rotation" {
  name = "lambda-secrets-rotator-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_rotation" {
  name = "secrets-rotation-policy"
  role = aws_iam_role.lambda_rotation.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:UpdateSecretVersionStage"
        ]
        Resource = aws_secretsmanager_secret.demo.arn
      },
      {
        Effect   = "Allow"
        Action   = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}
