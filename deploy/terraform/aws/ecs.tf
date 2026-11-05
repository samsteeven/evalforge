# ECS Fargate : front (derrière l'ALB), API (jointe par le front via Service Connect), worker, et une tâche de
# migration lancée avant chaque déploiement (commande dans les sorties).

resource "aws_ecr_repository" "app" {
  for_each             = toset(["backend", "frontend"])
  name                 = "evalforge-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = !var.production

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_http_namespace" "main" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(["api", "worker", "frontend", "migrate"])
  name              = "/evalforge/${var.environment}/${each.key}"
  retention_in_days = var.production ? 90 : 14
}

# --- Rôles : l'exécution (tirer l'image, lire les secrets, écrire les journaux) ; la tâche elle-même n'a besoin
# d'aucune permission AWS.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
}

resource "aws_iam_role_policy" "read_secret" {
  name   = "read-app-secret"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secret.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# --- Définitions de tâches

locals {
  backend_image  = "${aws_ecr_repository.app["backend"].repository_url}:${var.image_tag}"
  frontend_image = "${aws_ecr_repository.app["frontend"].repository_url}:${var.image_tag}"

  backend_environment = [
    { name = "EVALFORGE_COOKIE_SECURE", value = var.certificate_arn != "" ? "true" : "false" },
    { name = "EVALFORGE_RABBITMQ_PREFIX", value = "evalforge" },
    { name = "EVALFORGE_DATABASE_POOL_SIZE", value = "10" },
    { name = "FORWARDED_ALLOW_IPS", value = "*" },
  ]
  backend_secrets = [
    for key in keys(local.app_secrets) : {
      name      = key
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::"
    }
  ]
  # Comme dans les manifestes Kubernetes : racine en lecture seule, utilisateur non root de l'image.
  hardened = {
    readonlyRootFilesystem = true
    user                   = "10001"
    linuxParameters        = { initProcessEnabled = true }
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([merge(local.hardened, {
    name         = "api"
    image        = local.backend_image
    essential    = true
    portMappings = [{ name = "api", containerPort = 8000, protocol = "tcp" }]
    environment  = local.backend_environment
    secrets      = local.backend_secrets
    healthCheck = {
      command  = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
      interval = 15
      retries  = 3
      timeout  = 5
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app["api"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
  })])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([merge(local.hardened, {
    name        = "worker"
    image       = local.backend_image
    essential   = true
    command     = ["evalforge-worker"]
    environment = local.backend_environment
    secrets     = local.backend_secrets
    # Arrêt : le worker rend ses messages à RabbitMQ, un autre reprend les réponses manquantes.
    stopTimeout = 30
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app["worker"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  })])
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([merge(local.hardened, {
    name        = "migrate"
    image       = local.backend_image
    essential   = true
    command     = ["alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"]
    environment = local.backend_environment
    secrets     = local.backend_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app["migrate"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "migrate"
      }
    }
  })])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "tmp" }
  volume { name = "nginx-conf" }
  container_definitions = jsonencode([{
    name                   = "frontend"
    image                  = local.frontend_image
    essential              = true
    readonlyRootFilesystem = true
    user                   = "101"
    portMappings           = [{ name = "frontend", containerPort = 8080, protocol = "tcp" }]
    environment            = [{ name = "EVALFORGE_API_UPSTREAM", value = "http://api:8000" }]
    mountPoints = [
      { sourceVolume = "tmp", containerPath = "/tmp" },
      { sourceVolume = "nginx-conf", containerPath = "/etc/nginx/conf.d" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app["frontend"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "frontend"
      }
    }
  }])
}

# --- Services

resource "aws_ecs_service" "api" {
  name                   = "api"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.api.arn
  desired_count          = var.api_desired_count
  launch_type            = "FARGATE"
  enable_execute_command = false

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.tasks.id]
  }

  # Le front joint l'API par http://api:8000, résolu par Service Connect.
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
    service {
      port_name = "api"
      client_alias {
        port     = 8000
        dns_name = "api"
      }
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.tasks.id]
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_ecs_service" "frontend" {
  name            = "frontend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.tasks.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 8080
  }

  # Client Service Connect seulement : le front résout « api » sans exposer de service lui-même.
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}
