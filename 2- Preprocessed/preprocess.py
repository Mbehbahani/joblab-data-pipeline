"""
Job Description Preprocessing Pipeline (v2.2)
==============================================
STEP 0A: Posted Date Null Handling
STEP 0B: URL-Based Duplicate Detection
STEP 1: Minimal Text Normalization (Language-Safe)
STEP 2: Language Detection (uses existing 'language' column if available)
STEP 3: NLP Feature Extraction:
   - Education Field (Industrial Engineering vs Other Engineering vs others, with "Other Fields" fallback)
   - Education Level (as SET - multiple levels)
   - Is Research/Academic Position

Following best practices:
- ❌ NO aggressive stemming, stopword removal, TF-IDF
- ✅ Light normalization that preserves multilingual meaning
- ✅ Remove noise without harming signal
- ✅ Standardized "NA" for all unknown/missing values
- ✅ Replace null posted_date with scraped_at (date of scraping)

Input: input/jobs.db
Output: output/jobs_processed.db (new processed database)
"""

import sqlite3
import pandas as pd
import re
import html
import shutil
from datetime import datetime
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================
INPUT_DB_PATH = Path(__file__).parent.parent / "1- Scrapped Data" / "jobs.db"
OUTPUT_DIR = Path(__file__).parent
BACKUP_DIR = Path(__file__).parent / "backups"
OUTPUT_DB_PATH = OUTPUT_DIR / "jobs_processed.db"

# Standard value for unknown/missing data (for dashboard compatibility)
NA_VALUE = "NA"

# ============================================================================
# STEP 1: TEXT NORMALIZATION FUNCTIONS
# ============================================================================

class TextNormalizer:
    """
    Light text normalization that is language-safe.
    Designed for multilingual job descriptions going into embedding models.
    """
    
    # Boilerplate patterns to remove (case-insensitive)
    BOILERPLATE_PATTERNS = [
        # EEO / Legal disclaimers
        r"equal\s+opportunity\s+employer[^.]*\.",
        r"we\s+are\s+an?\s+equal\s+opportunity[^.]*\.",
        r"eoe[\s/,\-]*(m/f|minority|disability|veteran|protected)[^.]*\.",
        r"all\s+qualified\s+applicants\s+will\s+receive\s+consideration[^.]*\.",
        r"we\s+do\s+not\s+discriminate[^.]*\.",
        r"(affirmative\s+action|aa)\s*/?\s*(equal\s+opportunity|eoe)[^.]*\.",
        
        # About company sections (often generic)
        r"#{1,4}\s*about\s+(the\s+)?company.*?(?=#{1,4}|\Z)",
        r"#{1,4}\s*about\s+us.*?(?=#{1,4}|\Z)",
        r"#{1,4}\s*who\s+we\s+are.*?(?=#{1,4}|\Z)",
        r"\*{2}about\s+(the\s+)?company\*{2}.*?(?=\*{2}[a-z]|\Z)",
        r"\*{2}about\s+us\*{2}.*?(?=\*{2}[a-z]|\Z)",
        
        # Privacy / Cookie footers
        r"privacy\s+(policy|notice)[^.]*\.",
        r"cookie\s+(policy|notice|preferences)[^.]*\.",
        r"by\s+(applying|submitting)[^.]*consent[^.]*\.",
        
        # Application instructions (noise for embeddings)
        r"click\s+(here\s+)?to\s+apply[^.]*\.",
        r"apply\s+now[^.]*\.",
        r"to\s+apply[,:]?\s+(please\s+)?(submit|send|visit|click)[^.]*\.",
        
        # Generic job posting footers
        r"this\s+job\s+(posting|description)\s+(is|may)[^.]*\.",
        r"salary\s+(range|information)[^.]*\.",
        r"compensation[^.]*\$[\d,]+[^.]*\.",
    ]
    
    def __init__(self, remove_boilerplate: bool = True, lowercase: bool = True):
        self.remove_boilerplate = remove_boilerplate
        self.lowercase = lowercase
        
        # Compile boilerplate patterns for efficiency
        self._boilerplate_compiled = [
            re.compile(p, re.IGNORECASE | re.DOTALL) 
            for p in self.BOILERPLATE_PATTERNS
        ]
    
    def normalize(self, text: str) -> str:
        """Apply all normalization steps in order."""
        if pd.isna(text) or not text:
            return ""
        
        text = str(text)
        
        # 1. Decode HTML entities
        text = self._decode_html(text)
        
        # 2. Remove HTML tags (if any)
        text = self._remove_html_tags(text)
        
        # 3. Remove markdown formatting but keep structure
        text = self._clean_markdown(text)
        
        # 4. Normalize whitespace
        text = self._normalize_whitespace(text)
        
        # 5. Remove boilerplate sections
        if self.remove_boilerplate:
            text = self._remove_boilerplate(text)
        
        # 6. Lowercase (optional, but recommended for embeddings)
        if self.lowercase:
            text = text.lower()
        
        # 7. Final whitespace cleanup
        text = self._normalize_whitespace(text)
        
        return text.strip()
    
    def _decode_html(self, text: str) -> str:
        """Decode HTML entities."""
        return html.unescape(text)
    
    def _remove_html_tags(self, text: str) -> str:
        """Remove HTML tags while preserving text content."""
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<(br|p|div|h[1-6]|li|tr)[^>]*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        return text
    
    def _clean_markdown(self, text: str) -> str:
        """Remove markdown formatting while preserving content."""
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
        text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'^[\-\*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[\-\*\+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\\([*_`\[\]()#+-.])', r'\1', text)
        return text
    
    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace without destroying paragraph structure."""
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' *\n *', '\n', text)
        return text
    
    def _remove_boilerplate(self, text: str) -> str:
        """Remove boilerplate sections that add noise for embeddings."""
        for pattern in self._boilerplate_compiled:
            text = pattern.sub('', text)
        return text


# ============================================================================
# STEP 2: LANGUAGE DETECTION
# ============================================================================

class LanguageDetector:
    """
    Detect language of job descriptions.
    Uses langdetect with fallback handling.
    """
    
    def __init__(self):
        self._langdetect_available = False
        
        try:
            from langdetect import detect, DetectorFactory
            DetectorFactory.seed = 42
            self._langdetect_available = True
            self._detect_func = detect
            print("✓ Using langdetect for language detection")
        except ImportError:
            print("⚠ langdetect not available. Install with: pip install langdetect")
    
    def detect(self, text: str) -> str:
        """
        Detect language of text.
        Returns ISO 639-1 code (en, de, fr, etc.) or NA_VALUE.
        """
        if pd.isna(text) or not text or len(text.strip()) < 20:
            return NA_VALUE
        
        text = str(text).strip()
        
        if self._langdetect_available:
            try:
                return self._detect_func(text)
            except Exception:
                return NA_VALUE
        
        return NA_VALUE


# ============================================================================
# STEP 3: NLP FEATURE EXTRACTION
# ============================================================================

class FeatureExtractor:
    """
    Extract structured features from job descriptions using NLP.
    - Education Field (with Industrial Engineering distinction)
    - Education Level (as SET - multiple levels)
    - Is Research/Academic Position
    """
    
    # Education level patterns (ordered by priority)
    EDUCATION_LEVEL_PATTERNS = {
        'PhD': [
            r'\bph\.?d\.?\b', r'\bdoctora(l|te)\b', r'\bdoctor\s+of\s+philosophy\b',
            r'\bdr\.\s*(of\s+)?(engineering|science)\b'
        ],
        'Master': [
            r"\bmaster'?s?\b", r'\bm\.?s\.?\b(?!\s*office)', r'\bm\.?sc\.?\b',
            r'\bm\.?a\.?\b(?!\s+position)', r'\bm\.?eng\.?\b', r'\bmba\b',
            r'\bgraduate\s+degree\b'
        ],
        'Bachelor': [
            r"\bbachelor'?s?\b", r'\bb\.?s\.?\b(?!\s*in\s+business)', r'\bb\.?sc\.?\b',
            r'\bb\.?a\.?\b(?!\s+position)', r'\bb\.?eng\.?\b', r'\bundergraduate\s+degree\b',
            r'\b4[\-\s]?year\s+degree\b'
        ],
        'Associate': [
            r"\bassociate'?s?\s+degree\b", r'\ba\.?s\.?\s+degree\b',
            r'\b2[\-\s]?year\s+(college\s+)?degree\b'
        ],
        'High School': [
            r'\bhigh\s+school\b', r'\bged\b', r'\bhighschool\b',
            r'\bsecondary\s+(school\s+)?education\b'
        ]
    }
    
    # Education field patterns
    EDUCATION_FIELD_PATTERNS = {
        'Industrial Engineering': [
            r'\bindustrial\s+engineer(ing)?\b',
            r'\bmanufacturing\s+engineer(ing)?\b',
            r'\boperations\s+research\b',
            r'\bproduction\s+engineer(ing)?\b',
            r'\bsystems\s+engineer(ing)?\b',
            r'\bprocess\s+engineer(ing)?\b',
            r'\bquality\s+engineer(ing)?\b',
            r'\bsupply\s+chain\s+(management|engineer(ing)?)\b',
            r'\bwork\s+study\b',
            r'\bergonomics\b',
            r'\bie\s+degree\b',
            r'\blean\s+(manufacturing|six\s+sigma)\b',
        ],
        'Other Engineering': [
            r'\b(mechanical|electrical|civil|chemical|software|computer|aerospace|'
            r'biomedical|environmental|materials|nuclear|petroleum|agricultural|'
            r'marine|mining|structural|automotive|robotics)\s+engineer(ing)?\b',
            r'\bengineering\s+(degree|background|discipline)\b',
            r'\bengineer(ing)?\s+(field|major|program)\b',
        ],
        'Computer Science': [
            r'\bcomputer\s+science\b', r'\bcs\s+degree\b', r'\binformatics\b',
            r'\bsoftware\s+development\b', r'\bdata\s+science\b', r'\bai\b',
            r'\bmachine\s+learning\b', r'\bartificial\s+intelligence\b',
        ],
        'Business': [
            r'\bbusiness\s+(administration|management)\b', r'\bmba\b',
            r'\bfinance\b', r'\baccounting\b', r'\bmarketing\b', r'\beconomics\b',
            r'\bmanagement\s+(degree|background)\b',
        ],
        'Mathematics and Statistics': [
            r'\bmathematics\b', r'\bmath\s+degree\b', r'\bstatistics\b',
            r'\bstatistical\s+(analysis|methods)\b', r'\bapplied\s+math(ematics)?\b',
            r'\bcomputational\s+math(ematics)?\b', r'\bquantitative\s+(methods|analysis)\b',
        ],
        'Science': [
            r'\b(physics|chemistry|biology)\b',
            r'\bnatural\s+science\b', r'\blife\s+science\b', r'\bstem\b',
        ],
        'Medicine': [
            r'\bmedicine\b', r'\bmedical\s+degree\b', r'\bmd\s+degree\b',
            r'\bphysician\b', r'\bdoctor\s+of\s+medicine\b',
        ],
        'Healthcare/Nursing': [
            r'\bnursing\b', r'\bnurse\b', r'\brn\s+degree\b',
            r'\bpharmacy\b', r'\bpharmacist\b', r'\bhealthcare\s+management\b',
            r'\bpublic\s+health\b', r'\bhealth\s+administration\b',
        ],
    }
    
    # Research and academic position patterns
    RESEARCH_PATTERNS = [
        r'\bprofessor\b', r'\bassociate\s+professor\b', r'\bassistant\s+professor\b',
        r'\blecturer\b', r'\bsenior\s+lecturer\b', r'\bteaching\s+assistant\b',
        r'\bresearch\s+(fellow|associate|scientist|assistant)\b',
        r'\bpostdoc(toral)?\b', r'\bpost[\-\s]?doc(toral)?\b',
        r'\bfaculty\s+(position|member)\b', r'\btenure[\-\s]?track\b',
        r'\bacademic\s+(position|role|career)\b', r'\buniversity\s+(position|role)\b',
        r'\bphd\s+(student|candidate|position)\b', r'\bdoctoral\s+(student|candidate)\b',
        r'\bresearch\s+chair\b', r'\bendowed\s+chair\b', r'\bdean\b',
        r'\bdepartment\s+(head|chair)\b', r'\bprincipal\s+investigator\b',
        r'\blab\s+(director|manager|head)\b', r'\bvisiting\s+(scholar|professor|researcher)\b',
    ]
    

    
    def __init__(self):
        # Compile patterns for efficiency
        self._education_level_compiled = {
            level: [re.compile(p, re.IGNORECASE) for p in patterns]
            for level, patterns in self.EDUCATION_LEVEL_PATTERNS.items()
        }
        
        self._education_field_compiled = {
            field: [re.compile(p, re.IGNORECASE) for p in patterns]
            for field, patterns in self.EDUCATION_FIELD_PATTERNS.items()
        }
        
        self._research_compiled = [
            re.compile(p, re.IGNORECASE) for p in self.RESEARCH_PATTERNS
        ]
    
    def extract_education_levels(self, text: str) -> str:
        """
        Extract education levels as a SET (comma-separated string).
        Returns all matching levels, not just the highest.
        """
        if pd.isna(text) or not text:
            return NA_VALUE
        
        text = str(text)
        found_levels = set()
        
        for level, patterns in self._education_level_compiled.items():
            for pattern in patterns:
                if pattern.search(text):
                    found_levels.add(level)
                    break  # Don't need multiple matches for same level
        
        if not found_levels:
            return NA_VALUE
        
        # Sort by education hierarchy for consistent output
        hierarchy = ['PhD', 'Master', 'Bachelor', 'Associate', 'High School']
        sorted_levels = [lvl for lvl in hierarchy if lvl in found_levels]
        
        return ', '.join(sorted_levels)
    
    def extract_education_field(self, text: str) -> str:
        """
        Extract education field with distinction between Industrial Engineering 
        and Other Engineering fields. Returns 'Other Fields' if no specific field detected.
        """
        if pd.isna(text) or not text:
            return NA_VALUE
        
        text = str(text)
        found_fields = []
        
        # Check Industrial Engineering FIRST (more specific)
        for pattern in self._education_field_compiled['Industrial Engineering']:
            if pattern.search(text):
                found_fields.append('Industrial Engineering')
                break
        
        # Then check other fields
        for field, patterns in self._education_field_compiled.items():
            if field == 'Industrial Engineering':
                continue  # Already checked
            for pattern in patterns:
                if pattern.search(text):
                    if field not in found_fields:
                        found_fields.append(field)
                    break
        
        if not found_fields:
            return 'Other Fields'
        
        return ', '.join(found_fields)
    
    def extract_is_research(self, text: str, company_name: str = None) -> int:
        """
        Determine if this is a research or academic position.
        Returns 1 for research/academic, 0 for non-research.
        """
        if pd.isna(text) or not text:
            return 0
        
        text = str(text)
        
        # Check text for research/academic patterns
        for pattern in self._research_compiled:
            if pattern.search(text):
                return 1
        
        # Also check company name for universities/institutions
        if company_name and not pd.isna(company_name):
            company_lower = str(company_name).lower()
            academic_orgs = [
                'university', 'college', 'institute', 'school of',
                'faculty', 'department of', 'research center', 'research centre',
                'laboratory', 'national lab', 'academy', 'polytechnic'
            ]
            for org in academic_orgs:
                if org in company_lower:
                    return 1
        
        return 0


# ============================================================================
# JOB LEVEL REFINEMENT
# ============================================================================

def refine_job_level(current_level: str, title: str, description: str = None) -> str:
    """
    Refine job level by analyzing the job title more carefully.
    
    LinkedIn often provides "Mid-Senior level" as a combined category.
    This function distinguishes between Mid-Level and Senior based on title patterns.
    
    Args:
        current_level: The existing job_level value (from LinkedIn or extraction)
        title: The actual_role (job title)
        description: Optional job description for additional context
        
    Returns:
        Refined job level: Internship, Entry Level, Associate, Mid-Level, Senior, Director, Executive
    """
    if pd.isna(title) or not title:
        return current_level if current_level else "Mid-Level"
    
    title_lower = str(title).lower().strip()
    current_lower = str(current_level).lower() if current_level else ""
    
    # Already specific levels - keep them
    if current_lower in ["internship", "entry level", "director", "executive"]:
        return current_level
    
    # === SENIOR INDICATORS (in title) ===
    senior_title_keywords = [
        "senior", "sr.", "sr ", "lead ", "lead,", "principal", 
        "staff engineer", "staff scientist", "staff developer",
        "team lead", "tech lead", "technical lead",
        "head of", "chief", "vp ", "vice president",
    ]
    
    # === MID-LEVEL INDICATORS (in title) ===
    mid_level_title_keywords = [
        "analyst", "specialist", "coordinator", "associate", 
        "engineer", "scientist", "developer", "consultant",
        "researcher", "advisor", "planner"
    ]
    
    # === ENTRY LEVEL INDICATORS ===
    entry_keywords = [
        "junior", "jr.", "jr ", "entry", "graduate", "trainee",
        "new grad", "fresher", "apprentice"
    ]
    
    # === INTERNSHIP INDICATORS ===
    intern_keywords = ["intern", "internship", "co-op", "coop"]
    
    # Check internship first
    if any(kw in title_lower for kw in intern_keywords):
        return "Internship"
    
    # Check entry level
    if any(kw in title_lower for kw in entry_keywords):
        return "Entry Level"
    
    # Check senior indicators in title
    is_senior = any(kw in title_lower for kw in senior_title_keywords)
    
    # Check if has mid-level role without senior prefix
    has_mid_level_role = any(kw in title_lower for kw in mid_level_title_keywords)
    
    # Director/Executive - specific titles
    if any(kw in title_lower for kw in ["director", "vp ", "vice president"]):
        return "Director"
    if any(kw in title_lower for kw in ["chief", "cto", "ceo", "cfo", "coo"]):
        return "Executive"
    
    # Manager detection
    has_manager = "manager" in title_lower
    
    # Decision logic
    if is_senior:
        return "Senior"
    elif has_manager:
        # Managers without "senior" prefix are typically Senior level
        return "Senior"
    elif "associate" in title_lower and "senior" not in title_lower:
        return "Associate"
    elif has_mid_level_role:
        # Generic roles like "Analyst", "Engineer" without senior = Mid-Level
        return "Mid-Level"
    elif current_lower in ["mid-senior level", "mid senior level"]:
        # LinkedIn's "Mid-Senior level" - default to Mid-Level unless senior indicators found
        return "Mid-Level"
    else:
        return current_level if current_level else "Mid-Level"



# ============================================================================
# MAIN PREPROCESSING PIPELINE
# ============================================================================

def run_preprocessing_pipeline():
    """
    Main preprocessing pipeline for job descriptions.
    Reads from input/jobs.db, outputs to output/jobs_processed.db
    """
    print("=" * 80)
    print("JOB DESCRIPTION PREPROCESSING PIPELINE v2.0")
    print("=" * 80)
    print(f"Started: {datetime.now()}")
    print(f"Input:   {INPUT_DB_PATH}")
    print(f"Output:  {OUTPUT_DB_PATH}\n")
    
    # -------------------------------------------------------------------------
    # 0. Validate input and create directories
    # -------------------------------------------------------------------------
    if not INPUT_DB_PATH.exists():
        print(f"❌ ERROR: Input database not found at {INPUT_DB_PATH}")
        print("   Please ensure jobs.db exists in the 'input' folder.")
        return None
    
    OUTPUT_DIR.mkdir(exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)
    
    # Create backup of input
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"jobs_input_{timestamp}.db"
    shutil.copy2(INPUT_DB_PATH, backup_path)
    print(f"📦 Backup created: {backup_path}\n")
    
    # Copy input to output (we'll modify the output copy)
    shutil.copy2(INPUT_DB_PATH, OUTPUT_DB_PATH)
    
    # -------------------------------------------------------------------------
    # Load data
    # -------------------------------------------------------------------------
    conn = sqlite3.connect(OUTPUT_DB_PATH)
    df = pd.read_sql_query("SELECT * FROM jobs;", conn)
    print(f"📂 Loaded {len(df)} jobs from database\n")
    
    # Get existing columns
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(jobs);")
    existing_cols = [col[1] for col in cursor.fetchall()]
    print(f"   Existing columns: {existing_cols}\n")
    
    # Check if language column exists in source data
    has_language_column = 'language' in existing_cols
    
    # -------------------------------------------------------------------------
    # STEP 0A: Posted Date Null Handling
    # -------------------------------------------------------------------------
    print("=" * 80)
    print("STEP 0A: Posted Date Null Handling")
    print("=" * 80)
    
    # Count null posted_dates before fixing
    null_posted_count = df['posted_date'].isna().sum()
    print(f"   Null posted_date values found: {null_posted_count} ({null_posted_count/len(df)*100:.1f}%)")
    
    # Ensure is_posted_date_assigned column exists
    if 'is_posted_date_assigned' not in df.columns:
        df['is_posted_date_assigned'] = 0
    
    if null_posted_count > 0:
        # Safety net: Replace null posted_date with scraped_at and mark as assigned
        null_mask = df['posted_date'].isna()
        df.loc[null_mask, 'posted_date'] = df.loc[null_mask, 'scraped_at']
        df.loc[null_mask, 'is_posted_date_assigned'] = 1
        print(f"   ✓ Replaced {null_posted_count} null posted_date values with scraped_at (marked is_posted_date_assigned=1)\n")
    else:
        print("   ✓ No null posted_date values found\n")
    
    # Report how many dates were assigned by scraper
    assigned_count = (df['is_posted_date_assigned'] == 1).sum()
    if assigned_count > 0:
        print(f"   ℹ️  Total jobs with assigned dates (from scraper + preprocessing): {assigned_count} ({assigned_count/len(df)*100:.1f}%)\n")
    
    # -------------------------------------------------------------------------
    # STEP 0B: URL-Based Duplicate Detection
    # -------------------------------------------------------------------------
    print("=" * 80)
    print("STEP 0B: URL-Based Duplicate Detection")
    print("=" * 80)
    
    df['url_clean'] = df['url'].fillna('').str.lower().str.rstrip('/')
    df['company_url_clean'] = df['company_url'].fillna('').str.lower().str.rstrip('/') if 'company_url' in df.columns else ''
    
    df['has_url_duplicate'] = 0
    
    # Mark duplicate URLs
    if 'url' in df.columns:
        url_dup_mask = df['url_clean'].isin(
            df[df.duplicated('url_clean', keep=False) & (df['url_clean'] != '')]['url_clean']
        )
        df.loc[url_dup_mask, 'has_url_duplicate'] = 1
    
    # Mark duplicate company URLs
    if 'company_url' in df.columns:
        company_url_dup_mask = df['company_url_clean'].isin(
            df[df.duplicated('company_url_clean', keep=False) & (df['company_url_clean'] != '')]['company_url_clean']
        )
        df.loc[company_url_dup_mask, 'has_url_duplicate'] = 1
    
    total_url_duplicates = (df['has_url_duplicate'] == 1).sum()
    print(f"   URL duplicates found: {total_url_duplicates} jobs ({total_url_duplicates/len(df)*100:.1f}%)\n")
    
    # -------------------------------------------------------------------------
    # Initialize processors
    # -------------------------------------------------------------------------
    normalizer = TextNormalizer(remove_boilerplate=True, lowercase=True)
    lang_detector = LanguageDetector()
    feature_extractor = FeatureExtractor()
    
    # -------------------------------------------------------------------------
    # STEP 1: Text Normalization
    # -------------------------------------------------------------------------
    print("=" * 80)
    print("STEP 1: Text Normalization")
    print("=" * 80)
    print("Processing: HTML decode → Remove tags → Clean markdown → Normalize whitespace → Remove boilerplate → Lowercase\n")
    
    df["job_description_clean"] = df["job_description"].apply(normalizer.normalize)
    
    orig_lengths = df["job_description"].fillna("").str.len()
    clean_lengths = df["job_description_clean"].str.len()
    reduction_pct = ((orig_lengths - clean_lengths) / orig_lengths.replace(0, 1) * 100).mean()
    
    print(f"📊 Normalization Statistics:")
    print(f"   Original avg length: {orig_lengths.mean():.0f} chars")
    print(f"   Cleaned avg length:  {clean_lengths.mean():.0f} chars")
    print(f"   Average reduction:   {reduction_pct:.1f}%\n")
    
    # -------------------------------------------------------------------------
    # STEP 2: Language Detection
    # -------------------------------------------------------------------------
    print("=" * 80)
    print("STEP 2: Language Detection")
    print("=" * 80)
    
    if has_language_column and df['language'].notna().any():
        print("Using existing 'language' column from source data")
        df["detected_language"] = df["language"].fillna(NA_VALUE).replace('', NA_VALUE)
    else:
        print("Detecting languages from job descriptions...")
        df["detected_language"] = df["job_description_clean"].apply(lang_detector.detect)
    
    print(f"\n📊 Language Distribution:")
    lang_dist = df["detected_language"].value_counts()
    for lang, count in lang_dist.head(10).items():
        print(f"   {lang:10} : {count:4d} ({count/len(df)*100:.1f}%)")
    
    # -------------------------------------------------------------------------
    # STEP 3: NLP Feature Extraction
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STEP 3: NLP Feature Extraction")
    print("=" * 80)
    
    # Education Level (as SET)
    print("\nExtracting education levels (as SET)...")
    df["education_level"] = df["job_description"].apply(feature_extractor.extract_education_levels)
    
    edu_level_dist = df["education_level"].value_counts().head(10)
    print("📊 Education Level Distribution (top 10):")
    for level, count in edu_level_dist.items():
        print(f"   {level:30} : {count:4d} ({count/len(df)*100:.1f}%)")
    
    # Education Field (with Industrial Engineering distinction)
    print("\nExtracting education fields (Industrial Engineering vs Other)...")
    df["education_field"] = df["job_description"].apply(feature_extractor.extract_education_field)
    
    edu_field_dist = df["education_field"].value_counts().head(10)
    print("📊 Education Field Distribution (top 10):")
    for field, count in edu_field_dist.items():
        print(f"   {field:40} : {count:4d} ({count/len(df)*100:.1f}%)")
    
    # Is Research/Academic Position
    print("\nIdentifying research/academic positions...")
    df["is_research"] = df.apply(
        lambda row: feature_extractor.extract_is_research(
            row["job_description"], 
            row.get("company_name", None)
        ), axis=1
    )
    
    research_count = df["is_research"].sum()
    print(f"📊 Research/Academic Positions: {research_count} ({research_count/len(df)*100:.1f}%)")
    
    # -------------------------------------------------------------------------
    # STEP 4: Job Level Refinement
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STEP 4: Job Level Refinement")
    print("=" * 80)
    print("Refining job levels: Distinguishing Mid-Level vs Senior based on title analysis\n")
    
    # Store original job_level for comparison
    original_levels = df['job_level'].copy()
    
    # Apply refinement
    df['job_level_refined'] = df.apply(
        lambda row: refine_job_level(
            row['job_level'],
            row['actual_role'],
            row.get('job_description', None)
        ), axis=1
    )
    
    # Show before/after comparison
    print("📊 Job Level Distribution (Before → After Refinement):")
    original_dist = original_levels.value_counts()
    refined_dist = df['job_level_refined'].value_counts()
    
    all_levels = set(original_dist.index) | set(refined_dist.index)
    for level in sorted(all_levels):
        orig_count = original_dist.get(level, 0)
        new_count = refined_dist.get(level, 0)
        change = new_count - orig_count
        change_str = f"+{change}" if change > 0 else str(change) if change < 0 else "="
        print(f"   {level:20} : {orig_count:4d} → {new_count:4d} ({change_str})")
    
    # Replace job_level with refined version
    df['job_level'] = df['job_level_refined']
    df.drop(columns=['job_level_refined'], inplace=True)
    
    print(f"\n   ✓ Job levels refined successfully")
    
    # -------------------------------------------------------------------------
    # Standardize NA values
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STANDARDIZING MISSING VALUES")
    print("=" * 80)
    print(f"Using standard value for all unknown/missing: '{NA_VALUE}'")
    
    # List of columns to standardize
    text_columns = [
        'detected_language', 'education_level', 'education_field',
        'job_level', 'job_function', 'job_type',
        'company_industry', 'skills', 'company_name', 'location', 'country'
    ]
    
    for col in text_columns:
        if col in df.columns:
            # Replace empty strings, None, NaN with NA_VALUE
            df[col] = df[col].fillna(NA_VALUE)
            df[col] = df[col].replace('', NA_VALUE)
            df[col] = df[col].replace('unknown', NA_VALUE)
            df[col] = df[col].replace('Unknown', NA_VALUE)
    
    print("   ✓ All unknown/empty values standardized to 'NA'\n")
    
    # -------------------------------------------------------------------------
    # Save to Database
    # -------------------------------------------------------------------------
    print("=" * 80)
    print("SAVING TO DATABASE")
    print("=" * 80)
    
    # Define new columns to add
    new_columns = {
        'has_url_duplicate': 'INTEGER DEFAULT 0',
        'is_posted_date_assigned': 'INTEGER DEFAULT 0',
        'job_description_clean': 'TEXT',
        'detected_language': 'TEXT',
        'education_level': 'TEXT',
        'education_field': 'TEXT',
        'is_research': 'INTEGER DEFAULT 0'
    }
    
    # Add columns if they don't exist
    for col_name, col_type in new_columns.items():
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type};")
                print(f"   ✓ Added column: {col_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    print(f"   ⚠ Error adding {col_name}: {e}")
    
    # Update values (including refined job_level and posted_date)
    print("\n   Updating records...")
    for idx, row in df.iterrows():
        cursor.execute(
            """UPDATE jobs SET 
               has_url_duplicate = ?,
               is_posted_date_assigned = ?,
               job_description_clean = ?, 
               detected_language = ?,
               education_level = ?,
               education_field = ?,
               is_research = ?,
               job_level = ?,
               posted_date = ?
               WHERE job_id = ?;""",
            (
                int(row["has_url_duplicate"]),
                int(row["is_posted_date_assigned"]),
                row["job_description_clean"],
                row["detected_language"],
                row["education_level"],
                row["education_field"],
                int(row["is_research"]),
                row["job_level"],  # Refined job level
                row["posted_date"],  # Updated posted_date (null replaced with scraped_at)
                row["job_id"]
            )
        )
    
    conn.commit()
    print("   ✓ All changes committed to database")
    
    # Verify
    verify_df = pd.read_sql_query(
        """SELECT job_id, detected_language, education_level, education_field, 
           is_research 
           FROM jobs LIMIT 5;""", 
        conn
    )
    print("\n📋 Verification (first 5 rows):")
    print(verify_df.to_string(index=False))
    
    conn.close()
    
    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("✅ PREPROCESSING COMPLETE")
    print("=" * 80)
    print(f"""
Summary:
  • Jobs processed: {len(df)}
  • Input:  {INPUT_DB_PATH}
  • Output: {OUTPUT_DB_PATH}
  • Backup: {backup_path}

Posted Date Handling:
  • Null posted_date values: {null_posted_count} ({null_posted_count/len(df)*100:.1f}%)
  • Jobs with assigned dates (is_posted_date_assigned=1): {assigned_count} ({assigned_count/len(df)*100:.1f}%)
  
URL Duplicates:
  • Found: {total_url_duplicates} ({total_url_duplicates/len(df)*100:.1f}%)
  
New Columns Added:
  • has_url_duplicate         - 0/1 flag for URL duplicates
  • is_posted_date_assigned   - 0/1 flag: date was assigned (not from source)
  • job_description_clean     - Normalized text for embeddings
  • detected_language         - ISO 639-1 code or 'NA'
  • education_level           - SET of levels (e.g., "PhD, Master, Bachelor")
  • education_field           - Field (Industrial Engineering / Other Engineering / etc. / Other Fields)
  • is_research               - 0/1 flag for research/academic positions
  
Research/Academic Positions: {research_count} ({research_count/len(df)*100:.1f}%)

Standard Value for Missing Data: '{NA_VALUE}'

Next Steps:
  1. Run embedding_pipeline.py to generate embeddings
  2. Run taxonomy.py for taxonomy standardization
  3. Run validate_preprocessing.py to verify results
""")
    
    return df


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    df = run_preprocessing_pipeline()
