provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      project     = "p1-governance"
      environment = "demo"
      managed-by  = "terraform"
      cost-center = "portfolio"
    }
  }
}
