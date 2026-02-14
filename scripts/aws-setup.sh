#!/usr/bin/env bash
# =============================================================================
# AWS Infrastructure Setup Script for Job Pipeline
# =============================================================================
# This script creates all required AWS resources for the weekly pipeline
# Run with: ./scripts/aws-setup.sh
# =============================================================================

set -euo pipefail

# Configuration
PROJECT_NAME="${PROJECT_NAME:-joblab}"
AWS_REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${S3_BUCKET:-joblab-pipeline-data-moha-20260126}"
ECR_REPO="${ECR_REPO:-${PROJECT_NAME}-pipeline}"
ECS_CLUSTER="${ECS_CLUSTER:-${PROJECT_NAME}-cluster}"
ECS_TASK_FAMILY="${ECS_TASK_FAMILY:-joblab-pipeline-task}"
LOG_GROUP="${LOG_GROUP:-/ecs/joblab-pipeline}"
SCHEDULE_NAME="${SCHEDULE_NAME:-daily-${PROJECT_NAME}-2am}"

# Task configuration
TASK_CPU="${TASK_CPU:-1024}"       # 1 vCPU
TASK_MEMORY="${TASK_MEMORY:-2048}" # 2 GB

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "============================================="
echo "AWS Infrastructure Setup for ${PROJECT_NAME}"
echo "============================================="
echo "Region:      ${AWS_REGION}"
echo "Account ID:  ${AWS_ACCOUNT_ID}"
echo "S3 Bucket:   ${S3_BUCKET}"
echo "ECR Repo:    ${ECR_REPO}"
echo "ECS Cluster: ${ECS_CLUSTER}"
echo "============================================="
echo ""

# -----------------------------------------------------------------------------
# 1. Create S3 Bucket for snapshots
# -----------------------------------------------------------------------------
echo "📦 Creating S3 bucket: ${S3_BUCKET}"

if aws s3api head-bucket --bucket "${S3_BUCKET}" 2>/dev/null; then
    echo "   ✓ Bucket already exists"
else
    aws s3api create-bucket \
        --bucket "${S3_BUCKET}" \
        --region "${AWS_REGION}" \
        $([ "${AWS_REGION}" != "us-east-1" ] && echo "--create-bucket-configuration LocationConstraint=${AWS_REGION}") \
        || true
    
    # Enable versioning for safety
    aws s3api put-bucket-versioning \
        --bucket "${S3_BUCKET}" \
        --versioning-configuration Status=Enabled
    
    echo "   ✓ Bucket created with versioning enabled"
fi

# -----------------------------------------------------------------------------
# 2. Create ECR Repository
# -----------------------------------------------------------------------------
echo ""
echo "🐳 Creating ECR repository: ${ECR_REPO}"

if aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${AWS_REGION}" 2>/dev/null; then
    echo "   ✓ Repository already exists"
else
    aws ecr create-repository \
        --repository-name "${ECR_REPO}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --encryption-configuration encryptionType=AES256
    
    echo "   ✓ Repository created"
fi

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
echo "   ECR URI: ${ECR_URI}"

# -----------------------------------------------------------------------------
# 3. Create CloudWatch Log Group
# -----------------------------------------------------------------------------
echo ""
echo "📊 Creating CloudWatch Log Group: ${LOG_GROUP}"

if aws logs describe-log-groups --log-group-name-prefix "${LOG_GROUP}" --query "logGroups[?logGroupName=='${LOG_GROUP}']" --output text | grep -q "${LOG_GROUP}"; then
    echo "   ✓ Log group already exists"
else
    aws logs create-log-group \
        --log-group-name "${LOG_GROUP}" \
        --region "${AWS_REGION}"
    
    # Set retention to 30 days
    aws logs put-retention-policy \
        --log-group-name "${LOG_GROUP}" \
        --retention-in-days 30
    
    echo "   ✓ Log group created with 30-day retention"
fi

# -----------------------------------------------------------------------------
# 4. Create IAM Role for ECS Task Execution
# -----------------------------------------------------------------------------
echo ""
echo "🔐 Creating IAM roles..."

TASK_EXECUTION_ROLE="ecsTaskExecutionRole"
TASK_ROLE="ecsTaskRole"

# Task Execution Role (for pulling images, writing logs)
cat > /tmp/trust-policy.json << EOF
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
EOF

if aws iam get-role --role-name "${TASK_EXECUTION_ROLE}" 2>/dev/null; then
    echo "   ✓ Task execution role already exists. Updating trust policy..."
    aws iam update-assume-role-policy \
        --role-name "${TASK_EXECUTION_ROLE}" \
        --policy-document file:///tmp/trust-policy.json
else
    aws iam create-role \
        --role-name "${TASK_EXECUTION_ROLE}" \
        --assume-role-policy-document file:///tmp/trust-policy.json
    
    aws iam attach-role-policy \
        --role-name "${TASK_EXECUTION_ROLE}" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

    cat <<EOF > /tmp/ssm-policy.json
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
                "arn:aws:ssm:${AWS_REGION}:${AWS_ACCOUNT_ID}:parameter/${PROJECT_NAME}/*"
            ]
        }
    ]
}
EOF

    aws iam put-role-policy \
        --role-name "${TASK_EXECUTION_ROLE}" \
        --policy-name "${PROJECT_NAME}-ssm-access" \
        --policy-document file:///tmp/ssm-policy.json
    
    echo "   ✓ Task execution role created with SSM permissions"
fi

# Task Role (for S3 access from within the container)
if aws iam get-role --role-name "${TASK_ROLE}" 2>/dev/null; then
    echo "   ✓ Task role already exists. Updating trust policy..."
    aws iam update-assume-role-policy \
        --role-name "${TASK_ROLE}" \
        --policy-document file:///tmp/trust-policy.json
else
    aws iam create-role \
        --role-name "${TASK_ROLE}" \
        --assume-role-policy-document file:///tmp/trust-policy.json
    
    # Create policy for S3 access
    cat > /tmp/s3-policy.json << EOF
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
                "arn:aws:s3:::${S3_BUCKET}",
                "arn:aws:s3:::${S3_BUCKET}/*"
            ]
        }
    ]
}
EOF
    
    aws iam put-role-policy \
        --role-name "${TASK_ROLE}" \
        --policy-name "${PROJECT_NAME}-s3-access" \
        --policy-document file:///tmp/s3-policy.json
    
    echo "   ✓ Task role created with S3 permissions"
fi

# -----------------------------------------------------------------------------
# 5. Create ECS Cluster
# -----------------------------------------------------------------------------
echo ""
echo "🚀 Creating ECS Cluster: ${ECS_CLUSTER}"

if aws ecs describe-clusters --clusters "${ECS_CLUSTER}" --query "clusters[?status=='ACTIVE']" --output text | grep -q "${ECS_CLUSTER}"; then
    echo "   ✓ Cluster already exists"
else
    aws ecs create-cluster \
        --cluster-name "${ECS_CLUSTER}" \
        --capacity-providers FARGATE FARGATE_SPOT \
        --default-capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1
    
    echo "   ✓ Cluster created"
fi

# -----------------------------------------------------------------------------
# 6. Create Secrets in SSM Parameter Store (placeholders)
# -----------------------------------------------------------------------------
echo ""
echo "🔑 Creating SSM Parameter placeholders..."

# Create parameter if it doesn't exist
create_parameter() {
    local name=$1
    local description=$2
    local default_value=$3
    
    if aws ssm get-parameter --name "${name}" 2>/dev/null; then
        echo "   ✓ ${name} already exists"
    else
        aws ssm put-parameter \
            --name "${name}" \
            --description "${description}" \
            --type "SecureString" \
            --value "${default_value}"
        echo "   ✓ ${name} created (update with real value!)"
    fi
}

create_parameter "/${PROJECT_NAME}/CONVEX_DEPLOYMENT_URL" "Convex deployment URL" "https://your-deployment.convex.cloud"

# -----------------------------------------------------------------------------
# 7. Create ECS Task Definition
# -----------------------------------------------------------------------------
echo ""
echo "📋 Creating ECS Task Definition..."

cat > /tmp/task-definition.json << EOF
{
    "family": "${ECS_TASK_FAMILY}",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "${TASK_CPU}",
    "memory": "${TASK_MEMORY}",
    "executionRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${TASK_EXECUTION_ROLE}",
    "taskRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${TASK_ROLE}",
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
                {"name": "S3_BUCKET", "value": "${S3_BUCKET}"},
                {"name": "S3_PREFIX", "value": "joblab"},
                {"name": "AWS_REGION", "value": "${AWS_REGION}"},
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
                    "awslogs-group": "${LOG_GROUP}",
                    "awslogs-create-group": "true",
                    "awslogs-region": "${AWS_REGION}",
                    "awslogs-stream-prefix": "ecs"
                }
            }
        }
    ]
}
EOF

aws ecs register-task-definition \
    --cli-input-json file:///tmp/task-definition.json \
    --region "${AWS_REGION}"

echo "   ✓ Task definition registered"

# -----------------------------------------------------------------------------
# 8. Get Default VPC and Subnets
# -----------------------------------------------------------------------------
echo ""
echo "🌐 Getting VPC configuration..."

DEFAULT_VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)
echo "   Default VPC: ${DEFAULT_VPC_ID}"

SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=${DEFAULT_VPC_ID}" \
    --query "Subnets[*].SubnetId" \
    --output text | tr '\t' ',')
echo "   Subnets: ${SUBNET_IDS}"

# Get or create security group
SG_NAME="${PROJECT_NAME}-ecs-sg"
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${SG_NAME}" "Name=vpc-id,Values=${DEFAULT_VPC_ID}" \
    --query "SecurityGroups[0].GroupId" \
    --output text 2>/dev/null || echo "None")

if [ "${SG_ID}" == "None" ] || [ -z "${SG_ID}" ]; then
    SG_ID=$(aws ec2 create-security-group \
        --group-name "${SG_NAME}" \
        --description "Security group for ${PROJECT_NAME} ECS tasks" \
        --vpc-id "${DEFAULT_VPC_ID}" \
        --query "GroupId" \
        --output text)
    
    # Allow outbound traffic (required for scraping and S3/Convex access)
    # Inbound not needed for Fargate tasks that don't expose services
    echo "   ✓ Security group created: ${SG_ID}"
else
    echo "   ✓ Security group exists: ${SG_ID}"
fi

# -----------------------------------------------------------------------------
# 9. Create EventBridge Scheduler Role
# -----------------------------------------------------------------------------
echo ""
echo "⏰ Creating EventBridge Scheduler role..."

SCHEDULER_ROLE="${PROJECT_NAME}-scheduler-role"

cat > /tmp/scheduler-trust-policy.json << EOF
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
EOF

if aws iam get-role --role-name "${SCHEDULER_ROLE}" 2>/dev/null; then
    echo "   ✓ Scheduler role already exists. Updating trust policy..."
    aws iam update-assume-role-policy \
        --role-name "${SCHEDULER_ROLE}" \
        --policy-document file:///tmp/scheduler-trust-policy.json
else
    aws iam create-role \
        --role-name "${SCHEDULER_ROLE}" \
        --assume-role-policy-document file:///tmp/scheduler-trust-policy.json
    
    # Policy to run ECS tasks
    cat > /tmp/scheduler-policy.json << EOF
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
                "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/${ECS_CLUSTER}"
            ]
        },
        {
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": [
                "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${TASK_EXECUTION_ROLE}",
                "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${TASK_ROLE}"
            ]
        }
    ]
}
EOF
    
    aws iam put-role-policy \
        --role-name "${SCHEDULER_ROLE}" \
        --policy-name "${PROJECT_NAME}-scheduler-policy" \
        --policy-document file:///tmp/scheduler-policy.json
    
    echo "   ✓ Scheduler role created"
fi

# -----------------------------------------------------------------------------
# 10. Create EventBridge Schedule (Weekly - Sundays 3 AM UTC)
# -----------------------------------------------------------------------------
echo ""
echo "📅 Creating EventBridge Schedule..."

# Convert subnet list to JSON array
SUBNET_ARRAY=$(echo "[\"$(echo ${SUBNET_IDS} | sed 's/,/","/g')\"]")

cat > /tmp/schedule-target.json << EOF
{
    "RoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${SCHEDULER_ROLE}",
    "Arn": "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/${ECS_CLUSTER}",
    "EcsParameters": {
        "TaskDefinitionArn": "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:task-definition/${ECS_TASK_FAMILY}",
        "TaskCount": 1,
        "LaunchType": "FARGATE",
        "NetworkConfiguration": {
            "awsvpcConfiguration": {
                "Subnets": ${SUBNET_ARRAY},
                "SecurityGroups": ["${SG_ID}"],
                "AssignPublicIp": "ENABLED"
            }
        }
    },
    "RetryPolicy": {
        "MaximumEventAgeInSeconds": 3600,
        "MaximumRetryAttempts": 2
    }
}
EOF

# Check if schedule exists
if aws scheduler get-schedule --name "${SCHEDULE_NAME}" 2>/dev/null; then
    echo "   Updating existing schedule..."
    aws scheduler update-schedule \
        --name "${SCHEDULE_NAME}" \
        --schedule-expression "cron(0 2 * * ? *)" \
        --schedule-expression-timezone "UTC" \
        --flexible-time-window '{"Mode": "OFF"}' \
        --target file:///tmp/schedule-target.json \
        --state ENABLED
else
    echo "   Creating new schedule..."
    aws scheduler create-schedule \
        --name "${SCHEDULE_NAME}" \
        --schedule-expression "cron(0 2 * * ? *)" \
        --schedule-expression-timezone "UTC" \
        --target file:///tmp/schedule-target.json \
        --state ENABLED
fi

echo "   ✓ Schedule created: Sundays at 03:00 UTC"

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo ""
echo "============================================="
echo "✅ AWS INFRASTRUCTURE SETUP COMPLETE"
echo "============================================="
echo ""
echo "Resources created:"
echo "  • S3 Bucket:      ${S3_BUCKET}"
echo "  • ECR Repository: ${ECR_URI}"
echo "  • ECS Cluster:    ${ECS_CLUSTER}"
echo "  • Task Definition: ${ECS_TASK_FAMILY}"
echo "  • Log Group:      ${LOG_GROUP}"
echo "  • Schedule:       ${SCHEDULE_NAME} (Sundays 03:00 UTC)"
echo ""
echo "Next steps:"
echo "  1. Update SSM parameter with your Convex URL:"
echo "     aws ssm put-parameter --name '/${PROJECT_NAME}/CONVEX_DEPLOYMENT_URL' \\"
echo "         --type SecureString --value 'https://your-deployment.convex.cloud' --overwrite"
echo ""
echo "  2. Build and push Docker image:"
echo "     ./scripts/deploy.sh"
echo ""
echo "  3. Run a manual test:"
echo "     ./scripts/run-manual.sh"
echo ""
echo "============================================="
