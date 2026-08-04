variable "project_name" {
  type    = string
  default = "joblab"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "s3_bucket_name" {
  type    = string
  default = "joblab-pipeline-data-moha-20260126"
}

variable "ecr_repo_name" {
  type    = string
  default = "joblab-pipeline"
}

variable "ecs_cluster_name" {
  type    = string
  default = "joblab-cluster"
}

variable "ecs_task_family" {
  type    = string
  default = "joblab-pipeline-task"
}

variable "log_group_name" {
  type    = string
  default = "/ecs/joblab-pipeline"
}

variable "schedule_name" {
  type    = string
  default = "daily-joblab-2am"
}

variable "task_cpu" {
  type    = string
  default = "1024"
}

variable "task_memory" {
  type    = string
  default = "2048"
}

variable "convex_deployment_url" {
  type      = string
  sensitive = true
  default   = "https://your-deployment.convex.cloud"
}
