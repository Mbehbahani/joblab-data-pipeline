#!/usr/bin/env python3
"""
Multi-Country Optimization Job Scraper
Scrapes Operations Research & Optimization jobs across multiple countries

Usage:
    python run_netherlands_indeed_linkedin.py --jobs 50
    python run_netherlands_indeed_linkedin.py --jobs 30 --countries "USA,UK,Germany"
"""

import asyncio
import argparse
import logging
import sys
import time
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Suppress JobSpy verbose logging
logging.getLogger("jobspy").setLevel(logging.WARNING)

# ============================================================
# TIER 1: Broad Title Keywords (cast wide net)
# ============================================================
FILTER_KEYWORDS_TITLE = [
    "optim",                    # optimization, optimizer, optimal
    "operations research",
    "supply chain",
    "logistics",
    "routing",
    "scheduling",
    "decision science",
    "algorithm",
    "data scientist",
    "machine learning",
    "analytics",
    "solver",
    "mathematical",
]

# ============================================================
# TIER 2: Strong Technical Keywords (high precision)
# Must appear in job description for non-obvious titles
# ============================================================
FILTER_KEYWORDS_STRONG = [
    "operations research",
    "linear programming",
    "integer programming",
    "mixed integer",
    "milp",
    "gurobi", "cplex", "or-tools", "ortools",
    "constraint programming",
    "combinatorial optimization",
    "mathematical optimization",
    "network optimization",
    "vehicle routing",
    "scheduling optimization",
    "supply chain optimization",
    "inventory optimization",
    "heuristic", "metaheuristic",
    "convex optimization",
    "stochastic optimization",
    "discrete optimization",
    "pulp",
    "pyomo",
    "Industrial Engineering",
    "Fulfillment Optimization",
    "demand planning",
]

# ============================================================
# NEGATIVE KEYWORDS: Reject if title contains these
# ============================================================
FILTER_KEYWORDS_NEGATIVE = [
    "seo",
    "search engine",
    "sales optimization",
    "marketing optimization",
    "conversion optimization",
    "website optimization",
    "social media",
    "content optimization",
    "ad optimization",
    "campaign optimization",
]

# ============================================================
# FILTER STATISTICS TRACKER
# ============================================================
class FilterStats:
    """Track filter effectiveness per search term"""
    def __init__(self):
        self.stats = {}  # {search_term: {found, rejected_negative, accepted_tier1, accepted_tier2, accepted_both, rejected_no_match, final}}
    
    def init_term(self, search_term: str):
        if search_term not in self.stats:
            self.stats[search_term] = {
                "found": 0,
                "rejected_negative": 0,
                "accepted_tier1_only": 0,  # Title match but no description match
                "accepted_tier2_only": 0,  # Description match but no title match
                "accepted_both": 0,        # Both title and description match
                "rejected_no_match": 0,    # No match at all
                "final": 0,                # After deduplication
            }
    
    def record(self, search_term: str, found: int = 0, rejected_negative: int = 0,
               accepted_tier1_only: int = 0, accepted_tier2_only: int = 0,
               accepted_both: int = 0, rejected_no_match: int = 0, final: int = 0):
        self.init_term(search_term)
        self.stats[search_term]["found"] += found
        self.stats[search_term]["rejected_negative"] += rejected_negative
        self.stats[search_term]["accepted_tier1_only"] += accepted_tier1_only
        self.stats[search_term]["accepted_tier2_only"] += accepted_tier2_only
        self.stats[search_term]["accepted_both"] += accepted_both
        self.stats[search_term]["rejected_no_match"] += rejected_no_match
        self.stats[search_term]["final"] += final
    
    def print_summary(self):
        """Print a formatted table of filter statistics"""
        if not self.stats:
            print("\n📊 No filter statistics recorded.")
            return
        
        print("\n" + "=" * 120)
        print("📊 FILTER EFFECTIVENESS ANALYSIS")
        print("=" * 120)
        print(f"{'Search Term':<25} {'Found':>6} {'Neg':>5} {'T1':>5} {'T2':>5} {'Both':>5} {'NoMatch':>7} {'Final':>6} {'Rate':>6}")
        print("-" * 120)
        
        totals = {"found": 0, "rejected_negative": 0, "accepted_tier1_only": 0,
                  "accepted_tier2_only": 0, "accepted_both": 0, "rejected_no_match": 0, "final": 0}
        
        for term, s in sorted(self.stats.items()):
            found = s["found"]
            accepted = s["accepted_tier1_only"] + s["accepted_tier2_only"] + s["accepted_both"]
            rate = f"{(accepted/found*100):.0f}%" if found > 0 else "N/A"
            
            print(f"{term:<25} {found:>6} {s['rejected_negative']:>5} {s['accepted_tier1_only']:>5} "
                  f"{s['accepted_tier2_only']:>5} {s['accepted_both']:>5} {s['rejected_no_match']:>7} {s['final']:>6} {rate:>6}")
            
            for key in totals:
                totals[key] += s[key]
        
        print("-" * 120)
        total_accepted = totals["accepted_tier1_only"] + totals["accepted_tier2_only"] + totals["accepted_both"]
        total_rate = f"{(total_accepted/totals['found']*100):.0f}%" if totals["found"] > 0 else "N/A"
        print(f"{'TOTAL':<25} {totals['found']:>6} {totals['rejected_negative']:>5} {totals['accepted_tier1_only']:>5} "
              f"{totals['accepted_tier2_only']:>5} {totals['accepted_both']:>5} {totals['rejected_no_match']:>7} {totals['final']:>6} {total_rate:>6}")
        
        print("\n📖 Legend:")
        print("   Found    = Raw jobs from platform API")
        print("   Neg      = Rejected by NEGATIVE keywords (SEO, marketing, etc.)")
        print("   T1       = Accepted by TIER1 only (title match, no description match)")
        print("   T2       = Accepted by TIER2 only (description match, no title match)")
        print("   Both     = Accepted by BOTH tiers (strongest match)")
        print("   NoMatch  = Rejected (no filter matched)")
        print("   Final    = Stored after deduplication")
        print("   Rate     = Acceptance rate (accepted / found)")
        print("=" * 120)
        
        # Recommendations
        print("\n💡 RECOMMENDATIONS:")
        low_rate_terms = [(t, s) for t, s in self.stats.items() 
                          if s["found"] > 0 and (s["accepted_tier1_only"] + s["accepted_tier2_only"] + s["accepted_both"]) / s["found"] < 0.5]
        high_tier1_only = [(t, s) for t, s in self.stats.items() 
                           if s["accepted_tier1_only"] > s["accepted_tier2_only"] + s["accepted_both"]]
        
        if low_rate_terms:
            print(f"   ⚠️  Low acceptance rate (<50%): {', '.join(t for t, _ in low_rate_terms)}")
            print("      → Consider removing these search terms or refining filters")
        
        if high_tier1_only:
            print(f"   ⚠️  High TIER1-only acceptance: {', '.join(t for t, _ in high_tier1_only)}")
            print("      → Jobs matched by title only - may include false positives")
        
        if not low_rate_terms and not high_tier1_only:
            print("   ✅ Filter configuration looks good!")


# Global filter stats instance
filter_stats = FilterStats()


# ============================================================
# JOB LEVEL & FUNCTION EXTRACTION (for Indeed jobs)
# ============================================================
def extract_job_level(title: str, description: str) -> str:
    """
    Extract job seniority level from title/description for Indeed jobs.
    Returns level string distinguishing between Mid-Level and Senior.
    
    Levels:
    - Internship: intern, co-op
    - Entry level: entry level, junior, graduate, associate (not senior associate)
    - Mid-Level: analyst, specialist, coordinator, no seniority indicators
    - Senior: senior, sr., lead, principal, staff, manager
    - Director: director, vp, vice president, head of
    - Executive: chief, cto, ceo, cfo, executive
    """
    if not title:
        return ""
    
    title_lower = title.lower()
    text = (title + " " + (description or "")[:500]).lower()
    
    # Ordered by specificity - check most specific first
    
    # 1. Internship - explicit intern keywords
    if any(kw in text for kw in ["intern", "internship", "co-op", "co op"]):
        return "Internship"
    
    # 2. Executive level - C-suite
    if any(kw in text for kw in ["chief", "cto", "ceo", "cfo", "coo", "executive"]):
        return "Executive"
    
    # 3. Director level
    if any(kw in text for kw in ["director", "vp", "vice president", "head of"]):
        return "Director"
    
    # 4. Senior level - explicit senior keywords (check title primarily)
    senior_keywords = ["senior", "sr.", "sr ", "lead", "principal", "staff engineer", "staff scientist"]
    if any(kw in title_lower for kw in senior_keywords):
        return "Senior"
    
    # 5. Entry level - explicit entry keywords
    entry_keywords = ["entry level", "entry-level", "junior", "jr.", "jr ", "graduate", "new grad", "fresher"]
    if any(kw in text for kw in entry_keywords):
        return "Entry level"
    
    # 6. Associate level - but NOT "senior associate"
    if "associate" in title_lower and "senior" not in title_lower:
        return "Associate"
    
    # 7. Manager without senior/director implies Mid-Level to Senior transition
    if any(kw in title_lower for kw in ["manager", "management"]) and not any(kw in title_lower for kw in ["senior", "director"]):
        return "Senior"  # Managers are typically senior level
    
    # 8. Mid-Level indicators in title
    mid_level_keywords = ["analyst", "specialist", "coordinator", "engineer", "scientist", "consultant", "developer"]
    if any(kw in title_lower for kw in mid_level_keywords):
        # These are mid-level unless they have senior/lead prefix (already caught above)
        return "Mid-Level"
    
    # Default - unknown, will be refined later
    return "Mid-Level"


def extract_job_function(title: str, description: str) -> str:
    """
    Extract job function/category from title/description for Indeed jobs.
    Returns LinkedIn-compatible function string.
    """
    if not title:
        return ""
    
    text = (title + " " + (description or "")[:500]).lower()
    
    # Prioritize by relevance to OR/optimization jobs
    if any(kw in text for kw in ["operations research", "optimization", "mathematical", "algorithm"]):
        return "Operations Research"
    elif any(kw in text for kw in ["data scien", "machine learning", "ml engineer", "ai engineer"]):
        return "Data Science"
    elif any(kw in text for kw in ["software engineer", "developer", "programming", "backend", "frontend"]):
        return "Engineering"
    elif any(kw in text for kw in ["data engineer", "data architect", "etl", "pipeline"]):
        return "Information Technology"
    elif any(kw in text for kw in ["analyst", "analytics", "business intelligence", "bi "]):
        return "Analytics"
    elif any(kw in text for kw in ["supply chain", "logistics", "procurement", "inventory"]):
        return "Supply Chain"
    elif any(kw in text for kw in ["research", "scientist", "phd", "postdoc"]):
        return "Research"
    elif any(kw in text for kw in ["consulting", "consultant", "advisory"]):
        return "Consulting"
    elif any(kw in text for kw in ["product manager", "product owner", "pm "]):
        return "Product Management"
    else:
        return "Engineering"  # Default fallback


def matches_optimization_keywords(title: str, description: str) -> bool:
    """
    Returns True if job is optimization-related (OR/Math focus).
    
    Strategy:
    1. Reject if title contains negative keywords (SEO, sales, etc.)
    2. If title contains broad keywords → likely relevant
    3. If description contains strong OR/math keywords → definitely relevant
    4. Otherwise → reject (e.g., "SEO optimization", "sales optimization")
    """
    if not title:
        return False
    
    title_lower = title.lower()
    desc_lower = (description or "").lower()
    
    # First: Reject obvious non-OR jobs
    if any(neg.lower() in title_lower for neg in FILTER_KEYWORDS_NEGATIVE):
        return False
    
    # Method 1: Title match (broad but high recall)
    title_match = any(kw.lower() in title_lower for kw in FILTER_KEYWORDS_TITLE)
    
    # Method 2: Description match (narrow but high precision)
    strong_match = any(kw.lower() in desc_lower for kw in FILTER_KEYWORDS_STRONG)
    
    # Accept if EITHER condition is met
    return title_match or strong_match


def matches_optimization_keywords_detailed(title: str, description: str) -> dict:
    """
    Returns detailed match information for statistics tracking.
    
    Returns dict with:
        - accepted: bool
        - rejected_negative: bool
        - tier1_match: bool
        - tier2_match: bool
        - tier1_keywords: list of matched TIER1 keywords
        - tier2_keywords: list of matched TIER2 keywords
    """
    result = {
        "accepted": False,
        "rejected_negative": False,
        "tier1_match": False,
        "tier2_match": False,
        "tier1_keywords": [],
        "tier2_keywords": [],
    }
    
    if not title:
        return result
    
    title_lower = title.lower()
    desc_lower = (description or "").lower()
    
    # Check negative keywords
    if any(neg.lower() in title_lower for neg in FILTER_KEYWORDS_NEGATIVE):
        result["rejected_negative"] = True
        return result
    
    # Check Tier 1 (title) - collect all matching keywords
    tier1_matched = [kw for kw in FILTER_KEYWORDS_TITLE if kw.lower() in title_lower]
    if tier1_matched:
        result["tier1_match"] = True
        result["tier1_keywords"] = tier1_matched
    
    # Check Tier 2 (description) - collect all matching keywords
    tier2_matched = [kw for kw in FILTER_KEYWORDS_STRONG if kw.lower() in desc_lower]
    if tier2_matched:
        result["tier2_match"] = True
        result["tier2_keywords"] = tier2_matched
    
    # Accept if either matches
    result["accepted"] = result["tier1_match"] or result["tier2_match"]
    
    return result


# Configuration
COUNTRIES = {
    "USA": {"name": "USA", "indeed_country": "USA", "flag": "🇺🇸"},
    "Canada": {"name": "Canada", "indeed_country": "Canada", "flag": "🇨🇦"},
    "UK": {"name": "United Kingdom", "indeed_country": "UK", "flag": "🇬🇧"},
    "Netherlands": {"name": "Netherlands", "indeed_country": "Netherlands", "flag": "🇳🇱"},
    "Germany": {"name": "Germany", "indeed_country": "Germany", "flag": "🇩🇪"},
    "France": {"name": "France", "indeed_country": "France", "flag": "🇫🇷"},
    "Australia": {"name": "Australia", "indeed_country": "Australia", "flag": "🇦🇺"},
    "India": {"name": "India", "indeed_country": "India", "flag": "🇮🇳"},
}

SEARCH_TERMS = [
    "operation research",  # Lowercase - more targeted than capitalized version
    "Mathematical Optimization",
    "MILP",
    "Integer Programming",
    "Gurobi",
    "Routing Optimization",
    "Supply Chain Optimization",
    "Simulation Optimization",  # Captures both "simulation optimization" and "simulation-based optimization"
]

# Country-specific job targets based on market size
# Ensures fair distribution: more jobs for larger markets, fewer for smaller ones
COUNTRY_JOB_TARGETS = {
    "USA": 200,        # Huge market - extensive OR job opportunities
    "India": 200,      # Huge market - growing OR sector
    "UK": 150,         # Large market - strong OR presence
    "Canada": 100,     # Medium-large market
    "Germany": 100,    # Medium-large market
    "Australia": 100,  # Medium market
    "France": 80,      # Medium market
    "Netherlands": 100, # Small market - but high OR concentration
}

# Multiplier for all country targets (change this to scale all targets)
# Examples: 1.0 = normal, 2.0 = double jobs, 0.5 = half jobs
COUNTRY_JOB_MULTIPLIER = 3.0  # Aggressive scraping for initial database population

# Platforms to search
# LinkedIn now uses the improved notebook approach (sequential queries with proper rate limiting)
PLATFORMS = ["indeed", "linkedin"]

# Default countries per platform
# Indeed: All countries (comprehensive coverage)
DEFAULT_COUNTRIES_INDEED = list(COUNTRIES.keys())  # All 10 countries

# LinkedIn: Limited to avoid rate limiting (high-value markets only)
DEFAULT_COUNTRIES_LINKEDIN = ["Germany", "Netherlands"]

# ============================================================
# TIME FILTERS - Platform-specific (Indeed vs LinkedIn)
# ============================================================
# Note: Indeed's hours_old filter is approximate - jobs may be slightly older
# We apply a post-scrape filter to ensure strict compliance
HOURS_OLD_INDEED = 30   # ~1.25 days for Indeed (post-scrape filter enforced)
HOURS_OLD_LINKEDIN = 50  # ~2 days for LinkedIn

# LinkedIn rate-limiting settings (improved from job2LN.ipynb approach)
LINKEDIN_SLEEP_SEC = 10.0  # Sleep between LinkedIn queries (slightly longer for stability)
LINKEDIN_MAX_ERRORS = 3    # Stop LinkedIn scraping after this many consecutive errors
LINKEDIN_FETCH_DESCRIPTION = True  # Fetch full description (slower but more data)


def looks_like_rate_limit(err: Exception) -> bool:
    """Detect if error indicates rate limiting / blocking"""
    msg = str(err).lower()
    return ("429" in msg) or ("too many requests" in msg) or ("rate" in msg) or \
           ("blocked" in msg) or ("captcha" in msg) or ("nonetype" in msg)


def scrape_linkedin_for_country(
    queries: list[str],
    location: str,
    results_per_query: int = 25,
    hours_old: int = HOURS_OLD_LINKEDIN,  # Uses HOURS_OLD_LINKEDIN constant
    sleep_sec: float = 10.0,
    max_errors: int = 3,
    fetch_description: bool = True,
) -> pd.DataFrame:
    """
    Scrape LinkedIn using the improved notebook approach.
    Runs queries sequentially with proper rate limiting to avoid blocking.
    Returns a combined DataFrame with all results.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.error("python-jobspy not installed")
        return pd.DataFrame()
    
    all_parts = []
    error_count = 0
    
    for query in queries:
        print(f"    Searching: {query}...", end=" ")
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=query,
                location=location,
                results_wanted=results_per_query,
                hours_old=hours_old,
                verbose=0,
                linkedin_fetch_description=fetch_description,
            )
            if df is None or len(df) == 0:
                print("0 results")
            else:
                df = df.copy()
                df["search_term_used"] = query
                df["scraped_at"] = datetime.now(timezone.utc).isoformat()
                all_parts.append(df)
                print(f"{len(df)} results")
                error_count = 0  # Reset error count on success
            
            # Sleep between queries to avoid rate limiting
            time.sleep(sleep_sec)
            
        except Exception as e:
            error_count += 1
            logger.error(f"    ERROR: {e}")
            
            # If blocked/rate-limited: stop rather than hammering
            if looks_like_rate_limit(e):
                logger.error("    Detected blocking / rate limiting. Stopping LinkedIn for this country.")
                break
            
            if error_count >= max_errors:
                logger.error("    Too many errors. Stopping LinkedIn for this country.")
                break
            
            # Longer wait after error
            time.sleep(sleep_sec * 2)
    
    if not all_parts:
        return pd.DataFrame()
    
    # Combine and deduplicate by URL
    combined = pd.concat(all_parts, ignore_index=True)
    combined["url_temp"] = combined.apply(
        lambda row: str(row.get("job_url_direct") or row.get("job_url") or "").strip(),
        axis=1
    )
    combined = combined[combined["url_temp"].str.len() > 0]
    combined = combined.drop_duplicates(subset=["url_temp"])
    
    return combined


def process_jobs_dataframe(
    jobs_df: pd.DataFrame,
    platform: str,
    country_key: str,
    search_term: str | None,
    seen_urls: set,
    hours_old_filter: int = None,  # Optional: filter jobs older than this
) -> list:
    """
    Process a DataFrame of jobs into JobDetailModel objects.
    Captures all fields including:
    - date_posted, is_remote, job_level, job_function, company_industry
    - job_type, company_num_employees
    - filter_tier1_keywords, filter_tier2_keywords (computed per job)
    
    NOTE: Skill extraction is now performed at Stage 3 (Enrichment + Standardization)
    using the comprehensive skills_reference.json with regex patterns.
    
    Args:
        jobs_df: DataFrame from JobSpy scrape_jobs()
        platform: "indeed" or "linkedin"
        country_key: Country code (e.g., "USA", "Netherlands")
        search_term: Search term used (None if using per-row search_term_used)
        seen_urls: Set of already-seen URLs (will be updated)
        hours_old_filter: Optional - reject jobs older than this (hours)
    
    Returns:
        List of JobDetailModel objects
    """
    from src.models.models import JobDetailModel
    
    jobs = []
    filtered_old_count = 0  # Track jobs filtered for being too old
    
    # Calculate cutoff date if hours_old_filter is provided
    cutoff_date = None
    if hours_old_filter:
        cutoff_date = datetime.now() - timedelta(hours=hours_old_filter)
    
    # Deduplicate by URL before processing - prioritize platform URL
    jobs_df = jobs_df.copy()
    jobs_df['url_temp'] = jobs_df.apply(
        lambda row: str(row.get('job_url') or row.get('job_url_direct') or '').strip(),
        axis=1
    )
    # Remove rows with empty URLs or already seen URLs
    jobs_df = jobs_df[
        (jobs_df['url_temp'].str.len() > 0) & 
        (~jobs_df['url_temp'].isin(seen_urls))
    ]
    
    if len(jobs_df) == 0:
        return jobs
    
    for idx, (_, row) in enumerate(jobs_df.iterrows()):
        try:
            # Get URLs - prioritize platform URL (LinkedIn/Indeed) for main url field
            # job_url = Platform URL (LinkedIn/Indeed page)
            # job_url_direct = Company career page URL
            platform_url = str(row.get('job_url') or '').strip()
            company_url = str(row.get('job_url_direct') or '').strip()
            
            # Use platform URL as primary (for deduplication), fallback to company URL
            url = platform_url or company_url
            if not url or url == 'nan':
                continue
            if url in seen_urls:
                continue
            
            # Mark URL as seen
            seen_urls.add(url)
            
            # NOTE: Skill extraction is now done at Stage 3 (Enrichment + Standardization)
            # using the comprehensive skills_reference.json with regex patterns.
            # At this stage, we just store the raw description.
            description = row.get('description', '') or ''
            skills = []  # Skills will be extracted at Stage 3
            
            # Get search term (from row if available, else use provided)
            job_search_term = row.get('search_term_used') or search_term or ''
            
            # Get location - JobSpy returns Location object or string
            job_location = ''
            loc_data = row.get('location')
            if loc_data:
                if isinstance(loc_data, str):
                    job_location = loc_data
                elif hasattr(loc_data, 'city') or hasattr(loc_data, 'state'):
                    # Location object from JobSpy
                    parts = []
                    if hasattr(loc_data, 'city') and loc_data.city:
                        parts.append(str(loc_data.city))
                    if hasattr(loc_data, 'state') and loc_data.state:
                        parts.append(str(loc_data.state))
                    if hasattr(loc_data, 'country') and loc_data.country:
                        parts.append(str(loc_data.country))
                    job_location = ', '.join(parts)
                else:
                    job_location = str(loc_data)
            
            # Get posted date
            posted_date = None
            is_date_assigned = False  # Track if we assigned the date vs extracted from source
            date_posted_raw = row.get('date_posted')
            if date_posted_raw and not pd.isna(date_posted_raw):
                if isinstance(date_posted_raw, datetime):
                    posted_date = date_posted_raw
                elif hasattr(date_posted_raw, 'year'):
                    # Handle datetime.date objects from JobSpy
                    posted_date = datetime.combine(date_posted_raw, datetime.min.time())
                elif isinstance(date_posted_raw, str):
                    try:
                        # JobSpy returns dates as YYYY-MM-DD strings
                        posted_date = datetime.strptime(date_posted_raw, '%Y-%m-%d')
                    except (ValueError, TypeError):
                        # Fallback to ISO format
                        try:
                            posted_date = datetime.fromisoformat(date_posted_raw.replace('Z', '+00:00'))
                        except (ValueError, TypeError):
                            pass
            
            # If no date extracted, assign current date and mark it
            if not posted_date:
                posted_date = datetime.now()
                is_date_assigned = True
            
            # ===== POST-SCRAPE DATE FILTER =====
            # Indeed doesn't always respect hours_old, so we filter here
            # Jobs with assigned dates (missing original dates) will pass this filter
            if cutoff_date and posted_date:
                if posted_date < cutoff_date:
                    # Reject jobs older than cutoff
                    filtered_old_count += 1
                    continue  # Skip jobs older than cutoff
            
            # ===== FIELDS FROM JOBSPY =====
            # is_remote: boolean
            is_remote = row.get('is_remote')
            if pd.isna(is_remote):
                is_remote = None
            elif isinstance(is_remote, bool):
                pass  # Keep as is
            else:
                is_remote = bool(is_remote) if is_remote else None
            
            # job_level: LinkedIn provides this, extract from Indeed jobs
            job_level = row.get('job_level', '') or ''
            if pd.isna(job_level):
                job_level = ''
            else:
                job_level = str(job_level)
            
            # Extract job_level from title/description if empty (Indeed jobs)
            if not job_level and platform == "indeed":
                job_level = extract_job_level(row.get('title', ''), description)
            
            # job_function: LinkedIn provides this, extract from Indeed jobs
            job_function = row.get('job_function', '') or ''
            if pd.isna(job_function):
                job_function = ''
            else:
                job_function = str(job_function)
            
            # Extract job_function from title/description if empty (Indeed jobs)
            if not job_function and platform == "indeed":
                job_function = extract_job_function(row.get('title', ''), description)
            
            # company_industry: LinkedIn & Indeed (e.g., "IT Services", "Manufacturing")
            company_industry = row.get('company_industry', '') or ''
            if pd.isna(company_industry):
                company_industry = ''
            else:
                company_industry = str(company_industry)
            
            # ===== NEW FIELDS: job_type, experience, company size =====
            # job_type: fulltime, parttime, contract, internship
            job_type = row.get('job_type', '') or ''
            if pd.isna(job_type):
                job_type = ''
            elif hasattr(job_type, 'value'):
                # JobSpy returns JobType enum
                job_type = str(job_type.value)
            else:
                job_type = str(job_type)
            
            # company_num_employees: e.g., "1001-5000"
            company_num_employees = row.get('company_num_employees', '') or ''
            if pd.isna(company_num_employees):
                company_num_employees = ''
            else:
                company_num_employees = str(company_num_employees)
            
            # Get filter keywords for this job - compute directly from title/description
            # This ensures alignment even when rows are skipped
            title_for_filter = row.get('title', '') or ''
            filter_info = matches_optimization_keywords_detailed(title_for_filter, description)
            tier1_kw = ', '.join(filter_info.get('tier1_keywords', []))
            tier2_kw = ', '.join(filter_info.get('tier2_keywords', []))
            
            job = JobDetailModel(
                platform=platform,
                job_id=f"{platform}_{country_key}_{row.get('id', '')}_{hash(url) % 100000}",
                actual_role=(row.get('title') or job_search_term or ''),
                url=url,
                job_description=description,
                company_name=row.get('company', '') or '',
                country=country_key,
                location=job_location,
                search_term=job_search_term,
                posted_date=posted_date,
                is_posted_date_assigned=is_date_assigned,
                skills=', '.join(skills) if skills else '',
                # Remote/Location
                is_remote=is_remote,
                # Job metadata
                job_level=job_level,
                job_function=job_function,
                job_type=job_type,
                # Company fields
                company_industry=company_industry,
                company_url=company_url,
                company_num_employees=company_num_employees,
                # Filter tracking
                filter_tier1_keywords=tier1_kw,
                filter_tier2_keywords=tier2_kw,
            )
            jobs.append(job)
            
        except Exception as e:
            logger.error(f"Error processing job row: {e}")
            continue
    
    # Log if any jobs were filtered for being too old
    if filtered_old_count > 0:
        logger.warning(f"Filtered {filtered_old_count} jobs older than {hours_old_filter} hours")
    
    return jobs


async def scrape_optimization_jobs(
    num_jobs: int = 50,
    countries_filter: list = None,
    countries_filter_indeed: list = None,
    countries_filter_linkedin: list = None,
):
    """Scrape Optimization jobs from Indeed AND LinkedIn across multiple countries

    Allows platform-specific country lists. If not specified:
    - Indeed: Uses DEFAULT_COUNTRIES_INDEED (all 10 countries)
    - LinkedIn: Uses DEFAULT_COUNTRIES_LINKEDIN (USA, Germany, Netherlands)
    This avoids LinkedIn rate limiting while maintaining comprehensive Indeed coverage.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        print("[ERROR] python-jobspy is not installed!")
        print("\nTo install, run:")
        print("  pip install python-jobspy")
        print("\nOr:")
        print("  .\\venv\\Scripts\\activate")
        print("  pip install python-jobspy")
        sys.exit(1)

    from src.db.operations import JobStorageOperations
    # NOTE: Skill extraction is now done at Stage 3 (Enrichment + Standardization)
    # using the comprehensive skills_reference.json with regex patterns.

    # Build allowed sets per platform with smart defaults
    if countries_filter_indeed is not None:
        allowed_indeed = set(countries_filter_indeed)
    elif countries_filter:  # If --countries provided, use it for both
        allowed_indeed = set(countries_filter)
    else:  # No flags: use defaults
        allowed_indeed = set(DEFAULT_COUNTRIES_INDEED)

    if countries_filter_linkedin is not None:
        allowed_linkedin = set(countries_filter_linkedin)
    elif countries_filter:  # If --countries provided, use it for both
        allowed_linkedin = set(countries_filter)
    else:  # No flags: use defaults
        allowed_linkedin = set(DEFAULT_COUNTRIES_LINKEDIN)

    # Target countries = union of both platforms
    target_countries = sorted(allowed_indeed | allowed_linkedin)
    
    print("\n" + "=" * 70)
    print("[OPTIMIZATION JOBS SCRAPER] - Multi-Country & Multi-Platform")
    print("=" * 70)
    print(f"Platform filters:")
    print(f"  Indeed targets: {', '.join(sorted(allowed_indeed))}")
    print(f"  LinkedIn targets: {', '.join(sorted(allowed_linkedin))}")
    print(f"\nTarget Jobs: Market-based distribution (larger markets = more jobs)")
    if COUNTRY_JOB_MULTIPLIER != 1.0:
        print(f"Multiplier: {COUNTRY_JOB_MULTIPLIER}x (scaled targets)")
    for country_key in target_countries:
        base_target = COUNTRY_JOB_TARGETS.get(country_key, num_jobs)
        country_target = int(base_target * COUNTRY_JOB_MULTIPLIER)
        print(f"  {country_key}: {country_target} jobs")
    print(f"\nSearch Terms: {len(SEARCH_TERMS)} optimization-related queries")
    
    # Warning about LinkedIn rate limiting
    linkedin_countries_count = len(allowed_linkedin)
    if linkedin_countries_count > 2:
        print(f"\n[WARNING] Scraping {linkedin_countries_count} countries on LinkedIn")
        print(f"   LinkedIn may block after ~90-120 queries (rate limiting)")
        print(f"   With {len(SEARCH_TERMS)} search terms x {linkedin_countries_count} countries = {len(SEARCH_TERMS) * linkedin_countries_count} total queries")
    
    print("=" * 70 + "\n")

    # Use absolute path for database (same path that preprocessor expects)
    db_path = Path(__file__).resolve().parent / "jobs.db"
    db = JobStorageOperations(str(db_path))
    
    # NOTE: Skill extraction is now done at Stage 3 (Enrichment + Standardization)
    # using src/config/skills_reference.json with comprehensive regex patterns.
    
    all_jobs = []
    country_stats = {country: {"indeed": 0, "linkedin": 0} for country in target_countries}
    seen_urls = set()  # Track URLs to prevent duplicates across search terms and platforms

    for country_key in target_countries:
        country = COUNTRIES.get(country_key)
        if not country:
            print(f"⚠️  Unknown country: {country_key}, skipping...")
            continue
            
        print(f"\n{'='*70}")
        print(f"Scraping {country['name'].upper()}")
        print(f"{'='*70}")

        country_jobs = []
        # Use country-specific target, fallback to num_jobs if not defined
        base_target = COUNTRY_JOB_TARGETS.get(country_key, num_jobs)
        country_target = int(base_target * COUNTRY_JOB_MULTIPLIER)
        jobs_per_term = max(1, country_target // len(SEARCH_TERMS))
        
        # Determine which platforms to run for this country
        run_indeed = country_key in allowed_indeed
        run_linkedin = country_key in allowed_linkedin
        
        print(f"Target: {country_target} jobs ({jobs_per_term} per search term)")
        print(f"Platforms: Indeed={'YES' if run_indeed else 'NO'}, LinkedIn={'YES' if run_linkedin else 'NO'}")

        # ===== INDEED SCRAPING (per search term) =====
        if run_indeed:
            print(f"\n  [INDEED Platform]")
            for search_term in SEARCH_TERMS:
                print(f"\n    [SEARCH] {search_term}...")
                try:
                    jobs_df = scrape_jobs(
                        site_name=["indeed"],
                        search_term=search_term,
                        location=country["name"],
                        results_wanted=jobs_per_term,
                        hours_old=HOURS_OLD_INDEED,  # Indeed-specific time filter
                        country_indeed=country["indeed_country"],
                    )

                    if jobs_df is None or len(jobs_df) == 0:
                        print(f"      ⚠️  No jobs found for '{search_term}'")
                        continue

                    # Apply two-tier filter with detailed tracking
                    original_count = len(jobs_df)
                    
                    # Get detailed match info for each job
                    match_results = jobs_df.apply(
                        lambda row: matches_optimization_keywords_detailed(
                            row.get('title', ''), 
                            row.get('description', '')
                        ), 
                        axis=1
                    )
                    
                    # Count statistics
                    rejected_negative = sum(1 for r in match_results if r["rejected_negative"])
                    accepted_tier1_only = sum(1 for r in match_results if r["accepted"] and r["tier1_match"] and not r["tier2_match"])
                    accepted_tier2_only = sum(1 for r in match_results if r["accepted"] and r["tier2_match"] and not r["tier1_match"])
                    accepted_both = sum(1 for r in match_results if r["accepted"] and r["tier1_match"] and r["tier2_match"])
                    rejected_no_match = sum(1 for r in match_results if not r["accepted"] and not r["rejected_negative"])
                    
                    # Record stats
                    filter_stats.record(
                        search_term,
                        found=original_count,
                        rejected_negative=rejected_negative,
                        accepted_tier1_only=accepted_tier1_only,
                        accepted_tier2_only=accepted_tier2_only,
                        accepted_both=accepted_both,
                        rejected_no_match=rejected_no_match
                    )
                    
                    # Filter the dataframe
                    jobs_df = jobs_df[[r["accepted"] for r in match_results]]
                    filtered_count = len(jobs_df)
                    rejected_count = original_count - filtered_count
                    
                    if filtered_count == 0:
                        print(f"      [WARNING] Found {original_count} jobs, but 0 matched OR/optimization criteria")
                        continue
                        
                    print(f"      [OK] Found {original_count} jobs -> {filtered_count} relevant (filtered {rejected_count} non-OR)")

                    # Process Indeed jobs (with post-scrape date filter for Indeed reliability)
                    # NOTE: Skill extraction is done at Stage 3 (Enrichment + Standardization)
                    new_jobs = process_jobs_dataframe(
                        jobs_df, "indeed", country_key, search_term,
                        seen_urls,
                        hours_old_filter=HOURS_OLD_INDEED,  # Filter out jobs older than HOURS_OLD_INDEED
                    )
                    country_jobs.extend(new_jobs)
                    country_stats[country_key]["indeed"] += len(new_jobs)
                    
                    # Update final count in stats
                    filter_stats.record(search_term, final=len(new_jobs))

                except Exception as e:
                    logger.error(f"Error scraping '{search_term}' on Indeed in {country['name']}: {e}")
                    continue
        else:
            print(f"\n  [SKIP] Indeed scraping skipped for this country")

        # ===== LINKEDIN SCRAPING (improved notebook approach) =====
        if run_linkedin:
            # LinkedIn uses batch approach: run all queries for a country, then process
            print(f"\n  [LINKEDIN Platform] (improved sequential approach)")
            print(f"    Running {len(SEARCH_TERMS)} queries with {LINKEDIN_SLEEP_SEC}s delay between each...")
            
            linkedin_df = scrape_linkedin_for_country(
                queries=SEARCH_TERMS,
                location=country["name"],
                results_per_query=jobs_per_term,
                hours_old=HOURS_OLD_LINKEDIN,  # LinkedIn-specific time filter
                sleep_sec=LINKEDIN_SLEEP_SEC,
                max_errors=LINKEDIN_MAX_ERRORS,
                fetch_description=LINKEDIN_FETCH_DESCRIPTION,
            )
            
            if linkedin_df is not None and len(linkedin_df) > 0:
                # Apply two-tier filter with detailed tracking (per search term)
                original_count = len(linkedin_df)
                
                # Track per search term for LinkedIn
                for search_term in SEARCH_TERMS:
                    term_df = linkedin_df[linkedin_df.get('search_term_used') == search_term]
                    if len(term_df) == 0:
                        continue
                    
                    term_results = term_df.apply(
                        lambda row: matches_optimization_keywords_detailed(
                            row.get('title', ''), 
                            row.get('description', '')
                        ), 
                        axis=1
                    )
                    
                    filter_stats.record(
                        search_term,
                        found=len(term_df),
                        rejected_negative=sum(1 for r in term_results if r["rejected_negative"]),
                        accepted_tier1_only=sum(1 for r in term_results if r["accepted"] and r["tier1_match"] and not r["tier2_match"]),
                        accepted_tier2_only=sum(1 for r in term_results if r["accepted"] and r["tier2_match"] and not r["tier1_match"]),
                        accepted_both=sum(1 for r in term_results if r["accepted"] and r["tier1_match"] and r["tier2_match"]),
                        rejected_no_match=sum(1 for r in term_results if not r["accepted"] and not r["rejected_negative"])
                    )
                
                # Filter the full dataframe
                linkedin_df = linkedin_df[linkedin_df.apply(
                    lambda row: matches_optimization_keywords(
                        row.get('title', ''), 
                        row.get('description', '')
                    ), 
                    axis=1
                )]
                filtered_count = len(linkedin_df)
                rejected_count = original_count - filtered_count
                
                print(f"    [OK] LinkedIn total: {original_count} jobs -> {filtered_count} relevant (filtered {rejected_count} non-OR)")
                
                if filtered_count > 0:
                    # Process LinkedIn jobs with their individual search terms
                    # NOTE: Skill extraction is done at Stage 3 (Enrichment + Standardization)
                    new_jobs = process_jobs_dataframe(
                        linkedin_df, "linkedin", country_key, None,  # search_term from df
                        seen_urls,
                        hours_old_filter=HOURS_OLD_LINKEDIN,  # LinkedIn-specific date filter
                    )
                    country_jobs.extend(new_jobs)
                    country_stats[country_key]["linkedin"] += len(new_jobs)
                    
                    # Update final counts per search term for LinkedIn
                    for job in new_jobs:
                        if job.search_term:
                            filter_stats.record(job.search_term, final=1)
            else:
                print(f"    [WARNING] No LinkedIn jobs found for {country['name']}")
        else:
            print(f"\n  [SKIP] LinkedIn scraping skipped for this country")

        if country_jobs:
            # Store to database
            stored = db.store_details(country_jobs)
            print(f"\n  [STORED] {stored} jobs from {country['name']}")
            all_jobs.extend(country_jobs)
        
        # Add delay between countries to avoid overwhelming LinkedIn
        # Important when scraping multiple countries in sequence
        if country_key != target_countries[-1]:  # Skip delay after last country
            delay = 5  # 5 second delay between countries
            print(f"\n  [COOLDOWN] Waiting {delay}s before next country to avoid rate limiting...")
            time.sleep(delay)

    # Print summary
    print(f"\n" + "=" * 70)
    print(f"[OK] SCRAPING COMPLETE!")
    print(f"=" * 70)
    print(f"\n[STATS] Results by Country & Platform:")
    for country_key, stats in country_stats.items():
        if country_key in COUNTRIES:
            total = stats["indeed"] + stats["linkedin"]
            print(f"  {country_key}: {total} jobs (Indeed: {stats['indeed']}, LinkedIn: {stats['linkedin']})")
    
    # Platform totals
    total_indeed = sum(s["indeed"] for s in country_stats.values())
    total_linkedin = sum(s["linkedin"] for s in country_stats.values())
    
    print(f"\n📈 Total Jobs Scraped: {len(all_jobs)}")
    print(f"  - Indeed: {total_indeed}")
    print(f"  - LinkedIn: {total_linkedin}")
    print(f"  - Unique URLs: {len(seen_urls)}")
    if len(all_jobs) > len(seen_urls):
        print(f"  ⚠️  Warning: {len(all_jobs) - len(seen_urls)} duplicate URLs detected (this shouldn't happen)")
    print(f"[DATABASE] Data stored in: data/jobs.db")
    print(f"=" * 70)

    # Print filter effectiveness analysis
    filter_stats.print_summary()

    print("\n📊 Next steps:")
    print("  1. Export to CSV: python export_to_csv.py")
    print("  2. Analyze skills distribution in the exported CSV")
    print("  3. Filter by country or search term in Excel/Google Sheets")

    return all_jobs


async def main():
    parser = argparse.ArgumentParser(
        description="Multi-Country Optimization Job Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_netherlands_indeed_linkedin.py --jobs 50
  python run_netherlands_indeed_linkedin.py --jobs 30 --countries "USA,UK,Germany"
  python run_netherlands_indeed_linkedin.py --jobs 20 --countries "Netherlands"
  python run_netherlands_indeed_linkedin.py --batch --delay 60  # All countries with 60min delays
  python run_netherlands_indeed_linkedin.py --batch --delay 30 --countries "USA,UK,Germany"
        """
    )

    parser.add_argument(
        "--jobs",
        type=int,
        default=50,
        help="Fallback job limit for countries without specific targets (default: 50). Most countries use predefined targets based on market size.",
    )
    
    parser.add_argument(
        "--countries",
        type=str,
        default=None,
        help="Comma-separated list of countries (default: all 10 countries). Options: USA,Canada,UK,Netherlands,Germany,France,Australia,India",
    )
    
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: scrape countries one-by-one with delays between each to avoid rate limiting. Recommended for scraping multiple countries.",
    )
    
    parser.add_argument(
        "--delay",
        type=int,
        default=60,
        help="Delay in MINUTES between countries in batch mode (default: 60). Recommended: 30-90 minutes to avoid LinkedIn blocking.",
    )

    args = parser.parse_args()
    
    # Parse countries filter
    countries_filter = None
    if args.countries:
        countries_filter = [c.strip() for c in args.countries.split(",")]
        # Validate countries
        for c in countries_filter:
            if c not in COUNTRIES:
                print(f"[ERROR] Unknown country: {c}")
                print(f"Available countries: {', '.join(COUNTRIES.keys())}")
                sys.exit(1)

    print(f"\nStart time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        if args.batch:
            # BATCH MODE: Scrape countries one-by-one with delays
            target_countries = countries_filter if countries_filter else list(COUNTRIES.keys())
            
            print(f"\n{'='*70}")
            print(f"[BATCH MODE] Scraping {len(target_countries)} countries sequentially")
            print(f"Delay between countries: {args.delay} minutes")
            print(f"Estimated total time: {len(target_countries) * args.delay} minutes (~{len(target_countries) * args.delay / 60:.1f} hours)")
            print(f"{'='*70}\n")
            
            all_jobs = []
            for i, country in enumerate(target_countries, 1):
                print(f"\n{'='*70}")
                print(f"[BATCH {i}/{len(target_countries)}] Starting: {country}")
                print(f"{'='*70}")
                
                # Scrape single country (uses defaults per platform)
                jobs = await scrape_optimization_jobs(
                    num_jobs=args.jobs,
                    countries_filter=[country],
                    countries_filter_indeed=None,
                    countries_filter_linkedin=None
                )
                all_jobs.extend(jobs)
                
                # Delay before next country (skip after last one)
                if i < len(target_countries):
                    next_country = target_countries[i]
                    print(f"\n{'='*70}")
                    print(f"[BATCH DELAY] Waiting {args.delay} minutes before {next_country}")
                    print(f"Next country starts at: {(datetime.now() + timedelta(minutes=args.delay)).strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"Progress: {i}/{len(target_countries)} countries completed")
                    print(f"{'='*70}")
                    
                    # Sleep in 1-minute increments to allow Ctrl+C interruption
                    for minute in range(args.delay):
                        await asyncio.sleep(60)
                        remaining = args.delay - minute - 1
                        if remaining > 0 and (minute + 1) % 10 == 0:  # Progress update every 10 minutes
                            print(f"  ... {remaining} minutes remaining until next country")
            
            print(f"\n{'='*70}")
            print(f"[BATCH COMPLETE] Scraped {len(target_countries)} countries")
            print(f"Total jobs collected: {len(all_jobs)}")
            print(f"{'='*70}")
            
        else:
            # NORMAL MODE: Use defaults (platform-specific countries) or override with --countries
            # If countries_filter is None, scraper uses:
            #   - DEFAULT_COUNTRIES_INDEED for Indeed (all 10 countries)
            #   - DEFAULT_COUNTRIES_LINKEDIN for LinkedIn (USA, Germany, Netherlands)
            jobs = await scrape_optimization_jobs(
                num_jobs=args.jobs,
                countries_filter=countries_filter,
                countries_filter_indeed=None,
                countries_filter_linkedin=None
            )
        
        print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except KeyboardInterrupt:
        print("\n[WARNING] Scraping interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
