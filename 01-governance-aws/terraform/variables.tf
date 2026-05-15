variable "aws_region"   { default = "us-east-1" }
variable "project_name" { default = "governance-demo" }

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
