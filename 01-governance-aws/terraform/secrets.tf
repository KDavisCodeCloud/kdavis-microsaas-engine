resource "aws_secretsmanager_secret" "demo" {
  name                    = "governance-demo/app-secret"
  description             = "Demo secret with automated rotation"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "demo_initial" {
  secret_id     = aws_secretsmanager_secret.demo.id
  secret_string = jsonencode({
    username = "app-user"
    password = "initial-value-will-be-rotated"
  })
}

resource "aws_secretsmanager_secret_rotation" "demo" {
  secret_id           = aws_secretsmanager_secret.demo.id
  rotation_lambda_arn = aws_lambda_function.rotator.arn

  rotation_rules {
    automatically_after_days = 30
  }
}

resource "aws_lambda_permission" "allow_secretsmanager" {
  statement_id  = "AllowSecretsManagerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rotator.function_name
  principal     = "secretsmanager.amazonaws.com"
}
