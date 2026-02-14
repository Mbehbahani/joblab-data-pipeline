# =============================================================================
# Deploy Docker Image to ECR (PowerShell)
# =============================================================================
# Builds and pushes the pipeline container to AWS ECR
# Run with: .\scripts\deploy.ps1 [-NoCache]
# =============================================================================

param(
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"

# Configuration
$PROJECT_NAME = if ($env:PROJECT_NAME) { $env:PROJECT_NAME } else { "joblab" }
$AWS_REGION = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$ECR_REPO = if ($env:ECR_REPO) { $env:ECR_REPO } else { "$PROJECT_NAME-pipeline" }

# Get AWS account ID
$AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
$ECR_URI = "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO"

Write-Host "============================================="
Write-Host "Deploying $PROJECT_NAME to ECR"
Write-Host "============================================="
Write-Host "ECR URI: $ECR_URI"
Write-Host ""

# Navigate to project root
Push-Location (Split-Path $PSScriptRoot -Parent)

try {
    # Login to ECR
    Write-Host "🔐 Logging in to ECR..."
    $password = aws ecr get-login-password --region $AWS_REGION
    $password | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

    # Build image
    Write-Host ""
    Write-Host "🔨 Building Docker image..."
    $TIMESTAMP = Get-Date -Format "yyyyMMdd-HHmmss"
    
    $buildArgs = @("-t", "${ECR_REPO}:latest", "-t", "${ECR_REPO}:${TIMESTAMP}", "-f", "Dockerfile", ".")
    if ($NoCache) {
        $buildArgs = @("--no-cache") + $buildArgs
    }
    
    docker build @buildArgs

    # Tag for ECR
    Write-Host ""
    Write-Host "🏷️  Tagging images..."
    docker tag "${ECR_REPO}:latest" "${ECR_URI}:latest"
    docker tag "${ECR_REPO}:${TIMESTAMP}" "${ECR_URI}:${TIMESTAMP}"

    # Push to ECR
    Write-Host ""
    Write-Host "⬆️  Pushing to ECR..."
    docker push "${ECR_URI}:latest"
    docker push "${ECR_URI}:${TIMESTAMP}"

    Write-Host ""
    Write-Host "============================================="
    Write-Host "✅ DEPLOYMENT COMPLETE"
    Write-Host "============================================="
    Write-Host "Image pushed:"
    Write-Host "  ${ECR_URI}:latest"
    Write-Host "  ${ECR_URI}:${TIMESTAMP}"
    Write-Host ""
    Write-Host "To run a manual test:"
    Write-Host "  .\scripts\run-manual.ps1"
    Write-Host "============================================="
}
finally {
    Pop-Location
}
