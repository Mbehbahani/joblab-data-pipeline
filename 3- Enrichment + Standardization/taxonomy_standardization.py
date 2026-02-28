#!/usr/bin/env python3
"""
Taxonomy Standardization & Missing Value Imputation Pipeline
=============================================================
Based on prompt.txt recommendations (Hybrid Approach - Scenario 3)

Tasks:
  1. job_type_filled       - Fill missing values using pattern matching
  2. edu_level_filled      - Fill NA values using patterns + similarity
  3. job_level_std         - Standardize 12 values → 7 categories
  4. job_function_std      - Standardize 43 values → 14 categories  
  5. company_industry_std  - Standardize 96 values → 15 categories
  6. skills                - Update skills with category-level extraction (30 categories)
  7. job_relevance_score   - Score job relevance based on filter keywords

Input:  jobs_processed.db (from Stage 2: Preprocessing)
Output: jobs_enriched.db (with all new standardized columns)
"""

import sqlite3
import pandas as pd
import re
from pathlib import Path
from datetime import datetime
from collections import Counter
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================
INPUT_DB_PATH = Path(__file__).parent.parent / "2- Preprocessed" / "jobs_processed.db"
OUTPUT_DB_PATH = Path(__file__).parent / "jobs_enriched.db"
REPORT_PATH = Path(__file__).parent / "Report3.txt"
SKILLS_REFERENCE_PATH = Path(__file__).parent.parent / "src" / "config" / "skills_reference.json"
TOOLS_REFERENCE_PATH = Path(__file__).parent.parent / "src" / "config" / "tools_reference.json"

NA_VALUE = "NA"

# ============================================================================
# STANDARD TAXONOMIES
# ============================================================================

# Task 1: Job Type - 5 standard categories
JOB_TYPE_TAXONOMY = {
    "Full-time": ["fulltime", "full-time", "full time", "permanent", "fte", "regular"],
    "Part-time": ["parttime", "part-time", "part time", "pte"],
    "Contract": ["contract", "contractor", "consulting", "freelance", "temporary", "temp"],
    "Internship": ["intern", "internship", "co-op", "coop", "trainee", "apprentice"],
    "Other": []
}

JOB_TYPE_PATTERNS = {
    "Full-time": r'\b(full[\s\-]?time|permanent|fte)\b',
    "Part-time": r'\b(part[\s\-]?time|pte)\b',
    "Contract": r'\b(contract(or)?|consulting|freelance|temporary|temp\b)',
    "Internship": r'\b(intern(ship)?|co[\s\-]?op|trainee|apprentice)\b',
}

# Task 2: Education Level - 5 standard categories (already exists, just fill NAs)
EDUCATION_LEVEL_TAXONOMY = ["PhD", "Master", "Bachelor", "Associate", "High School"]

# Task 3: Job Level - 7 standard categories
JOB_LEVEL_TAXONOMY = {
    "Internship": ["internship", "intern"],
    "Entry Level": ["entry level", "entry-level", "graduate", "junior", "fresher"],
    "Associate": ["associate"],
    "Mid-Level": ["mid-level", "mid level", "intermediate"],
    "Senior": ["senior", "sr.", "lead", "principal", "staff", "mid-senior level"],
    "Director": ["director", "head of", "vp", "vice president"],
    "Executive": ["executive", "c-level", "chief", "cto", "ceo", "cfo", "coo"],
    "Not Specified": ["not applicable", "na", ""]
}

JOB_LEVEL_MAPPING = {
    # Internship
    "internship": "Internship",
    "Internship": "Internship",
    # Entry Level
    "entry level": "Entry Level",
    "Entry level": "Entry Level",
    "Entry Level": "Entry Level",
    # Associate
    "associate": "Not Specified",
    "Associate": "Not Specified",
    # Mid-Level (from preprocessing refinement)
    "mid-level": "Mid-Level",
    "Mid-Level": "Mid-Level",
    "Mid-level": "Mid-Level",
    "mid level": "Mid-Level",
    # Senior (including legacy Mid-Senior level from LinkedIn)
    "senior": "Senior",
    "Senior": "Senior",
    "senior level": "Senior",
    "Senior level": "Senior",
    "Senior Level": "Senior",
    "mid-senior level": "Senior",  # Legacy LinkedIn - if not refined, default to Senior
    "Mid-Senior level": "Senior",
    "Mid-senior level": "Senior",
    # Director
    "director": "Director",
    "Director": "Director",
    # Executive
    "executive": "Executive",
    "Executive": "Executive",
    # Not Specified
    "not applicable": "Not Specified",
    "Not Applicable": "Not Specified",
    "NA": "Not Specified",
    "": "Not Specified",
}

# Task 4: Job Function - 14 standard categories
JOB_FUNCTION_TAXONOMY = {
    "Engineering": [
        "engineering", "software", "hardware", "developer", "programmer",
        "engineering and information technology", "information technology and engineering"
    ],
    "Data Science & Analytics": [
        "data science", "analytics", "analyst", "data analyst", "business intelligence",
        "research, analyst", "analyst,"
    ],
    "Operations Research": [
        "operations research", "optimization", "or specialist"
    ],
    "Research & Development": [
        "research", "r&d", "scientist", "research and engineering", "science and research"
    ],
    "Supply Chain & Logistics": [
        "supply chain", "logistics", "procurement", "purchasing", "distribution",
        "manufacturing", "production", "quality assurance"
    ],
    "Information Technology": [
        "information technology", "it ", "it services", "infrastructure", "network",
        "systems admin"
    ],
    "Product Management": [
        "product management", "product manager", "product owner"
    ],
    "Business & Finance": [
        "finance", "accounting", "business", "investment", "banking",
        "accounting/auditing"
    ],
    "Consulting": [
        "consulting", "consultant", "advisory"
    ],
    "Sales & Marketing": [
        "sales", "marketing", "business development", "customer"
    ],
    "Healthcare": [
        "health care", "healthcare", "medical", "clinical", "nursing"
    ],
    "Education": [
        "education", "training", "teaching", "academic"
    ],
    "Management": [
        "management", "manager", "administrative", "strategy"
    ],
    "Other": []
}

# Task 5: Company Industry - 15 standard categories
JOB_RELEVANCE_TIER1_HIGH_VALUE = ["optim"]  # High-value tier1 keywords

# Additional optimization-related keywords to search in descriptions
# These are searched IN ADDITION to tier1/tier2 keywords
OPTIMIZATION_KEYWORDS = [
    # Solvers & Libraries
    "gurobi", "cplex", "pyomo", "or-tools", "ortools", "pulp", "scipy",
    "cvxpy", "gekko", "amplpy", "mosek", "xpress", "cbc", "glpk", "scip",
    # Optimization Types
    "optimization", "optimisation", "linear programming", "integer programming",
    "mixed integer", "milp", "mip", "quadratic programming", "convex optimization",
    "combinatorial optimization", "constraint programming", "stochastic optimization",
    "dynamic programming", "network optimization", "vehicle routing", "scheduling",
    # Methods
    "heuristic", "metaheuristic", "genetic algorithm", "simulated annealing",
    "tabu search", "branch and bound", "simplex", "interior point",
    # Operations Research
    "operations research", "operational research", "mathematical modeling",
    "decision science", "prescriptive analytics",
]

COMPANY_INDUSTRY_TAXONOMY = {
    "Technology & Software": [
        "software", "internet", "technology", "it services", "computer",
        "information services", "saas", "cloud", "digital"
    ],
    "Healthcare & Pharmaceuticals": [
        "health care", "healthcare", "pharmaceutical", "biotech", "medical",
        "hospital", "clinical", "life science"
    ],
    "Finance & Banking": [
        "financial", "banking", "insurance", "investment", "fintech",
        "capital", "asset management"
    ],
    "Manufacturing & Industrial": [
        "manufacturing", "industrial", "production", "machinery", "equipment",
        "appliances", "electrical", "electronics"
    ],
    "Retail & E-commerce": [
        "retail", "e-commerce", "ecommerce", "consumer goods", "marketplace"
    ],
    "Consulting & Professional Services": [
        "consulting", "professional services", "business services", "advisory",
        "human resources"
    ],
    "Transportation & Logistics": [
        "transportation", "logistics", "shipping", "freight", "airline",
        "aviation", "ground passenger"
    ],
    "Energy & Utilities": [
        "energy", "oil", "gas", "utilities", "power", "renewable", "nuclear",
        "electric"
    ],
    "Telecommunications": [
        "telecom", "telecommunications", "wireless", "network"
    ],
    "Education & Research": [
        "education", "school", "university", "research services", "academic"
    ],
    "Government & Public Sector": [
        "government", "public sector", "federal", "state", "municipal"
    ],
    "Aerospace & Defense": [
        "aerospace", "defense", "space", "military", "aviation and aerospace"
    ],
    "Automotive": [
        "automotive", "motor vehicle", "car", "auto"
    ],
    "Food & Beverage": [
        "food", "beverage", "restaurant", "hospitality"
    ],
    "Other": []
}


# ============================================================================
# TASK 1: job_type_filled - Fill missing job_type values
# ============================================================================

def standardize_job_type(existing_value: str) -> str:
    """Map existing job_type value to standard category"""
    if pd.isna(existing_value) or not existing_value or existing_value.strip() == "":
        return None  # Will need to infer from description
    
    value_lower = str(existing_value).lower()
    
    # Check for multiple types (e.g., "fulltime, internship")
    found_types = set()
    for std_type, keywords in JOB_TYPE_TAXONOMY.items():
        if std_type == "Other":
            continue
        for kw in keywords:
            if kw in value_lower:
                found_types.add(std_type)
                break
    
    if found_types:
        # Priority: Full-time > Part-time > Contract > Internship
        priority = ["Full-time", "Part-time", "Contract", "Internship"]
        for p in priority:
            if p in found_types:
                return p
    
    return "Full-time"  # Default


def infer_job_type_from_description(description: str) -> str:
    """Infer job type from job description using patterns"""
    if pd.isna(description) or not description:
        return "Full-time"  # Default (71.1% are full-time)
    
    desc_lower = str(description).lower()
    
    # Check patterns in order of specificity
    for job_type, pattern in JOB_TYPE_PATTERNS.items():
        if re.search(pattern, desc_lower, re.IGNORECASE):
            return job_type
    
    return "Full-time"  # Default


def fill_job_type(df: pd.DataFrame) -> pd.DataFrame:
    """Fill job_type_filled column"""
    print("\n" + "="*80)
    print("TASK 1: job_type_filled")
    print("="*80)
    
    def process_row(row):
        # First try to standardize existing value
        std_value = standardize_job_type(row['job_type'])
        if std_value:
            return std_value
        # If no existing value, infer from description
        return infer_job_type_from_description(row['job_description_clean'])
    
    df['job_type_filled'] = df.apply(process_row, axis=1)
    
    # Statistics
    original_filled = df['job_type'].apply(lambda x: pd.notna(x) and str(x).strip() != '').sum()
    new_filled = df['job_type_filled'].notna().sum()
    
    print(f"  Original filled: {original_filled} ({original_filled/len(df)*100:.1f}%)")
    print(f"  After filling:   {new_filled} ({new_filled/len(df)*100:.1f}%)")
    print(f"\n  Distribution:")
    for val, count in df['job_type_filled'].value_counts().items():
        print(f"    {val:<20}: {count:>5} ({count/len(df)*100:.1f}%)")
    
    return df


# ============================================================================
# TASK 2: edu_level_filled - Fill NA education levels
# ============================================================================

EDUCATION_PATTERNS = {
    'PhD': [r'\bph\.?d\.?\b', r'\bdoctora(l|te)\b', r'\bdoctor\s+of\s+philosophy\b'],
    'Master': [r"\bmaster'?s?\b", r'\bm\.?s\.?\b(?!\s*office)', r'\bm\.?sc\.?\b', r'\bmba\b', r'\bgraduate\s+degree\b'],
    'Bachelor': [r"\bbachelor'?s?\b", r'\bb\.?s\.?\b', r'\bb\.?sc\.?\b', r'\bundergraduate\s+degree\b', r'\b4[\-\s]?year\s+degree\b'],
    'Associate': [r"\bassociate'?s?\s+degree\b", r'\b2[\-\s]?year\s+degree\b'],
    'High School': [r'\bhigh\s+school\b', r'\bged\b']
}


def extract_education_from_description(description: str) -> str:
    """Extract education level from description using patterns"""
    if pd.isna(description) or not description:
        return NA_VALUE
    
    text = str(description).lower()
    found_levels = []
    
    hierarchy = ['PhD', 'Master', 'Bachelor', 'Associate', 'High School']
    
    for level in hierarchy:
        for pattern in EDUCATION_PATTERNS.get(level, []):
            if re.search(pattern, text, re.IGNORECASE):
                found_levels.append(level)
                break
    
    if found_levels:
        return ', '.join(found_levels)
    return NA_VALUE


def fill_education_level(df: pd.DataFrame) -> pd.DataFrame:
    """Fill edu_level_filled column"""
    print("\n" + "="*80)
    print("TASK 2: edu_level_filled")
    print("="*80)
    
    def process_row(row):
        # If existing value is valid (not NA), keep it
        existing = row['education_level']
        if pd.notna(existing) and str(existing).strip() != '' and str(existing) != NA_VALUE:
            return existing
        # Try to extract from description
        return extract_education_from_description(row['job_description'])
    
    df['edu_level_filled'] = df.apply(process_row, axis=1)
    
    # Statistics
    original_na = (df['education_level'] == NA_VALUE).sum() + df['education_level'].isna().sum()
    new_na = (df['edu_level_filled'] == NA_VALUE).sum() + df['edu_level_filled'].isna().sum()
    
    print(f"  Original NA: {original_na} ({original_na/len(df)*100:.1f}%)")
    print(f"  After fill NA: {new_na} ({new_na/len(df)*100:.1f}%)")
    print(f"  Filled: {original_na - new_na} additional records")
    print(f"\n  Top 10 Distribution:")
    for val, count in df['edu_level_filled'].value_counts().head(10).items():
        print(f"    {val:<35}: {count:>5} ({count/len(df)*100:.1f}%)")
    
    return df


# ============================================================================
# TASK 3: job_level_std - Standardize job levels
# ============================================================================

def standardize_job_level(existing_value: str, description: str = None) -> str:
    """Map job_level to standard category"""
    if pd.isna(existing_value) or not existing_value:
        existing_value = ""
    
    value_lower = str(existing_value).lower().strip()
    
    # Direct mapping
    if value_lower in JOB_LEVEL_MAPPING:
        return JOB_LEVEL_MAPPING[value_lower]
    
    # Fuzzy matching
    for std_level, keywords in JOB_LEVEL_TAXONOMY.items():
        for kw in keywords:
            if kw in value_lower:
                return std_level
    
    # If still not found and we have description, try to infer
    if description and pd.notna(description):
        desc_lower = str(description).lower()
        # Check for level indicators in description
        if re.search(r'\b(senior|sr\.|lead|principal|staff)\b', desc_lower):
            return "Senior"
        elif re.search(r'\b(junior|jr\.|entry|graduate|fresher)\b', desc_lower):
            return "Entry Level"
        elif re.search(r'\b(director|head\s+of|vp|vice\s+president)\b', desc_lower):
            return "Director"
        elif re.search(r'\b(intern|internship)\b', desc_lower):
            return "Internship"
    
    return "Mid-Level"  # Default


def standardize_job_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize job_level to job_level_std column"""
    print("\n" + "="*80)
    print("TASK 3: job_level_std")
    print("="*80)
    
    df['job_level_std'] = df.apply(
        lambda row: standardize_job_level(row['job_level'], row.get('job_description', '')), 
        axis=1
    )
    
    # Statistics
    print(f"  Original unique values: {df['job_level'].nunique()}")
    print(f"  Standardized categories: {df['job_level_std'].nunique()}")
    print(f"\n  Mapping results:")
    
    # Show mapping
    mapping_df = df.groupby(['job_level', 'job_level_std']).size().reset_index(name='count')
    mapping_df = mapping_df.sort_values('count', ascending=False)
    for _, row in mapping_df.iterrows():
        print(f"    {str(row['job_level']):<25} → {row['job_level_std']:<15} ({row['count']:>4})")
    
    print(f"\n  Final Distribution:")
    for val, count in df['job_level_std'].value_counts().items():
        print(f"    {val:<20}: {count:>5} ({count/len(df)*100:.1f}%)")
    
    return df


# ============================================================================
# TASK 4: job_function_std - Standardize job functions
# ============================================================================

def standardize_job_function(existing_value: str, description: str = None) -> str:
    """Map job_function to standard category"""
    if pd.isna(existing_value) or not existing_value:
        existing_value = ""
    
    value_lower = str(existing_value).lower().strip()
    
    # Score each category
    scores = {cat: 0 for cat in JOB_FUNCTION_TAXONOMY.keys()}
    
    for category, keywords in JOB_FUNCTION_TAXONOMY.items():
        if category == "Other":
            continue
        for kw in keywords:
            if kw in value_lower:
                scores[category] += 1
    
    # Also check description for tie-breakers
    if description and pd.notna(description):
        desc_lower = str(description).lower()[:1000]  # First 1000 chars
        for category, keywords in JOB_FUNCTION_TAXONOMY.items():
            if category == "Other":
                continue
            for kw in keywords:
                if kw in desc_lower:
                    scores[category] += 0.5  # Lower weight for description
    
    # Get best match
    best_cat = max(scores, key=scores.get)
    if scores[best_cat] > 0:
        return best_cat
    
    return "Other"


def standardize_job_functions(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize job_function to job_function_std column"""
    print("\n" + "="*80)
    print("TASK 4: job_function_std")
    print("="*80)
    
    df['job_function_std'] = df.apply(
        lambda row: standardize_job_function(row['job_function'], row.get('job_description_clean', '')), 
        axis=1
    )
    
    # Statistics
    print(f"  Original unique values: {df['job_function'].nunique()}")
    print(f"  Standardized categories: {df['job_function_std'].nunique()}")
    
    print(f"\n  Final Distribution:")
    for val, count in df['job_function_std'].value_counts().items():
        print(f"    {val:<30}: {count:>5} ({count/len(df)*100:.1f}%)")
    
    return df


# ============================================================================
# TASK 5: company_industry_std - Standardize company industries
# ============================================================================

def standardize_company_industry(existing_value: str, company_name: str = None, description: str = None) -> str:
    """Map company_industry to standard category"""
    # Collect text from all sources
    text_parts = []
    
    if pd.notna(existing_value) and str(existing_value).strip():
        text_parts.append(str(existing_value).lower())
    
    if pd.notna(company_name) and str(company_name).strip():
        text_parts.append(str(company_name).lower())
    
    if pd.notna(description) and str(description).strip():
        text_parts.append(str(description).lower()[:500])  # First 500 chars
    
    combined_text = " ".join(text_parts)
    
    if not combined_text.strip():
        return "Other"
    
    # Score each category
    scores = {cat: 0 for cat in COMPANY_INDUSTRY_TAXONOMY.keys()}
    
    for category, keywords in COMPANY_INDUSTRY_TAXONOMY.items():
        if category == "Other":
            continue
        for kw in keywords:
            # Count occurrences with different weights
            if existing_value and pd.notna(existing_value) and kw in str(existing_value).lower():
                scores[category] += 3  # Highest weight for existing industry
            if company_name and pd.notna(company_name) and kw in str(company_name).lower():
                scores[category] += 2  # Medium weight for company name
            if description and pd.notna(description) and kw in str(description).lower()[:500]:
                scores[category] += 0.5  # Lower weight for description
    
    # Get best match
    best_cat = max(scores, key=scores.get)
    if scores[best_cat] > 0:
        return best_cat
    
    return "Other"


def standardize_company_industries(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize company_industry to company_industry_std column"""
    print("\n" + "="*80)
    print("TASK 5: company_industry_std")
    print("="*80)
    
    # Count original missing
    original_missing = df['company_industry'].apply(
        lambda x: pd.isna(x) or str(x).strip() == ''
    ).sum()
    
    df['company_industry_std'] = df.apply(
        lambda row: standardize_company_industry(
            row['company_industry'], 
            row.get('company_name', ''),
            row.get('job_description_clean', '')
        ), 
        axis=1
    )
    
    # Statistics
    new_other = (df['company_industry_std'] == 'Other').sum()
    
    print(f"  Original missing: {original_missing} ({original_missing/len(df)*100:.1f}%)")
    print(f"  Original unique values: {df['company_industry'].nunique()}")
    print(f"  Standardized 'Other': {new_other} ({new_other/len(df)*100:.1f}%)")
    print(f"  Standardized categories: {df['company_industry_std'].nunique()}")
    
    print(f"\n  Final Distribution:")
    for val, count in df['company_industry_std'].value_counts().items():
        print(f"    {val:<35}: {count:>5} ({count/len(df)*100:.1f}%)")
    
    return df


# ============================================================================
# TASK 6: skills (Update existing column with category-level extraction)
# ============================================================================

def load_skill_extractor():
    """
    Load the skill extractor from the skill_extractor module.
    Falls back to a simple implementation if the module is not available.
    """
    try:
        from src.analysis.skill_extraction.skill_extractor import SkillExtractor
        extractor = SkillExtractor(str(SKILLS_REFERENCE_PATH))
        print(f"  ✓ Loaded SkillExtractor with {extractor.get_skills_count()} skills in {len(extractor.get_all_categories())} categories")
        return extractor
    except ImportError as e:
        print(f"  ⚠ Could not import SkillExtractor: {e}")
        print("  → Using fallback implementation")
        return None


def extract_categories_from_description(description: str, extractor=None) -> str:
    """
    Extract skill categories from job description using the comprehensive skills_reference.json.
    
    Returns categories (not individual skills) for higher-level granularity.
    
    Args:
        description: Job description text (cleaned)
        extractor: SkillExtractor instance or None for fallback
        
    Returns:
        Comma-separated string of extracted skill categories
    """
    if pd.isna(description) or not description:
        return ""
    
    if extractor:
        return extractor.extract_categories_string(str(description))
    
    # Fallback: Simple keyword matching returning categories
    import json
    if not SKILLS_REFERENCE_PATH.exists():
        return ""
    
    try:
        with open(SKILLS_REFERENCE_PATH, 'r', encoding='utf-8') as f:
            skills_data = json.load(f)
        
        skills = skills_data.get('skills', [])
        description_text = str(description)
        found_categories = set()
        
        for skill in skills:
            name = skill.get('name', '')
            category = skill.get('category', '')
            patterns = skill.get('patterns', [])
            
            # Check each pattern
            for pattern in patterns:
                try:
                    if re.search(pattern, description_text, re.IGNORECASE):
                        if category:
                            found_categories.add(category)
                        break
                except re.error:
                    # If regex is invalid, try simple match
                    if name.lower() in description_text.lower():
                        if category:
                            found_categories.add(category)
                        break
        
        return ', '.join(sorted(found_categories))
    except Exception as e:
        print(f"    Warning: Category extraction failed: {e}")
        return ""


def extract_skills(df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract skill categories from job descriptions and UPDATE the existing 'skills' column.
    
    This uses category-level granularity as defined in skills_reference.json,
    providing a standardized skill taxonomy for the database.
    """
    print("\n" + "="*80)
    print("TASK 6: skills (Category-Level Skill Extraction)")
    print("="*80)
    
    print(f"\n  Skills Reference: {SKILLS_REFERENCE_PATH}")
    
    # Load skill extractor
    extractor = load_skill_extractor()
    
    # Use either cleaned description or raw description
    desc_column = 'job_description_clean' if 'job_description_clean' in df.columns else 'job_description'
    print(f"  Using description column: {desc_column}")
    print(f"  Output: Updating existing 'skills' column with category-level values")
    
    # Store original for comparison
    original_skills = df['skills'].copy() if 'skills' in df.columns else None
    
    # Extract categories for each job and UPDATE the existing 'skills' column
    print(f"  Processing {len(df)} job descriptions...")
    
    df['skills'] = df[desc_column].apply(
        lambda desc: extract_categories_from_description(desc, extractor)
    )
    
    # Calculate statistics
    jobs_with_skills = (df['skills'].apply(lambda x: pd.notna(x) and str(x).strip() != '')).sum()
    jobs_with_skills_pct = jobs_with_skills / len(df) * 100
    
    # Count unique categories across all jobs
    all_categories_flat = []
    for cat_str in df['skills'].dropna():
        if cat_str:
            all_categories_flat.extend([c.strip() for c in str(cat_str).split(',') if c.strip()])
    
    category_counts = Counter(all_categories_flat)
    unique_categories = len(category_counts)
    total_category_mentions = len(all_categories_flat)
    avg_categories_per_job = total_category_mentions / len(df) if len(df) > 0 else 0
    
    print(f"\n  Results:")
    print(f"    Jobs with skills extracted: {jobs_with_skills} ({jobs_with_skills_pct:.1f}%)")
    print(f"    Unique categories found:    {unique_categories}")
    print(f"    Total category mentions:    {total_category_mentions}")
    print(f"    Avg categories per job:     {avg_categories_per_job:.1f}")
    
    # Show all category distribution
    print(f"\n  Category Distribution (all {unique_categories} categories):")
    for category, count in category_counts.most_common():
        pct = count / len(df) * 100
        print(f"    {category:<40}: {count:>5} ({pct:>5.1f}%)")
    
    # Compare with original if exists
    if original_skills is not None:
        original_filled = (original_skills.apply(lambda x: pd.notna(x) and str(x).strip() != '' and str(x) != 'NA')).sum()
        print(f"\n  Comparison with Original:")
        print(f"    Original 'skills' column filled: {original_filled} ({original_filled/len(df)*100:.1f}%)")
        print(f"    Updated 'skills' column filled:  {jobs_with_skills} ({jobs_with_skills_pct:.1f}%)")
    
    return df


# ============================================================================
# TASK 6b: tools (Extract optimization tools into separate column)
# ============================================================================

def load_tool_extractor():
    """
    Load the tool extractor from the tool_extractor module.
    Falls back to a simple implementation if the module is not available.
    """
    try:
        from src.analysis.skill_extraction.tool_extractor import ToolExtractor
        extractor = ToolExtractor(str(TOOLS_REFERENCE_PATH))
        print(f"  \u2713 Loaded ToolExtractor with {extractor.get_tools_count()} tools")
        return extractor
    except ImportError as e:
        print(f"  \u26a0 Could not import ToolExtractor: {e}")
        print("  \u2192 Using fallback implementation")
        return None


def extract_tools_from_description(description: str, extractor=None) -> str:
    """
    Extract optimization tools from job description.
    
    Args:
        description: Job description text (cleaned)
        extractor: ToolExtractor instance or None for fallback
        
    Returns:
        Comma-separated string of extracted tool names
    """
    if pd.isna(description) or not description:
        return ""
    
    if extractor:
        return extractor.extract_tools_string(str(description))
    
    # Fallback: simple keyword matching
    import json
    if not TOOLS_REFERENCE_PATH.exists():
        return ""
    
    try:
        with open(TOOLS_REFERENCE_PATH, 'r', encoding='utf-8') as f:
            tools_data = json.load(f)
        
        tools = tools_data.get('tools', [])
        description_text = str(description)
        found_tools = set()
        
        for tool in tools:
            name = tool.get('name', '')
            patterns = tool.get('patterns', [])
            
            for pattern in patterns:
                try:
                    if re.search(pattern, description_text, re.IGNORECASE):
                        found_tools.add(name)
                        break
                except re.error:
                    if name.lower() in description_text.lower():
                        found_tools.add(name)
                        break
        
        return ', '.join(sorted(found_tools))
    except Exception as e:
        print(f"    Warning: Tool extraction failed: {e}")
        return ""


def extract_tools(df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract optimization tools from job descriptions into a new 'tools' column.
    
    This keeps optimization solvers/libraries separate from general skills.
    """
    print("\n" + "="*80)
    print("TASK 6b: tools (Optimization Tool Extraction)")
    print("="*80)
    
    print(f"\n  Tools Reference: {TOOLS_REFERENCE_PATH}")
    
    # Load tool extractor
    extractor = load_tool_extractor()
    
    # Use either cleaned description or raw description
    desc_column = 'job_description_clean' if 'job_description_clean' in df.columns else 'job_description'
    print(f"  Using description column: {desc_column}")
    print(f"  Output: Creating 'tools' column")
    
    # Extract tools for each job
    print(f"  Processing {len(df)} job descriptions...")
    
    df['tools'] = df[desc_column].apply(
        lambda desc: extract_tools_from_description(desc, extractor)
    )
    
    # Calculate statistics
    jobs_with_tools = (df['tools'].apply(lambda x: pd.notna(x) and str(x).strip() != '')).sum()
    jobs_with_tools_pct = jobs_with_tools / len(df) * 100
    
    # Count unique tools across all jobs
    all_tools_flat = []
    for tool_str in df['tools'].dropna():
        if tool_str:
            all_tools_flat.extend([t.strip() for t in str(tool_str).split(',') if t.strip()])
    
    tool_counts = Counter(all_tools_flat)
    unique_tools = len(tool_counts)
    total_tool_mentions = len(all_tools_flat)
    avg_tools_per_job = total_tool_mentions / len(df) if len(df) > 0 else 0
    
    print(f"\n  Results:")
    print(f"    Jobs with tools extracted:  {jobs_with_tools} ({jobs_with_tools_pct:.1f}%)")
    print(f"    Unique tools found:         {unique_tools}")
    print(f"    Total tool mentions:        {total_tool_mentions}")
    print(f"    Avg tools per job:          {avg_tools_per_job:.1f}")
    
    # Show tool distribution
    print(f"\n  Tool Distribution (all {unique_tools} tools):")
    for tool, count in tool_counts.most_common():
        pct = count / len(df) * 100
        print(f"    {tool:<30}: {count:>5} ({pct:>5.1f}%)")
    
    # Also remove tool entries from the 'skills' column to avoid duplication
    if extractor:
        tool_names = set(extractor.get_all_tool_names())
    else:
        tool_names = set()
        try:
            import json
            with open(TOOLS_REFERENCE_PATH, 'r', encoding='utf-8') as f:
                tools_data = json.load(f)
            tool_names = {t['name'] for t in tools_data.get('tools', [])}
        except Exception:
            pass
    
    if tool_names and 'skills' in df.columns:
        # The skills column stores categories, not individual tool names,
        # so tool categories (Pyomo, OR-Tools, etc.) that were previously
        # used as category names are already removed from skills_reference.json.
        # No additional cleanup needed since we updated skills_reference.json.
        print(f"\n  \u2713 Skills column already uses updated skills_reference.json (tools excluded)")
    
    return df


# ============================================================================
# TASK 7: job_relevance_score - Score job relevance based on keywords
# ============================================================================

def count_keyword_frequency(tier1: str, tier2: str, title: str, description: str) -> int:
    """
    Count how many times tier1, tier2, and optimization keywords appear in title and description.
    
    Args:
        tier1: Comma-separated tier1 keywords
        tier2: Comma-separated tier2 keywords
        title: Job title (actual_role)
        description: Job description (job_description_clean)
    
    Returns:
        int: Total count of keyword occurrences in title and description
    """
    # Parse keywords from tier1 and tier2
    tier1_keywords = []
    tier2_keywords = []
    
    if pd.notna(tier1) and str(tier1).strip():
        tier1_keywords = [v.strip().lower() for v in str(tier1).split(',') if v.strip()]
    
    if pd.notna(tier2) and str(tier2).strip():
        tier2_keywords = [v.strip().lower() for v in str(tier2).split(',') if v.strip()]
    
    # Combine tier keywords with additional optimization keywords
    # Use a set to avoid duplicates
    all_keywords = set(tier1_keywords + tier2_keywords)
    all_keywords.update([kw.lower() for kw in OPTIMIZATION_KEYWORDS])
    
    if not all_keywords:
        return 0
    
    # Prepare text for searching
    title_text = str(title).lower() if pd.notna(title) else ''
    desc_text = str(description).lower() if pd.notna(description) else ''
    combined_text = f"{title_text} {desc_text}"
    
    # Count occurrences of each keyword
    total_count = 0
    for keyword in all_keywords:
        # Use word boundary matching for more accurate counting
        # Escape special regex characters in keyword
        escaped_keyword = re.escape(keyword)
        matches = re.findall(r'\b' + escaped_keyword + r'\b', combined_text, re.IGNORECASE)
        total_count += len(matches)
    
    return total_count


def calculate_relevance_score(tier1: str, tier2: str, keyword_freq: int) -> int:
    """
    Calculate job relevance score on a 1-10 scale based on:
    - Tier1 and Tier2 keyword presence
    - Keyword frequency (repetition in title/description)
    
    Scoring Logic (1-10 scale):
    
    Base Score (from keyword tiers):
      - Tier1 + Tier2 present: 4 base points
      - Tier1 only (with high-value or multiple): 3 base points
      - Tier1 only (single, basic): 2 base points  
      - Tier2 multiple: 2 base points
      - Tier2 single: 1 base point
      - No keywords: 0 base points
    
    Frequency Bonus (up to 6 additional points):
      - 1-2 occurrences:  +1 point
      - 3-5 occurrences:  +2 points
      - 6-10 occurrences: +3 points
      - 11-15 occurrences: +4 points
      - 16-20 occurrences: +5 points
      - 21+ occurrences:  +6 points
    
    Final score capped at 10, minimum 1 if any keywords exist.
    """
    # Check if values exist
    has_tier1 = pd.notna(tier1) and str(tier1).strip() != ''
    has_tier2 = pd.notna(tier2) and str(tier2).strip() != ''
    
    if not has_tier1 and not has_tier2:
        return 0
    
    # Parse tier1 and tier2 (comma-separated values)
    tier1_values = []
    tier2_values = []
    
    if has_tier1:
        tier1_values = [v.strip() for v in str(tier1).lower().split(',') if v.strip()]
    
    if has_tier2:
        tier2_values = [v.strip() for v in str(tier2).lower().split(',') if v.strip()]
    
    tier1_count = len(tier1_values)
    tier2_count = len(tier2_values)
    
    # Calculate base score from keyword tiers
    base_score = 0
    
    if tier1_count > 0 and tier2_count > 0:
        # Both tiers present - highest base
        base_score = 4
    elif tier1_count > 0:
        # Check for high-value keywords in tier1
        has_high_value = any(
            any(keyword in val for keyword in JOB_RELEVANCE_TIER1_HIGH_VALUE)
            for val in tier1_values
        )
        if has_high_value or tier1_count > 1:
            base_score = 3
        else:
            base_score = 2
    elif tier2_count > 1:
        base_score = 2
    else:
        base_score = 1
    
    # Calculate frequency bonus
    freq_bonus = 0
    if keyword_freq >= 21:
        freq_bonus = 6
    elif keyword_freq >= 16:
        freq_bonus = 5
    elif keyword_freq >= 11:
        freq_bonus = 4
    elif keyword_freq >= 6:
        freq_bonus = 3
    elif keyword_freq >= 3:
        freq_bonus = 2
    elif keyword_freq >= 1:
        freq_bonus = 1
    
    # Final score (capped at 10)
    final_score = min(base_score + freq_bonus, 10)
    
    return final_score


def add_relevance_score(df: pd.DataFrame) -> pd.DataFrame:
    """Add keyword_frequency and job_relevance_score columns"""
    print("\n" + "="*80)
    print("TASK 6: keyword_frequency & job_relevance_score")
    print("="*80)
    
    # Step 1: Calculate keyword frequency (how many times keywords appear in title/description)
    print("\n  Step 1: Calculating keyword_frequency...")
    df['keyword_frequency'] = df.apply(
        lambda row: count_keyword_frequency(
            row['filter_tier1_keywords'],
            row['filter_tier2_keywords'],
            row['actual_role'],
            row.get('job_description_clean', row.get('job_description', ''))
        ),
        axis=1
    )
    
    print(f"    Keyword Frequency Distribution:")
    freq_ranges = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 15), (16, 20), (21, float('inf'))]
    freq_labels = ['0', '1-2', '3-5', '6-10', '11-15', '16-20', '21+']
    for (low, high), label in zip(freq_ranges, freq_labels):
        if high == float('inf'):
            count = (df['keyword_frequency'] >= low).sum()
        else:
            count = ((df['keyword_frequency'] >= low) & (df['keyword_frequency'] <= high)).sum()
        pct = count / len(df) * 100
        print(f"      {label:>6} occurrences: {count:>4} jobs ({pct:>5.1f}%)")
    
    print(f"\n    Average Frequency: {df['keyword_frequency'].mean():.2f}")
    print(f"    Max Frequency:    {df['keyword_frequency'].max()}")
    
    # Step 2: Calculate job relevance score (1-10 scale)
    print("\n  Step 2: Calculating job_relevance_score (1-10 scale)...")
    df['job_relevance_score'] = df.apply(
        lambda row: calculate_relevance_score(
            row['filter_tier1_keywords'], 
            row['filter_tier2_keywords'],
            row['keyword_frequency']
        ),
        axis=1
    )
    
    # Scoring rules explanation
    print(f"\n  Scoring Rules (1-10 scale):")
    print(f"    Base Score (from keyword tiers):")
    print(f"      - Tier1 + Tier2:                    4 points")
    print(f"      - Tier1 (high-value or multiple):  3 points")
    print(f"      - Tier1 (single, basic):           2 points")
    print(f"      - Tier2 (multiple):                2 points")
    print(f"      - Tier2 (single):                  1 point")
    print(f"      - No keywords:                     0 points")
    print(f"\n    Frequency Bonus:")
    print(f"      - 1-2 occurrences:   +1 point")
    print(f"      - 3-5 occurrences:   +2 points")
    print(f"      - 6-10 occurrences:  +3 points")
    print(f"      - 11-15 occurrences: +4 points")
    print(f"      - 16-20 occurrences: +5 points")
    print(f"      - 21+ occurrences:   +6 points")
    
    print(f"\n  Score Distribution:")
    for score in sorted(df['job_relevance_score'].unique(), reverse=True):
        count = (df['job_relevance_score'] == score).sum()
        pct = count / len(df) * 100
        print(f"    Score {score:>2}: {count:>4} jobs ({pct:>5.1f}%)")
    
    print(f"\n  Average Score: {df['job_relevance_score'].mean():.2f}")
    print(f"  Median Score:  {df['job_relevance_score'].median():.0f}")
    
    # Show examples by score tier
    print(f"\n  Sample Jobs by Score Tier:")
    for score in [10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]:
        sample = df[df['job_relevance_score'] == score].head(1)
        if len(sample) > 0:
            for idx, row in sample.iterrows():
                tier1 = str(row['filter_tier1_keywords'])[:30] if pd.notna(row['filter_tier1_keywords']) else '[none]'
                tier2 = str(row['filter_tier2_keywords'])[:30] if pd.notna(row['filter_tier2_keywords']) else '[none]'
                freq = row['keyword_frequency']
                title = str(row['actual_role'])[:40]
                print(f"    Score {score:>2}: Freq={freq:>2} | {title}")
                print(f"             Tier1: {tier1} | Tier2: {tier2}")
    
    return df


# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(df: pd.DataFrame, output_path: Path):
    """Generate Report3.txt with enrichment statistics"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("="*100 + "\n")
        f.write("ENRICHMENT & STANDARDIZATION REPORT\n")
        f.write("="*100 + "\n\n")
        f.write(f"Generated: {datetime.now()}\n")
        f.write(f"Total Records: {len(df):,}\n\n")
        
        f.write("PIPELINE TASKS:\n")
        f.write("  1. job_type_filled       - Fill missing job types\n")
        f.write("  2. edu_level_filled      - Fill NA education levels\n")
        f.write("  3. job_level_std         - Standardize job levels (12 → 7 categories)\n")
        f.write("  4. job_function_std      - Standardize job functions (43 → 14 categories)\n")
        f.write("  5. company_industry_std  - Standardize industries (96 → 15 categories)\n")
        f.write("  6. skills                - Update with category-level skill extraction (30 categories)\n")
        f.write("  7. job_relevance_score   - Score job relevance based on filter keywords\n\n")
        
        f.write("="*100 + "\n")
        f.write("NEW FIELDS SUMMARY\n")
        f.write("="*100 + "\n\n")
        
        new_fields = ['job_type_filled', 'edu_level_filled', 'job_level_std', 
                      'job_function_std', 'company_industry_std', 'skills', 'job_relevance_score']
        
        f.write(f"{'Field Name':<30} {'Fill Rate':<12} {'Unique Values':<15}\n")
        f.write("-"*100 + "\n")
        
        for field in new_fields:
            if field in df.columns:
                filled = df[field].apply(lambda x: pd.notna(x) and str(x) not in ['', 'NA', 'Other']).sum()
                fill_rate = filled / len(df) * 100
                unique = df[field].nunique()
                f.write(f"{field:<30} {fill_rate:>6.1f}%      {unique:>10,}\n")
        
        f.write("\n\n" + "="*100 + "\n")
        f.write("FIELD DISTRIBUTIONS\n")
        f.write("="*100 + "\n\n")
        
        # Task 1: job_type_filled
        f.write("1. job_type_filled\n")
        f.write("   Method: Pattern matching on existing value + description\n")
        for val, count in df['job_type_filled'].value_counts().items():
            f.write(f"   {val:<20}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write("\n")
        
        # Task 2: edu_level_filled
        f.write("2. edu_level_filled\n")
        f.write("   Method: Keep existing + pattern extraction from description\n")
        for val, count in df['edu_level_filled'].value_counts().head(15).items():
            f.write(f"   {val:<40}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write("\n")
        
        # Task 3: job_level_std
        f.write("3. job_level_std\n")
        f.write("   Method: Manual mapping (12 → 7 categories)\n")
        for val, count in df['job_level_std'].value_counts().items():
            f.write(f"   {val:<20}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write("\n")
        
        # Task 4: job_function_std
        f.write("4. job_function_std\n")
        f.write("   Method: Keyword matching (43 → 14 categories)\n")
        for val, count in df['job_function_std'].value_counts().items():
            f.write(f"   {val:<30}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write("\n")
        
        # Task 5: company_industry_std
        f.write("5. company_industry_std\n")
        f.write("   Method: Keyword matching on industry + company_name + description\n")
        for val, count in df['company_industry_std'].value_counts().items():
            f.write(f"   {val:<35}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write("\n")
        
        # Task 6: skills (category-level extraction)
        f.write("6. skills (updated)\n")
        f.write("   Method: Category-level extraction using skills_reference.json (30 categories)\n")
        if 'skills' in df.columns:
            jobs_with_skills = (df['skills'].apply(lambda x: pd.notna(x) and str(x).strip() != '')).sum()
            f.write(f"   Jobs with skill categories: {jobs_with_skills} ({jobs_with_skills/len(df)*100:.1f}%)\n")
            
            # Count all categories
            all_skills = []
            for skills_str in df['skills'].dropna():
                if skills_str:
                    all_skills.extend([s.strip() for s in str(skills_str).split(',') if s.strip()])
            
            skill_counts = Counter(all_skills)
            f.write(f"   Unique categories found: {len(skill_counts)}\n")
            f.write(f"   Total category mentions: {len(all_skills)}\n")
            f.write(f"   Avg categories per job: {len(all_skills)/len(df):.1f}\n\n")
            
            f.write("   All Categories (sorted by frequency):\n")
            for skill, count in skill_counts.most_common():
                f.write(f"   {skill:<40}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write("\n")
        
        # Task 7: job_relevance_score
        f.write("7. job_relevance_score\n")
        f.write("   Method: Scoring based on filter_tier1_keywords and filter_tier2_keywords\n")
        f.write("   Rules: 5=Both tiers, 4=Tier1 'optim'/multiple, 3=Tier2 multiple, 2=Tier1, 1=Tier2, 0=None\n")
        for score in sorted(df['job_relevance_score'].unique(), reverse=True):
            count = (df['job_relevance_score'] == score).sum()
            f.write(f"   Score {score}: {count:>5} ({count/len(df)*100:.1f}%)\n")
        f.write(f"   Average: {df['job_relevance_score'].mean():.2f}, Median: {df['job_relevance_score'].median():.0f}\n")
        f.write("\n")
        
        # Summary comparison
        f.write("\n" + "="*100 + "\n")
        f.write("BEFORE vs AFTER COMPARISON\n")
        f.write("="*100 + "\n\n")
        
        comparisons = [
            ('job_type', 'job_type_filled'),
            ('education_level', 'edu_level_filled'),
            ('job_level', 'job_level_std'),
            ('job_function', 'job_function_std'),
            ('company_industry', 'company_industry_std'),
            ('skills (original)', 'skills (updated)'),
        ]
        
        f.write(f"{'Original Field':<25} {'Unique':<10} {'New Field':<25} {'Unique':<10}\n")
        f.write("-"*100 + "\n")
        
        for orig, new in comparisons:
            orig_unique = df[orig].nunique() if orig in df.columns else 0
            new_unique = df[new].nunique() if new in df.columns else 0
            f.write(f"{orig:<25} {orig_unique:<10} {new:<25} {new_unique:<10}\n")
        
        f.write("\n" + "="*100 + "\n")
        f.write("END OF REPORT\n")
        f.write("="*100 + "\n")
    
    print(f"\n📄 Report saved to: {output_path}")


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_enrichment_pipeline():
    """Run the full enrichment and standardization pipeline"""
    print("="*80)
    print("TAXONOMY STANDARDIZATION & ENRICHMENT PIPELINE")
    print("="*80)
    print(f"Started: {datetime.now()}")
    print(f"Input:   {INPUT_DB_PATH}")
    print(f"Output:  {OUTPUT_DB_PATH}\n")
    
    # Validate input
    if not INPUT_DB_PATH.exists():
        print(f"❌ ERROR: Input database not found at {INPUT_DB_PATH}")
        return None
    
    # Load data
    conn = sqlite3.connect(INPUT_DB_PATH)
    df = pd.read_sql_query("SELECT * FROM jobs;", conn)
    conn.close()
    df = add_relevance_score(df)
    
    print(f"📂 Loaded {len(df)} jobs from database")
    print(f"   Columns: {len(df.columns)}")
    
    # Run tasks
    df = fill_job_type(df)
    df = fill_education_level(df)
    df = standardize_job_levels(df)
    df = standardize_job_functions(df)
    df = standardize_company_industries(df)
    df = extract_skills(df)  # Task 6: Comprehensive skill extraction
    df = extract_tools(df)   # Task 6b: Optimization tool extraction
    
    # Save to new database, job_relevance_score
    print("\n" + "="*80)
    print("SAVING RESULTS")
    print("="*80)
    
    conn_out = sqlite3.connect(OUTPUT_DB_PATH)
    df.to_sql('jobs', conn_out, if_exists='replace', index=False)
    conn_out.close()
    
    print(f"✅ Saved {len(df)} records to {OUTPUT_DB_PATH}")
    print(f"   Total columns: {len(df.columns)}")
    print(f"   Columns added/updated: job_type_filled, edu_level_filled, job_level_std, job_function_std, company_industry_std, skills (updated)")
    
    # Generate report
    generate_report(df, REPORT_PATH)
    
    # Final summary
    print("\n" + "="*80)
    print("✅ ENRICHMENT COMPLETE")
    print("="*80)
    print(f"""
Summary:
  • keyword_frequency    - Count of keyword occurrences in title/description
  • job_relevance_score  - Job relevance score (1-10 scale)
  • skills               - Category-level skill extraction (30 categories)
  • tools                - Optimization tool extraction (separate column)
  • Jobs processed: {len(df)}
  • Input:  {INPUT_DB_PATH}
  • Output: {OUTPUT_DB_PATH}
  • Report: {REPORT_PATH}
  
New Columns Added:
  • job_type_filled      - Standardized job type (5 categories)
  • edu_level_filled     - Education level with filled NAs
  • job_level_std        - Standardized job level (7 categories)
  • job_function_std     - Standardized job function (14 categories)
  • company_industry_std - Standardized industry (15 categories)
  • skills (updated)     - Category-level extraction (30 categories from skills_reference.json)
  • tools (new)          - Optimization tools (from tools_reference.json)
  • keyword_frequency    - Keyword occurrences in title/description
  • job_relevance_score  - Relevance score (1-10 based on tiers + frequency)
""")
    
    return df


if __name__ == "__main__":
    df = run_enrichment_pipeline()
