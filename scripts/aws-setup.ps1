# =============================================================================
# AWS Infrastructure Setup Script for Job Pipeline (PowerShell)
# =============================================================================
# This script creates all required AWS resources for the weekly pipeline
# Run with: .\scripts\aws-setup.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

# Configuration
$PROJECT_NAME = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "joblab" }
$AWS_REGION = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$S3_BUCKET = if ($env:S3_BUCKET) { $env:S3_BUCKET } else { "joblab-pipeline-data-moha-20260126" }
$ECR_REPO = if ($env:ECR_REPO) { $env:ECR_REPO } else { "$PROJECT_NAME-pipeline" }
$ECS_CLUSTER = if ($env:ECS_CLUSTER) { $env:ECS_CLUSTER } else { "$PROJECT_NAME-cluster" }
$ECS_TASK_FAMILY = if ($env:ECS_TASK_FAMILY) { $env:ECS_TASK_FAMILY } else { "joblab-pipeline-task" }
$LOG_GROUP = if ($env:LOG_GROUP) { $env:LOG_GROUP } else { "/ecs/joblab-pipeline" }
$SCHEDULE_NAME = if ($env:SCHEDULE_NAME) { $env:SCHEDULE_NAME } else { "daily-$PROJECT_NAME-2am" }

# Task configuration
$TASK_CPU = "1024"       # 1 vCPU
$TASK_MEMORY = "2048"    # 2 GB

# Get AWS account ID
$AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

Write-Host "============================================="
Write-Host "AWS Infrastructure Setup for $PROJECT_NAME"
Write-Host "============================================="
Write-Host "Region:      $AWS_REGION"
Write-Host "Account ID:  $AWS_ACCOUNT_ID"
Write-Host "S3 Bucket:   $S3_BUCKET"
Write-Host "ECR Repo:    $ECR_REPO"
Write-Host "ECS Cluster: $ECS_CLUSTER"
Write-Host "============================================="
Write-Host ""

# -----------------------------------------------------------------------------
# 1. Create S3 Bucket for snapshots
# -----------------------------------------------------------------------------
Write-Host "📦 Creating S3 bucket: $S3_BUCKET"

try {
    aws s3api head-bucket --bucket $S3_BUCKET 2>$null
    Write-Host "   ✓ Bucket already exists"
} catch {
    if ($AWS_REGION -eq "us-east-1") {
        aws s3api create-bucket --bucket $S3_BUCKET --region $AWS_REGION
    } else {
        aws s3api create-bucket --bucket $S3_BUCKET --region $AWS_REGION --create-bucket-configuration "LocationConstraint=$AWS_REGION"
    }
    
    # Enable versioning
    aws s3api put-bucket-versioning --bucket $S3_BUCKET --versioning-configuration Status=Enabled
    
    Write-Host "   ✓ Bucket created with versioning enabled"
}

# -----------------------------------------------------------------------------
# 2. Create ECR Repository
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "🐳 Creating ECR repository: $ECR_REPO"

try {
    aws ecr describe-repositories --repository-names $ECR_REPO --region $AWS_REGION 2>$null
    Write-Host "   ✓ Repository already exists"
} catch {
    aws ecr create-repository `
        --repository-name $ECR_REPO `
        --region $AWS_REGION `
        --image-scanning-configuration scanOnPush=true `
        --encryption-configuration encryptionType=AES256
    
    Write-Host "   ✓ Repository created"
}

$ECR_URI = "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO"
Write-Host "   ECR URI: $ECR_URI"

# -----------------------------------------------------------------------------
# 3. Create CloudWatch Log Group
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "📊 Creating CloudWatch Log Group: $LOG_GROUP"

try {
    $logGroups = aws logs describe-log-groups --log-group-name-prefix $LOG_GROUP --output json | ConvertFrom-Json
    if ($logGroups.logGroups | Where-Object { $_.logGroupName -eq $LOG_GROUP }) {
        Write-Host "   ✓ Log group already exists"
    } else {
        throw "Not found"
    }
} catch {
    aws logs create-log-group --log-group-name $LOG_GROUP --region $AWS_REGION
    aws logs put-retention-policy --log-group-name $LOG_GROUP --retention-in-days 30
    Write-Host "   ✓ Log group created with 30-day retention"
}

# -----------------------------------------------------------------------------
# 4. Create IAM Roles
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "🔐 Creating IAM roles..."

$TASK_EXECUTION_ROLE = "ecsTaskExecutionRole"
$TASK_ROLE = "ecsTaskRole"

$trustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
'@

$trustPolicy | Out-File -FilePath "$env:TEMP\trust-policy.json" -Encoding ASCII

# Task Execution Role
aws iam get-role --role-name $TASK_EXECUTION_ROLE 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✓ Task execution role already exists. Updating trust policy..."
    aws iam update-assume-role-policy `
        --role-name $TASK_EXECUTION_ROLE `
        --policy-document "file://$env:TEMP\trust-policy.json"
} else {
    Write-Host "   Creating Task execution role..."
    aws iam create-role `
        --role-name $TASK_EXECUTION_ROLE `
        --assume-role-policy-document "file://$env:TEMP\trust-policy.json"
    
    aws iam attach-role-policy `
        --role-name $TASK_EXECUTION_ROLE `
        --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"

    $ssmPolicy = @"
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ssm:GetParameters",
                "secretsmanager:GetSecretValue"
            ],
            "Resource": [
                "arn:aws:ssm:${AWS_REGION}:${AWS_ACCOUNT_ID}:parameter/$PROJECT_NAME/*"
            ]
        }
    ]
}
"@
    $ssmPolicy | Out-File -FilePath "$env:TEMP\ssm-policy.json" -Encoding ASCII

    aws iam put-role-policy `
        --role-name $TASK_EXECUTION_ROLE `
        --policy-name "$PROJECT_NAME-ssm-access" `
        --policy-document "file://$env:TEMP\ssm-policy.json"
    
    Write-Host "   ✓ Task execution role created with SSM permissions"
}

# Task Role
aws iam get-role --role-name $TASK_ROLE 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✓ Task role already exists. Updating trust policy..."
    aws iam update-assume-role-policy `
        --role-name $TASK_ROLE `
        --policy-document "file://$env:TEMP\trust-policy.json"
} else {
    Write-Host "   Creating Task role..."
    aws iam create-role `
        --role-name $TASK_ROLE `
        --assume-role-policy-document "file://$env:TEMP\trust-policy.json"
    
    $s3Policy = @"
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::$S3_BUCKET",
                "arn:aws:s3:::$S3_BUCKET/*"
            ]
        }
    ]
}
"@
    
    $s3Policy | Out-File -FilePath "$env:TEMP\s3-policy.json" -Encoding ASCII
    
    aws iam put-role-policy `
        --role-name $TASK_ROLE `
        --policy-name "$PROJECT_NAME-s3-access" `
        --policy-document "file://$env:TEMP\s3-policy.json"
    
    Write-Host "   ✓ Task role created with S3 permissions"
}

# -----------------------------------------------------------------------------
# 5. Create ECS Cluster
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "🚀 Creating ECS Cluster: $ECS_CLUSTER"

$clusters = aws ecs describe-clusters --clusters $ECS_CLUSTER --output json | ConvertFrom-Json
if ($clusters.clusters | Where-Object { $_.status -eq "ACTIVE" }) {
    Write-Host "   ✓ Cluster already exists"
} else {
    aws ecs create-cluster `
        --cluster-name $ECS_CLUSTER `
        --capacity-providers FARGATE FARGATE_SPOT `
        --default-capacity-provider-strategy "capacityProvider=FARGATE_SPOT,weight=1"
    
    Write-Host "   ✓ Cluster created"
}

# -----------------------------------------------------------------------------
# 6. Create SSM Parameter (placeholder)
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "🔑 Creating SSM Parameter placeholders..."

try {
    aws ssm get-parameter --name "/$PROJECT_NAME/CONVEX_DEPLOYMENT_URL" 2>$null
    Write-Host "   ✓ /$PROJECT_NAME/CONVEX_DEPLOYMENT_URL already exists"
} catch {
    aws ssm put-parameter `
        --name "/$PROJECT_NAME/CONVEX_DEPLOYMENT_URL" `
        --description "Convex deployment URL" `
        --type "SecureString" `
        --value "https://your-deployment.convex.cloud"
    Write-Host "   ✓ /$PROJECT_NAME/CONVEX_DEPLOYMENT_URL created (update with real value!)"
}

# -----------------------------------------------------------------------------
# 7. Get VPC Configuration
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "🌐 Getting VPC configuration..."

$DEFAULT_VPC_ID = aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text
Write-Host "   Default VPC: $DEFAULT_VPC_ID"

$SUBNETS = aws ec2 describe-subnets --filters "Name=vpc-id,Values=$DEFAULT_VPC_ID" --query "Subnets[*].SubnetId" --output json | ConvertFrom-Json
$SUBNET_IDS = $SUBNETS -join ","
Write-Host "   Subnets: $SUBNET_IDS"

# Get or create security group
$SG_NAME = "$PROJECT_NAME-ecs-sg"
try {
    $SG_ID = aws ec2 describe-security-groups `
        --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$DEFAULT_VPC_ID" `
        --query "SecurityGroups[0].GroupId" --output text 2>$null
    
    if ($SG_ID -eq "None" -or -not $SG_ID) { throw "Not found" }
    Write-Host "   ✓ Security group exists: $SG_ID"
} catch {
    $SG_ID = aws ec2 create-security-group `
        --group-name $SG_NAME `
        --description "Security group for $PROJECT_NAME ECS tasks" `
        --vpc-id $DEFAULT_VPC_ID `
        --query "GroupId" --output text
    
    Write-Host "   ✓ Security group created: $SG_ID"
}

# -----------------------------------------------------------------------------
# 8. Create ECS Task Definition
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "📋 Creating ECS Task Definition..."

$taskDef = @"
{
    "family": "$ECS_TASK_FAMILY",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "$TASK_CPU",
    "memory": "$TASK_MEMORY",
    "executionRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$TASK_EXECUTION_ROLE",
    "taskRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$TASK_ROLE",
    "containerDefinitions": [
        {
            "name": "joblab-pipeline",
            "image": "${ECR_URI}:latest",
            "cpu": 0,
            "essential": true,
            "command": [
                "sh",
                "-c",
                "exec python -u src/orchestrate/run_weekly.py 2>&1"
            ],
            "environment": [
                {"name": "DRY_RUN", "value": "false"},
                {"name": "S3_BUCKET", "value": "$S3_BUCKET"},
                {"name": "S3_PREFIX", "value": "joblab"},
                {"name": "AWS_REGION", "value": "$AWS_REGION"},
                {"name": "PYTHONUNBUFFERED", "value": "1"}
            ],
            "secrets": [
                {
                    "name": "CONVEX_DEPLOYMENT_URL",
                    "valueFrom": "arn:aws:ssm:${AWS_REGION}:${AWS_ACCOUNT_ID}:parameter/joblab/CONVEX_DEPLOYMENT_URL"
                }
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": "$LOG_GROUP",
                    "awslogs-create-group": "true",
                    "awslogs-region": "$AWS_REGION",
                    "awslogs-stream-prefix": "ecs"
                }
            }
        }
    ]
}
"@

$taskDef | Out-File -FilePath "$env:TEMP\task-definition.json" -Encoding ASCII

$REGISTER_RESULT = aws ecs register-task-definition --cli-input-json "file://$env:TEMP\task-definition.json" --region $AWS_REGION --output json | ConvertFrom-Json
$LATEST_TASK_DEF_ARN = $REGISTER_RESULT.taskDefinition.taskDefinitionArn

Write-Host "   ✓ Task definition registered: $LATEST_TASK_DEF_ARN"

# -----------------------------------------------------------------------------
# 9. Create EventBridge Scheduler Role
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "⏰ Creating EventBridge Scheduler role..."

$SCHEDULER_ROLE = "eventbridge-ecs-scheduler-role"

$schedulerTrustPolicy = @'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "scheduler.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
'@

$schedulerTrustPolicy | Out-File -FilePath "$env:TEMP\scheduler-trust-policy.json" -Encoding ASCII

aws iam get-role --role-name $SCHEDULER_ROLE 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✓ Scheduler role already exists. Updating trust policy..."
    aws iam update-assume-role-policy `
        --role-name $SCHEDULER_ROLE `
        --policy-document "file://$env:TEMP\scheduler-trust-policy.json"
} else {
    Write-Host "   Creating Scheduler role..."
    aws iam create-role `
        --role-name $SCHEDULER_ROLE `
        --assume-role-policy-document "file://$env:TEMP\scheduler-trust-policy.json"
    
    $schedulerPolicy = @"
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ecs:RunTask"
            ],
            "Resource": [
                "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:task-definition/${ECS_TASK_FAMILY}:*",
                "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/$ECS_CLUSTER"
            ]
        },
        {
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": [
                "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$TASK_EXECUTION_ROLE",
                "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$TASK_ROLE"
            ]
        }
    ]
}
"@
    
    $schedulerPolicy | Out-File -FilePath "$env:TEMP\scheduler-policy.json" -Encoding ASCII
    
    aws iam put-role-policy `
        --role-name $SCHEDULER_ROLE `
        --policy-name "$PROJECT_NAME-scheduler-policy" `
        --policy-document "file://$env:TEMP\scheduler-policy.json"
    
    Write-Host "   ✓ Scheduler role created"
}

# -----------------------------------------------------------------------------
# 10. Create EventBridge Schedule
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "📅 Creating EventBridge Schedule..."

$SUBNET_ARRAY = "[`"" + ($SUBNETS -join "`",`"") + "`"]"

# Using the Task Definition ARN from the registration step above
Write-Host "   ✓ Using TaskDefinitionArn: $LATEST_TASK_DEF_ARN"

$scheduleTarget = @"
{
    "RoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$SCHEDULER_ROLE",
    "Arn": "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/$ECS_CLUSTER",
    "EcsParameters": {
        "TaskDefinitionArn": "$LATEST_TASK_DEF_ARN",
        "TaskCount": 1,
        "LaunchType": "FARGATE",
        "NetworkConfiguration": {
            "awsvpcConfiguration": {
                "Subnets": $SUBNET_ARRAY,
                "SecurityGroups": ["$SG_ID"],
                "AssignPublicIp": "ENABLED"
            }
        }
    },
    "RetryPolicy": {
        "MaximumEventAgeInSeconds": 86400,
        "MaximumRetryAttempts": 0
    }
}
"@

$scheduleTarget | Out-File -FilePath "$env:TEMP\schedule-target.json" -Encoding ASCII

try {
    aws scheduler get-schedule --name $SCHEDULE_NAME 2>$null
    Write-Host "   Updating existing schedule..."
    aws scheduler update-schedule `
        --name $SCHEDULE_NAME `
        --schedule-expression "cron(0 2 * * ? *)" `
        --schedule-expression-timezone "Europe/Berlin" `
        --flexible-time-window 'Mode=OFF' `
        --target "file://$env:TEMP\schedule-target.json" `
        --state ENABLED | Out-Null
} catch {
    Write-Host "   Creating new schedule..."
    aws scheduler create-schedule `
        --name $SCHEDULE_NAME `
        --schedule-expression "cron(0 2 * * ? *)" `
        --schedule-expression-timezone "Europe/Berlin" `
        --flexible-time-window 'Mode=OFF' `
        --target "file://$env:TEMP\schedule-target.json" `
        --state ENABLED | Out-Null
}

Write-Host "   ✓ Schedule updated: Daily at 13:00 UTC (2 AM Europe/Berlin)"

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================="
Write-Host "✅ AWS INFRASTRUCTURE SETUP COMPLETE"
Write-Host "============================================="
Write-Host ""
Write-Host "Resources created:"
Write-Host "  • S3 Bucket:       $S3_BUCKET"
Write-Host "  • ECR Repository:  $ECR_URI"
Write-Host "  • ECS Cluster:     $ECS_CLUSTER"
Write-Host "  • Task Definition: $ECS_TASK_FAMILY"
Write-Host "  • Log Group:       $LOG_GROUP"
Write-Host "  • Schedule:        $SCHEDULE_NAME (Daily 13:00 UTC / 2 AM Berlin)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Update SSM parameter with your Convex URL:"
Write-Host "     aws ssm put-parameter --name '/$PROJECT_NAME/CONVEX_DEPLOYMENT_URL' \"
Write-Host "         --type SecureString --value 'https://your-deployment.convex.cloud' --overwrite"
Write-Host ""
Write-Host "  2. Build and push Docker image:"
Write-Host "     .\scripts\deploy.ps1"
Write-Host ""
Write-Host "  3. Run a manual test:"
Write-Host "     .\scripts\run-manual.ps1"
Write-Host "============================================="
