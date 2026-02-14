# =============================================================================
# Run Manual ECS Task (PowerShell)
# =============================================================================
# Triggers a one-off run of the pipeline on ECS Fargate
# Run with: .\scripts\run-manual.ps1 [-DryRun]
# =============================================================================

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Configuration
$PROJECT_NAME = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "joblab" }
$AWS_REGION = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$ECS_CLUSTER = if ($env:ECS_CLUSTER) { $env:ECS_CLUSTER } else { "$PROJECT_NAME-cluster" }
$ECS_TASK_FAMILY = if ($env:ECS_TASK_FAMILY) { $env:ECS_TASK_FAMILY } else { "$PROJECT_NAME-pipeline-task" }

Write-Host "============================================="
Write-Host "Running Manual ECS Task"
Write-Host "============================================="
Write-Host "Cluster: $ECS_CLUSTER"
Write-Host "Task:    $ECS_TASK_FAMILY"
if ($DryRun) {
    Write-Host "Mode:    DRY RUN (no Convex push)"
}
Write-Host ""

# Get AWS account ID
$AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

# Get VPC configuration
$DEFAULT_VPC_ID = aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text
$SUBNET_ID = aws ec2 describe-subnets `
    --filters "Name=vpc-id,Values=$DEFAULT_VPC_ID" `
    --query "Subnets[0].SubnetId" --output text
$SG_ID = aws ec2 describe-security-groups `
    --filters "Name=group-name,Values=$PROJECT_NAME-ecs-sg" "Name=vpc-id,Values=$DEFAULT_VPC_ID" `
    --query "SecurityGroups[0].GroupId" --output text

# Get latest task definition ARN
$TASK_DEF_ARN = aws ecs describe-task-definition `
    --task-definition $ECS_TASK_FAMILY `
    --query "taskDefinition.taskDefinitionArn" --output text

Write-Host "Task Definition: $TASK_DEF_ARN"
Write-Host "VPC: $DEFAULT_VPC_ID"
Write-Host "Subnet: $SUBNET_ID"
Write-Host "Security Group: $SG_ID"
Write-Host ""

# Build overrides
$overridesJson = "{}"
if ($DryRun) {
    $overridesJson = @"
{"containerOverrides": [{"name": "joblab-pipeline", "environment": [{"name": "DRY_RUN", "value": "true"}]}]}
"@
}

# Network configuration
$networkConfig = "awsvpcConfiguration={subnets=[$SUBNET_ID],securityGroups=[$SG_ID],assignPublicIp=ENABLED}"

# Run task
Write-Host "🚀 Starting ECS task..."
$result = aws ecs run-task `
    --cluster $ECS_CLUSTER `
    --task-definition $TASK_DEF_ARN `
    --launch-type FARGATE `
    --network-configuration $networkConfig `
    --overrides $overridesJson `
    --output json | ConvertFrom-Json

$TASK_ARN = $result.tasks[0].taskArn
$TASK_ID = ($TASK_ARN -split "/")[-1]

Write-Host ""
Write-Host "============================================="
Write-Host "✅ TASK STARTED"
Write-Host "============================================="
Write-Host "Task ARN: $TASK_ARN"
Write-Host "Task ID:  $TASK_ID"
Write-Host ""
Write-Host "Monitor logs:"
Write-Host "  aws logs tail /ecs/$PROJECT_NAME --follow"
Write-Host ""
Write-Host "Check task status:"
Write-Host "  aws ecs describe-tasks --cluster $ECS_CLUSTER --tasks $TASK_ID"
Write-Host ""
Write-Host "View in AWS Console:"
Write-Host "  https://$AWS_REGION.console.aws.amazon.com/ecs/v2/clusters/$ECS_CLUSTER/tasks/$TASK_ID"
Write-Host "============================================="
