resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_actions" {
  name = "role-github-actions-governance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = [
            "repo:KDavisCodeCloud/freelance-hub:ref:refs/heads/main",
            "repo:KDavisCodeCloud/freelance-hub:pull_request"
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_terraform" {
  name = "github-actions-terraform-policy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:*",
          "lambda:*",
          "iam:*",
          "s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket",
          "dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem",
          "config:*",
          "logs:*"
        ]
        Resource = "*"
      }
    ]
  })
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}
