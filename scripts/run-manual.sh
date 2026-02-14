#!/usr/bin/env bash
# =============================================================================
# Run Manual ECS Task
# =============================================================================
# Triggers a one-off run of the pipeline on ECS Fargate
# Run with: ./scripts/run-manual.sh [--dry-run]
# =============================================================================

set -euo pipefail

# Configuration
PROJECT_NAME="${PROJECT_NAME:-joblab}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECS_CLUSTER="${ECS_CLUSTER:-${PROJECT_NAME}-cluster}"
ECS_TASK_FAMILY="${ECS_TASK_FAMILY:-${PROJECT_NAME}-weekly-task}"

# Parse arguments
DRY_RUN_OVERRIDE=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN_OVERRIDE='{"name": "DRY_RUN", "value": "true"}'
fi

echo "============================================="
echo "Running Manual ECS Task"
echo "============================================="
echo "Cluster: ${ECS_CLUSTER}"
echo "Task:    ${ECS_TASK_FAMILY}"
if [[ -n "${DRY_RUN_OVERRIDE}" ]]; then
    echo "Mode:    DRY RUN (no Convex push)"
fi
echo ""

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Get VPC configuration
DEFAULT_VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)
SUBNET_ID=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=${DEFAULT_VPC_ID}" \
    --query "Subnets[0].SubnetId" \
    --output text)
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${PROJECT_NAME}-ecs-sg" "Name=vpc-id,Values=${DEFAULT_VPC_ID}" \
    --query "SecurityGroups[0].GroupId" \
    --output text)

# Get latest task definition ARN
TASK_DEF_ARN=$(aws ecs describe-task-definition \
    --task-definition "${ECS_TASK_FAMILY}" \
    --query "taskDefinition.taskDefinitionArn" \
    --output text)

echo "Task Definition: ${TASK_DEF_ARN}"
echo "VPC: ${DEFAULT_VPC_ID}"
echo "Subnet: ${SUBNET_ID}"
echo "Security Group: ${SG_ID}"
echo ""

# Build overrides if dry-run
OVERRIDES="{}"
if [[ -n "${DRY_RUN_OVERRIDE}" ]]; then
    OVERRIDES="{\"containerOverrides\": [{\"name\": \"${PROJECT_NAME}-container\", \"environment\": [${DRY_RUN_OVERRIDE}]}]}"
fi

# Run task
echo "🚀 Starting ECS task..."
TASK_ARN=$(aws ecs run-task \
    --cluster "${ECS_CLUSTER}" \
    --task-definition "${TASK_DEF_ARN}" \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_ID}],securityGroups=[${SG_ID}],assignPublicIp=ENABLED}" \
    --overrides "${OVERRIDES}" \
    --query "tasks[0].taskArn" \
    --output text)

TASK_ID=$(echo "${TASK_ARN}" | rev | cut -d'/' -f1 | rev)

echo ""
echo "============================================="
echo "✅ TASK STARTED"
echo "============================================="
echo "Task ARN: ${TASK_ARN}"
echo "Task ID:  ${TASK_ID}"
echo ""
echo "Monitor logs:"
echo "  aws logs tail /ecs/${PROJECT_NAME} --follow"
echo ""
echo "Check task status:"
echo "  aws ecs describe-tasks --cluster ${ECS_CLUSTER} --tasks ${TASK_ID}"
echo ""
echo "View in AWS Console:"
echo "  https://${AWS_REGION}.console.aws.amazon.com/ecs/v2/clusters/${ECS_CLUSTER}/tasks/${TASK_ID}"
echo "============================================="
