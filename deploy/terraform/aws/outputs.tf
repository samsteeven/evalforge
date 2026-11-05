output "url" {
  description = "Adresse de l'interface"
  value       = "${local.https ? "https" : "http"}://${aws_lb.main.dns_name}"
}

output "ecr_repositories" {
  description = "Dépôts d'images où pousser evalforge-backend et evalforge-frontend"
  value       = { for key, repo in aws_ecr_repository.app : key => repo.repository_url }
}

output "migrate_command" {
  description = "Migrations à lancer avant chaque déploiement d'une nouvelle version"
  value = join(" ", [
    "aws ecs run-task --cluster ${aws_ecs_cluster.main.name}",
    "--task-definition ${aws_ecs_task_definition.migrate.family}",
    "--launch-type FARGATE",
    "--network-configuration 'awsvpcConfiguration={subnets=[${join(",", aws_subnet.private[*].id)}],securityGroups=[${aws_security_group.tasks.id}]}'",
  ])
}

output "secret_arn" {
  description = "Secrets de l'application (Secrets Manager)"
  value       = aws_secretsmanager_secret.app.arn
}
