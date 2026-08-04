output "cluster_name" {
  value = aws_ecs_cluster.pipeline.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.pipeline.arn
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.pipeline.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.pipeline.name
}

output "schedule_name" {
  value = aws_scheduler_schedule.pipeline.name
}

output "s3_bucket_name" {
  value = aws_s3_bucket.pipeline.bucket
}

output "ecr_repository_uri" {
  value = aws_ecr_repository.pipeline.repository_url
}
