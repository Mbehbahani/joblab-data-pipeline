# Job Market Pipeline [job.oploy.eu](https://job.oploy.eu)

<p align="center">
  <a href="https://job.oploy.eu">
    <img src="./Readme/job-market-pipeline.jpg" alt="Job Market Pipeline workflow illustration" width="58%" />
  </a>
</p>

Python **data pipeline** for scraping, filtering, normalizing, deduplicating, and exporting job-market data, deployed as a **daily scheduled AWS Fargate task** provisioned with **Terraform**.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Stage%20Storage-003B57?logo=sqlite&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-Export-3ECF8E?logo=supabase&logoColor=white)
![AWS Fargate](https://img.shields.io/badge/AWS-ECS%20Fargate-FF9900?logo=amazon-aws&logoColor=white)
![Terraform](https://img.shields.io/badge/Terraform-IaC-7B42BC?logo=terraform&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)

> ⭐ If this project is useful for your work or research, consider starring it.

## Overview

This is the ingestion layer for the **JobLab** product family: a general-purpose job-scraping **data pipeline**, currently tuned to Optimization / Operations Research / Data Science roles as a concrete example domain. It runs unattended, once a day, on **AWS ECS Fargate**, and feeds a Supabase Postgres database consumed by [joblab-analytics-frontend](https://github.com/Mbehbahani/joblab-analytics-frontend) and [joblab-agent-api](https://github.com/Mbehbahani/joblab-agent-api).

The pipeline is built around a practical retrieval principle:

- **Recall** — cast a wide net with broad title/query keywords so relevant postings aren't missed.
- **Precision** — validate with description-level technical keywords and negative-keyword exclusions so the final dataset stays clean.

This matters because job titles are noisy: a role requiring mathematical optimization skill might be titled `Decision Scientist`, `Supply Chain Analyst`, or `Applied Scientist` as often as `Operations Research Scientist`.

**This repository is best described as:**

- A **6-stage** pipeline: scrape → preprocess → enrich/standardize → S3 snapshot → Supabase export → dedupe
- A **daily-scheduled, containerized AWS workload** — Fargate task, EventBridge Scheduler, Terraform-provisioned infrastructure
- A pipeline with real **NLP/skill-extraction** logic, not just scraping — job-relevance scoring, taxonomy standardization, and structured skill/tool extraction

## Tech stack

| Component | Responsibility | Tech |
|---|---|---|
| Scraping | Multi-source collection | `python-jobspy`, `requests`, `aiohttp` |
| Stage storage | Intermediate datasets between steps | SQLite |
| NLP | Language detection, skill/tool extraction | `langdetect`, custom extractors |
| Export | Final job-market dataset | Supabase (Postgres, REST API) |
| Orchestration | Single-entrypoint daily run | `src/orchestrate/run_weekly.py` |
| Containerization | Reproducible runtime | Docker |
| Compute | Scheduled unattended execution | **AWS ECS Fargate** |
| Scheduling | Daily trigger | **AWS EventBridge Scheduler** — `cron(0 2 * * ? *)` |
| Secrets | Deploy-time configuration | AWS SSM Parameter Store |
| IaC | Infrastructure provisioning | **Terraform** (primary) + CloudFormation (`cfn/`) |

## Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#111827', 'primaryTextColor': '#F9FAFB', 'primaryBorderColor': '#60A5FA', 'lineColor': '#94A3B8', 'secondaryColor': '#1F2937', 'tertiaryColor': '#0F172A', 'fontSize': '15px'}}}%%
flowchart LR
    classDef source fill:#0F172A,stroke:#60A5FA,color:#F8FAFC,stroke-width:2px;
    classDef stage fill:#111827,stroke:#34D399,color:#F8FAFC,stroke-width:2px;
    classDef storage fill:#111827,stroke:#F59E0B,color:#F8FAFC,stroke-width:2px;
    classDef output fill:#111827,stroke:#C084FC,color:#F8FAFC,stroke-width:2px;

    A[Indeed + LinkedIn] --> B["1: Scraping<br/>jobs.db"]
    B --> C["2: Preprocessing<br/>jobs_processed.db"]
    C --> D["3: Enrichment + taxonomy<br/>jobs_enriched.db"]
    D --> E["S3 snapshot<br/>(versioned)"]
    D --> F["Supabase export<br/>(upsert via REST)"]
    F --> G["4: Deduplicate"]

    class A source;
    class B,C,D,F,G stage;
    class E storage;
    class G output;
```

### Filtering logic (recall then precision)

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#111827', 'primaryTextColor': '#F9FAFB', 'primaryBorderColor': '#F59E0B', 'lineColor': '#94A3B8', 'secondaryColor': '#1F2937', 'tertiaryColor': '#0F172A', 'fontSize': '15px'}}}%%
flowchart TD
    classDef input fill:#0F172A,stroke:#60A5FA,color:#F8FAFC,stroke-width:2px;
    classDef decision fill:#111827,stroke:#F59E0B,color:#F8FAFC,stroke-width:2px;
    classDef process fill:#111827,stroke:#34D399,color:#F8FAFC,stroke-width:2px;
    classDef reject fill:#111827,stroke:#F87171,color:#F8FAFC,stroke-width:2px;
    classDef keep fill:#111827,stroke:#C084FC,color:#F8FAFC,stroke-width:2px;

    A[Job title / source query] --> B{Broad recall match?}
    B -- yes --> C[Read description]
    B -- no --> X[Discard]
    C --> D{Tier1/Tier2 keyword score, no strong negative signal?}
    D -- yes --> E[Score 1-10, keep]
    D -- no --> X

    class A input;
    class B,D decision;
    class C process;
    class X reject;
    class E keep;
```

Relevance scoring (1–10) is documented in [`3- Enrichment + Standardization/JOB_RELEVANCE_SCORE.md`](3-%20Enrichment%20%2B%20Standardization/JOB_RELEVANCE_SCORE.md): a base score from tier1/tier2 keyword presence, adjusted by keyword frequency in title and description.

## NLP: skill and tool extraction

[`src/analysis/skill_extraction/`](src/analysis/skill_extraction/) pulls structured skill and tool mentions out of free-text job descriptions (`skill_extractor.py`, `tool_extractor.py`), matched against a controlled vocabulary in [`src/config/skills_reference.json`](src/config/skills_reference.json) and [`src/config/tools_reference.json`](src/config/tools_reference.json) — the same taxonomy that powers the skills/tools charts in [joblab-analytics-frontend](https://github.com/Mbehbahani/joblab-analytics-frontend).

## Quickstart

```bash
git clone https://github.com/Mbehbahani/joblab-data-pipeline.git
cd joblab-data-pipeline
python -m venv .venv && .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # fill in Supabase + AWS values
```

### Run the full local pipeline

```bash
python src/orchestrate/run_weekly.py --local           # full run, no S3 upload
python src/orchestrate/run_weekly.py --local --dry-run # skip Supabase push
python src/orchestrate/run_weekly.py --local --skip-scrape
```

### Run a single stage

```bash
python "1- Scrapped Data/job_scraper.py" --jobs 50 --countries "Germany,Netherlands"
python "2- Preprocessed/preprocess.py"
python "3- Enrichment + Standardization/taxonomy_standardization.py"
python "4- Deduplicate/deduplicate_supabase.py"
```

### Docker

```bash
docker build -t joblab-pipeline .
docker run --rm --env-file .env joblab-pipeline python src/orchestrate/run_weekly.py --local
```

## Configuration

All variables are documented in [`.env.example`](.env.example).

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUPABASE_URL` | export only | — | Target Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | export only | — | Server-side key for upserts |
| `S3_BUCKET` | AWS only | — | Bucket for snapshots and dedup tracking |
| `S3_PREFIX` | no | `joblab-supabase` | S3 key prefix |
| `AWS_REGION` | no | `us-east-1` | AWS region |
| `SCRAPE_COUNTRIES` | no | all | Comma-separated country list |
| `SCRAPE_MAX_JOBS` | no | `50` | Max jobs per country per run |
| `DAYS_BACK` | no | `7` | Lookback window for recency filtering |
| `DRY_RUN` | no | `false` | Skip Supabase push |
| `CLEAR_SUPABASE` | no | `false` | Clear tables before a full refresh |

## Project structure

```text
.
├── 1- Scrapped Data/
│   └── job_scraper.py
├── 2- Preprocessed/
│   └── preprocess.py
├── 3- Enrichment + Standardization/
│   ├── JOB_RELEVANCE_SCORE.md
│   └── taxonomy_standardization.py
├── 4- Deduplicate/
│   └── deduplicate_supabase.py
├── src/
│   ├── analysis/skill_extraction/   # NLP skill + tool extraction
│   ├── config/                       # skills/tools controlled vocabulary
│   ├── db/
│   ├── export/to_supabase.py         # Supabase REST client, dedup strategy
│   ├── models/
│   └── orchestrate/run_weekly.py     # daily pipeline entrypoint
├── scripts/                            # AWS setup, deploy, manual-run scripts
├── terraform/                           # ECS Fargate + EventBridge + IAM IaC
├── cfn/                                  # CloudFormation alternative
├── Dockerfile
├── task-definition.json
├── .env.example
├── LICENSE
└── requirements.txt
```

## Deployment

Deployed as a **daily containerized pipeline on AWS**: Terraform provisions an ECR repository, an ECS Fargate cluster/task, IAM roles, CloudWatch logging, an SSM parameter for secrets, and an EventBridge schedule (`cron(0 2 * * ? *)`, daily at 02:00 UTC).

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#111827', 'primaryTextColor': '#F9FAFB', 'primaryBorderColor': '#60A5FA', 'lineColor': '#94A3B8', 'secondaryColor': '#1F2937', 'tertiaryColor': '#0F172A', 'fontSize': '15px'}}}%%
flowchart TD
    classDef actor fill:#1f2937,stroke:#60a5fa,color:#f9fafb,stroke-width:2px;
    classDef script fill:#111827,stroke:#f59e0b,color:#f9fafb,stroke-width:2px;
    classDef aws fill:#0b1220,stroke:#8b5cf6,color:#f9fafb,stroke-width:2px;
    classDef runtime fill:#052e2b,stroke:#34d399,color:#ecfeff,stroke-width:2px;

    DEV([Developer]) --> TF[terraform apply]
    DEV --> DEPLOY[scripts/deploy.sh: build + push image]

    TF --> ECR[(ECR repository)]
    TF --> ECS{{ECS Fargate cluster}}
    TF --> SSM[(SSM Parameter Store)]
    TF --> SCH([EventBridge Scheduler: daily 02:00 UTC])
    TF --> LOGS[(CloudWatch Logs)]

    DEPLOY --> ECR
    SCH --> RUN([ECS Fargate task])
    ECS --> RUN
    ECR --> RUN
    SSM --> RUN

    RUN --> APP[[run_weekly.py]]
    APP --> SUPA[(Supabase)]
    APP --> S3[(S3 snapshots)]
    APP --> LOGS

    class DEV actor;
    class TF,DEPLOY script;
    class ECR,ECS,SSM,SCH,LOGS aws;
    class RUN,SUPA,APP,S3 runtime;
```

```bash
cd terraform
terraform init
terraform apply

./scripts/deploy.sh          # build + push image to ECR
./scripts/run-manual.sh      # trigger a one-off task run
aws logs tail /ecs/joblab-pipeline --follow
```

## Roadmap / TO-DO

- [ ] Add automated tests for the enrichment/scoring stage
- [ ] Publish relevance-scoring precision/recall metrics
- [ ] Add CI (lint) via `.github/workflows/`

## Related links

- [joblab-agent-api](https://github.com/Mbehbahani/joblab-agent-api) — AI agent backend consuming this pipeline's Supabase output
- [joblab-analytics-frontend](https://github.com/Mbehbahani/joblab-analytics-frontend) — dashboard visualizing this data

## Security note

This repository's git history was scrubbed of a plaintext database password with `git-filter-repo` prior to publication.

## License

[MIT](LICENSE) © 2026 M.Behbahani
