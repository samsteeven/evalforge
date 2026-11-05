# Secrets de l'application dans Secrets Manager : injectés dans les tâches au démarrage, jamais dans la définition.
# La clé Fernet (chiffrement des clés d'API des fournisseurs) : 32 octets en base64 « URL-safe ».

resource "random_bytes" "fernet" {
  length = 32
}

resource "random_password" "jwt" {
  length  = 64
  special = false
}

locals {
  amqp_endpoint = replace(aws_mq_broker.rabbitmq.instances[0].endpoints[0], "amqps://", "")
  app_secrets = {
    EVALFORGE_DATABASE_URL   = "postgresql+asyncpg://evalforge:${random_password.db.result}@${aws_db_instance.postgres.endpoint}/evalforge"
    EVALFORGE_RABBITMQ_URL   = "amqps://evalforge:${random_password.mq.result}@${local.amqp_endpoint}/"
    EVALFORGE_SECRET_KEY     = replace(replace(random_bytes.fernet.base64, "+", "-"), "/", "_")
    EVALFORGE_JWT_SECRET_KEY = random_password.jwt.result
  }
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  description             = "Secrets d'EvalForge (${var.environment})"
  recovery_window_in_days = var.production ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id     = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(local.app_secrets)
}
