# PostgreSQL (RDS) et RabbitMQ (Amazon MQ), dans les sous-réseaux privés, chiffrés au repos.
# Les mots de passe sont générés ici et ne sortent que vers Secrets Manager.

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "mq" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = var.postgres_version
  instance_class               = var.db_instance_class
  allocated_storage            = 20
  max_allocated_storage        = 100
  storage_encrypted            = true
  db_name                      = "evalforge"
  username                     = "evalforge"
  password                     = random_password.db.result
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.data.id]
  multi_az                     = var.production
  backup_retention_period      = var.production ? 14 : 1
  deletion_protection          = var.production
  skip_final_snapshot          = !var.production
  final_snapshot_identifier    = var.production ? "${local.name}-final" : null
  auto_minor_version_upgrade   = true
  performance_insights_enabled = true
}

resource "aws_mq_broker" "rabbitmq" {
  broker_name                = local.name
  engine_type                = "RabbitMQ"
  engine_version             = var.rabbitmq_version
  host_instance_type         = var.mq_instance_type
  deployment_mode            = var.production ? "CLUSTER_MULTI_AZ" : "SINGLE_INSTANCE"
  subnet_ids                 = var.production ? aws_subnet.private[*].id : [aws_subnet.private[0].id]
  security_groups            = [aws_security_group.data.id]
  publicly_accessible        = false
  auto_minor_version_upgrade = true

  user {
    username = "evalforge"
    password = random_password.mq.result
  }

  logs {
    general = true
  }
}
