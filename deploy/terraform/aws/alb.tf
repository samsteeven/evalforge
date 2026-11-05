# Un seul point d'entrée : l'ALB vers le front, qui relaie /api à l'API (même origine pour le navigateur).

#trivy:ignore:AWS-0053 point d'entrée public de l'application, voulu
resource "aws_lb" "main" {
  name                       = local.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.production
}

resource "aws_lb_target_group" "frontend" {
  name        = "${local.name}-front"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path    = "/healthz"
    matcher = "200"
  }
}

locals {
  https = var.certificate_arn != ""
}

# Avec un certificat, ce port ne fait que rediriger vers HTTPS ; sans certificat (démonstration sans domaine),
# il sert le front en clair, et le cookie de session n'est alors pas « secure ».
#trivy:ignore:AWS-0054 redirection vers HTTPS dès qu'un certificat est fourni
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # Avec un certificat : redirection vers HTTPS. Sans : le front en HTTP (démonstration seulement).
  default_action {
    type             = local.https ? "redirect" : "forward"
    target_group_arn = local.https ? null : aws_lb_target_group.frontend.arn

    dynamic "redirect" {
      for_each = local.https ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}
