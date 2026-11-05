variable "region" {
  description = "Région AWS"
  type        = string
  default     = "eu-west-3"
}

variable "environment" {
  description = "Nom de l'environnement (préfixe des ressources)"
  type        = string
  default     = "staging"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.environment))
    error_message = "Minuscules, chiffres et tirets, 2 à 21 caractères."
  }
}

variable "vpc_cidr" {
  description = "Plage d'adresses du VPC"
  type        = string
  default     = "10.40.0.0/16"
}

variable "image_tag" {
  description = "Étiquette des images à déployer (par exemple le SHA du commit construit par la CI)"
  type        = string
  default     = "latest"
}

variable "certificate_arn" {
  description = "Certificat ACM pour HTTPS sur l'ALB ; vide, l'ALB n'écoute qu'en HTTP (démonstration seulement)"
  type        = string
  default     = ""
}

variable "api_desired_count" {
  description = "Nombre de tâches API"
  type        = number
  default     = 2
}

variable "worker_desired_count" {
  description = "Nombre de tâches worker"
  type        = number
  default     = 2
}

variable "db_instance_class" {
  description = "Classe de l'instance RDS PostgreSQL"
  type        = string
  default     = "db.t4g.small"
}

variable "postgres_version" {
  description = "Version majeure de PostgreSQL sur RDS"
  type        = string
  default     = "17"
}

variable "mq_instance_type" {
  description = "Type d'instance Amazon MQ (RabbitMQ)"
  type        = string
  default     = "mq.t3.micro"
}

variable "rabbitmq_version" {
  description = "Version de RabbitMQ sur Amazon MQ"
  type        = string
  default     = "3.13"
}

variable "production" {
  description = "Vrai : RDS multi-AZ, protection contre la suppression, instantané final, courtier en cluster"
  type        = bool
  default     = false
}
