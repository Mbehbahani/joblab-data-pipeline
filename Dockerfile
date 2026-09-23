# =============================================================================
# Job Pipeline - Production Dockerfile
# =============================================================================
# Pure Python runtime for scraping, preprocessing, enrichment, S3 upload, 
# and Supabase push (via REST API).
# No Node.js required - Supabase push is done via Python HTTP client.
# =============================================================================

FROM python:3.14-slim as production

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Verify installations
RUN python --version && sqlite3 --version

# Set working directory
WORKDIR /app

# Create non-root user for security
RUN groupadd -r joblab && useradd -r -g joblab -m joblab

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (excluding node_modules, convex TS files, etc.)
COPY . .

# Create necessary directories
RUN mkdir -p /tmp/joblab_run && chown -R joblab:joblab /tmp/joblab_run

# Set ownership
RUN chown -R joblab:joblab /app

# Switch to non-root user
USER joblab

# Environment variables (override at runtime)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    S3_BUCKET="" \
    S3_PREFIX="joblab-supabase" \
    AWS_REGION="us-east-1" \
    DRY_RUN="false" \
    CLEAR_SUPABASE="false"

# Default command - run the weekly orchestrator with shell to ensure proper logging
CMD ["sh", "-c", "exec python -u src/orchestrate/run_weekly.py 2>&1"]