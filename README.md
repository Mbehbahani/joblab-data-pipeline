# Job Market Analytics Pipeline

Automated job scraping, processing, and analytics pipeline for Operations Research & Data Science positions.

## 📊 Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DATA TRANSFORMATION PIPELINE                        │
└─────────────────────────────────────────────────────────────────────────────┘

Step 1: SCRAPING          Step 2: PREPROCESSING       Step 3: ENRICHMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
jobs.db                   jobs_processed.db           jobs_enriched.db
↓                         ↓                           ↓
Indeed + LinkedIn      →  Text cleaning, NLP       →  Taxonomy mapping
                          Language detection          Standardization
                          Duplicate detection         Job relevance scoring
                                                           ↓
                                                    ┌──────┴──────┐
                                                    ↓             ↓
                                               S3 Snapshot   Convex Push
                                               (versioned)   (incremental)
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for containerized runs)
- AWS CLI configured (for cloud deployment)

### Local Development Setup

```bash
# Clone and setup
cd "5 -Wrap"

# Install Python dependencies
pip install -r requirements.txt

# Install Node dependencies
npm install

# Create .env.local with your Convex URL
echo "NEXT_PUBLIC_CONVEX_URL=https://your-deployment.convex.cloud" > .env.local
```

## 🖥️ Run Locally

### Option 1: Run Individual Steps

```bash
# Step 1: Scraping
cd "1- Scrapped Data"
python job_scraper.py --jobs 30 --countries "USA,UK"

# Step 2: Preprocessing
cd "../2- Preprocessed"
python preprocess.py

# Step 3: Enrichment
cd "../3- Enrichment + Standardization"
python taxonomy_standardization.py

# Push to Convex
npx tsx "4- convex/seedData.ts"
```

### Option 2: Run Full Pipeline (Orchestrator)

```bash
# Full pipeline with local mode (no S3 upload)
python src/orchestrate/run_weekly.py --local

# Dry run (skip Convex push)
python src/orchestrate/run_weekly.py --local --dry-run

# Skip scraping (use existing jobs.db)
python src/orchestrate/run_weekly.py --local --skip-scrape
```

### Option 3: Run with Docker Locally

```bash
# Build image
docker build -t joblab-pipeline .

# Run with local mode
docker run --rm \
  -e DRY_RUN=false \
  -e NEXT_PUBLIC_CONVEX_URL="https://your-deployment.convex.cloud" \
  -v $(pwd):/app \
  joblab-pipeline python src/orchestrate/run_weekly.py --local

# Run dry-run mode
docker run --rm \
  -e DRY_RUN=true \
  -v $(pwd):/app \
  joblab-pipeline python src/orchestrate/run_weekly.py --local --dry-run
```

## ☁️ Run on AWS ECS (Scheduled)

### Initial Setup (One-time)

```bash
# 1. Configure AWS CLI
aws configure

# 2. Run infrastructure setup script
chmod +x scripts/*.sh
./scripts/aws-setup.sh

# 3. Update Convex URL in SSM Parameter Store
aws ssm put-parameter \
  --name "/joblab/CONVEX_DEPLOYMENT_URL" \
  --type SecureString \
  --value "https://your-deployment.convex.cloud" \
  --overwrite

# 4. Build and push Docker image
./scripts/deploy.sh
```

### Trigger Manual Run

```bash
# Normal run
./scripts/run-manual.sh

# Dry run (skip Convex push)
./scripts/run-manual.sh --dry-run
```

### Monitor Runs

```bash
# Follow logs in real-time
aws logs tail /ecs/joblab --follow

# Check task status
aws ecs describe-tasks \
  --cluster joblab-cluster \
  --tasks <TASK_ID>

# List recent runs
aws ecs list-tasks \
  --cluster joblab-cluster \
  --family joblab-weekly-task
```

## 🔧 Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `S3_BUCKET` | Yes (AWS) | - | S3 bucket for snapshots |
| `S3_PREFIX` | No | `joblab` | S3 key prefix |
| `AWS_REGION` | No | `us-east-1` | AWS region |
| `CONVEX_DEPLOYMENT_URL` | Yes | - | Convex deployment URL |
| `NEXT_PUBLIC_CONVEX_URL` | Yes* | - | Alternative Convex URL |
| `DRY_RUN` | No | `false` | Skip Convex push if `true` |
| `CLEAR_CONVEX` | No | `false` | Clear all Convex data before push |
| `JOB_DB_PATH` | No | Auto | Override path to enriched DB |
| `SCRAPE_COUNTRIES` | No | All | Comma-separated country list |
| `SCRAPE_MAX_JOBS` | No | `50` | Max jobs per country |
| `DAYS_BACK` | No | `7` | Scrape jobs from past N days |

*Either `CONVEX_DEPLOYMENT_URL` or `NEXT_PUBLIC_CONVEX_URL` must be set.

## 📦 Convex Seeding Options

```bash
# Incremental upsert (default for weekly automation)
npx tsx "4- convex/seedData.ts"

# Full reset (clear all data first)
CLEAR_CONVEX=true npx tsx "4- convex/seedData.ts"

# Dry run (no changes made)
DRY_RUN=true npx tsx "4- convex/seedData.ts"

# Custom database path
JOB_DB_PATH=/path/to/jobs_enriched.db npx tsx "4- convex/seedData.ts"
```

## 💾 Restore from Snapshot

### List Available Snapshots

```bash
# List all snapshots
aws s3 ls s3://joblab-pipeline-snapshots/joblab/snapshots/

# List with details
aws s3 ls s3://joblab-pipeline-snapshots/joblab/snapshots/ --recursive
```

### Download and Restore

```bash
# Download specific snapshot
aws s3 cp \
  s3://joblab-pipeline-snapshots/joblab/snapshots/2026-01-20T030000Z/jobs_enriched.db \
  ./restored_jobs.db

# Or download latest
aws s3 cp \
  s3://joblab-pipeline-snapshots/joblab/latest/jobs_enriched.db \
  ./restored_jobs.db

# Re-seed Convex from restored snapshot
JOB_DB_PATH=./restored_jobs.db CLEAR_CONVEX=true npx tsx "4- convex/seedData.ts"
```

## 📁 Project Structure

```
5 -Wrap/
├── 1- Scrapped Data/
│   ├── job_scraper.py  # Scraping script
│   ├── jobs.db                             # Raw scraped data
│   └── Report1.txt
├── 2- Preprocessed/
│   ├── preprocess.py                       # Text processing & NLP
│   ├── jobs_processed.db                   # Processed data
│   └── Report2.txt
├── 3- Enrichment + Standardization/
│   ├── taxonomy_standardization.py         # Taxonomy mapping
│   ├── jobs_enriched.db                    # Final enriched data
│   └── Report3.txt
├── 4- convex/
│   ├── schema.ts                           # Convex schema
│   ├── mutations.ts                        # Convex mutations (upsert)
│   ├── queries.ts                          # Convex queries
│   └── seedData.ts                         # Seeding script
├── src/
│   └── orchestrate/
│       └── run_weekly.py                   # Pipeline orchestrator
├── scripts/
│   ├── aws-setup.sh                        # AWS infrastructure setup
│   ├── deploy.sh                           # Docker build & push
│   └── run-manual.sh                       # Manual ECS task trigger
├── Dockerfile                              # Production container
├── requirements.txt                        # Python dependencies
├── package.json                            # Node dependencies
├── README.md                               # This file
└── RUNBOOK.md                              # Operations runbook
```

## 🏗️ AWS Resources Created

| Resource | Name | Description |
|----------|------|-------------|
| S3 Bucket | `joblab-pipeline-snapshots` | Versioned DB snapshots |
| ECR Repository | `joblab-pipeline` | Docker images |
| ECS Cluster | `joblab-cluster` | Fargate cluster |
| ECS Task Definition | `joblab-weekly-task` | Task config |
| CloudWatch Log Group | `/ecs/joblab` | Container logs |
| EventBridge Schedule | `joblab-weekly-schedule` | Weekly trigger |
| IAM Roles | `joblab-ecs-task-*` | Task permissions |
| SSM Parameter | `/joblab/CONVEX_DEPLOYMENT_URL` | Secrets |

## 📅 Schedule

The pipeline runs automatically every **Sunday at 03:00 UTC** via EventBridge Scheduler.

To modify the schedule:
```bash
aws scheduler update-schedule \
  --name joblab-weekly-schedule \
  --schedule-expression "cron(0 3 ? * SUN *)"
```

Common cron expressions:
- Daily at 3 AM: `cron(0 3 * * ? *)`
- Every Monday at 6 AM: `cron(0 6 ? * MON *)`
- Every 12 hours: `rate(12 hours)`

## 🔍 Troubleshooting

See [RUNBOOK.md](RUNBOOK.md) for detailed troubleshooting and operations guide.

## 📄 License

Private - Internal use only.

