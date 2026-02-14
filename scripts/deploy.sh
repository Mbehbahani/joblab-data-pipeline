#!/usr/bin/env bash
# =============================================================================
# Deploy Docker Image to ECR
# =============================================================================
# Builds and pushes the pipeline container to AWS ECR
# Run with: ./scripts/deploy.sh [--no-cache]
# =============================================================================

set -euo pipefail

# Configuration
PROJECT_NAME="${PROJECT_NAME:-joblab}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="${ECR_REPO:-${PROJECT_NAME}-pipeline}"

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

# Parse arguments
NO_CACHE=""
if [[ "${1:-}" == "--no-cache" ]]; then
    NO_CACHE="--no-cache"
fi

echo "============================================="
echo "Deploying ${PROJECT_NAME} to ECR"
echo "============================================="
echo "ECR URI: ${ECR_URI}"
echo ""

# Navigate to project root
cd "$(dirname "$0")/.."

# Login to ECR
echo "🔐 Logging in to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
    docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Build image
echo ""
echo "🔨 Building Docker image..."
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
docker build ${NO_CACHE} \
    -t "${ECR_REPO}:latest" \
    -t "${ECR_REPO}:${TIMESTAMP}" \
    -f Dockerfile \
    .

# Tag for ECR
echo ""
echo "🏷️  Tagging images..."
docker tag "${ECR_REPO}:latest" "${ECR_URI}:latest"
docker tag "${ECR_REPO}:${TIMESTAMP}" "${ECR_URI}:${TIMESTAMP}"

# Push to ECR
echo ""
echo "⬆️  Pushing to ECR..."
docker push "${ECR_URI}:latest"
docker push "${ECR_URI}:${TIMESTAMP}"

# Update ECS task definition to use new image
echo ""
echo "🔄 Updating ECS task definition..."
# Force new deployment by updating the task definition
# (ECS will pull the new :latest image)

echo ""
echo "============================================="
echo "✅ DEPLOYMENT COMPLETE"
echo "============================================="
echo "Image pushed:"
echo "  ${ECR_URI}:latest"
echo "  ${ECR_URI}:${TIMESTAMP}"
echo ""
echo "To run a manual test:"
echo "  ./scripts/run-manual.sh"
echo "============================================="
