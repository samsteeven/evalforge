# Deux zones : sous-réseaux publics (ALB, NAT) et privés (tâches ECS, RDS, Amazon MQ).
# Les tâches sortent par la NAT : fournisseurs de modèles, agents évalués, webhooks.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "evalforge-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  tags              = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  tags              = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat" }
}

# Une seule NAT pour limiter le coût ; une par zone en production si la sortie doit survivre à une zone.
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = local.name }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "public" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- Groupes de sécurité : chaque flux autorisé est déclaré, rien d'autre n'entre.

#trivy:ignore:AWS-0053 point d'entrée public de l'application, voulu
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "ALB public : HTTP et HTTPS depuis Internet"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP (redirigé vers HTTPS si un certificat est fourni)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_frontend" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Vers le front"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "Tâches ECS : front, API, worker"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Front depuis l'ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "tasks_api_internal" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "API depuis le front (Service Connect)"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

# Sorties des tâches : HTTPS vers l'extérieur (fournisseurs de modèles, agents évalués, webhooks, ECR, Secrets
# Manager, CloudWatch), la base, le courtier, et l'API pour le front. Un agent ou un webhook en HTTP simple est
# donc injoignable depuis AWS : voulu. Les adresses internes restent refusées par l'application (SSRF).
#trivy:ignore:AWS-0104 sortie HTTPS vers Internet nécessaire : fournisseurs et destinataires choisis par l'utilisateur
resource "aws_vpc_security_group_egress_rule" "tasks_https" {
  security_group_id = aws_security_group.tasks.id
  description       = "HTTPS sortant"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "tasks_api" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Front vers API (Service Connect)"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "tasks_postgres" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "PostgreSQL"
  referenced_security_group_id = aws_security_group.data.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "tasks_amqps" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "AMQP sur TLS"
  referenced_security_group_id = aws_security_group.data.id
  ip_protocol                  = "tcp"
  from_port                    = 5671
  to_port                      = 5671
}

resource "aws_security_group" "data" {
  name        = "${local.name}-data"
  description = "RDS et Amazon MQ : joignables par les tâches seulement"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "postgres_from_tasks" {
  security_group_id            = aws_security_group.data.id
  description                  = "PostgreSQL"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "amqps_from_tasks" {
  security_group_id            = aws_security_group.data.id
  description                  = "AMQP sur TLS"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 5671
  to_port                      = 5671
}
