output "instance_id" {
  description = "EC2 instance ID running the scheduled repo-dev-runtime timer."
  value       = aws_instance.runtime.id
}

output "instance_public_ip" {
  description = "Public IP, if the default subnet assigns one. Empty when SSH access is disabled and the instance has no public IP."
  value       = aws_instance.runtime.public_ip
}

output "github_token_secret_arn" {
  description = "Secrets Manager ARN to populate with a real GITHUB_TOKEN (repo scope) after apply - see README.md."
  value       = aws_secretsmanager_secret.github_token.arn
}

output "openrouter_key_secret_arn" {
  description = "Secrets Manager ARN to populate with a real OPENROUTER_API_KEY after apply - see README.md."
  value       = aws_secretsmanager_secret.openrouter_key.arn
}
