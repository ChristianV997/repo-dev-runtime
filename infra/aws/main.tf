terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# --- Secrets: containers only. Terraform never writes a real secret value
# into these resources or into state - populate them out-of-band after
# apply (see README.md). This mirrors docs/aws-autonomous-deployment.md's
# guidance to keep GITHUB_TOKEN/OPENROUTER_API_KEY out of any committed
# or state-tracked artifact.
resource "aws_secretsmanager_secret" "github_token" {
  name = var.github_token_secret_name
  tags = var.tags
}

resource "aws_secretsmanager_secret" "openrouter_key" {
  name = var.openrouter_key_secret_name
  tags = var.tags
}

resource "aws_iam_role" "instance" {
  name = "repo-dev-runtime-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

# Least-privilege: read access to exactly the two secrets this deployment
# needs, nothing broader (no wildcard secretsmanager:* / resource "*").
resource "aws_iam_role_policy" "read_secrets" {
  name = "repo-dev-runtime-read-secrets"
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.github_token.arn,
        aws_secretsmanager_secret.openrouter_key.arn,
      ]
    }]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "repo-dev-runtime-instance"
  role = aws_iam_role.instance.name
}

resource "aws_security_group" "instance" {
  name        = "repo-dev-runtime-instance"
  description = "repo-dev-runtime autonomous-operation instance: egress only by default"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "all outbound (GitHub, OpenRouter, package registries)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = var.key_name != "" && var.allowed_ssh_cidr != "" ? [1] : []
    content {
      description = "SSH, only when a key pair and CIDR are explicitly configured"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [var.allowed_ssh_cidr]
    }
  }

  tags = var.tags
}

resource "aws_instance" "runtime" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  key_name               = var.key_name != "" ? var.key_name : null

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    runtime_pip_spec           = var.runtime_pip_spec
    target_repo_url            = var.target_repo_url
    target_repo_ref            = var.target_repo_ref
    schedule_oncalendar        = var.schedule_oncalendar
    schedule_key               = var.schedule_key
    run_prompt                 = var.run_prompt
    openrouter_model           = var.openrouter_model
    ollama_url                 = var.ollama_url
    github_token_secret_name   = var.github_token_secret_name
    openrouter_key_secret_name = var.openrouter_key_secret_name
    aws_region                 = var.aws_region
    reap_stuck_timeout_s       = var.reap_stuck_timeout_s
  })

  tags = merge(var.tags, { Name = "repo-dev-runtime" })
}
