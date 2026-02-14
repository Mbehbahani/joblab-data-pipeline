# Job Relevance Score - Feature Documentation

## Overview
Added **`keyword_frequency`** and **`job_relevance_score`** columns to jobs_enriched.db to quantify job relevance based on filter keyword quality and their repetition in job title and description.

---

## New Columns

### 1. `keyword_frequency` (INTEGER)
Counts how many times tier1 and tier2 keywords appear in the job title (`actual_role`) and description (`job_description_clean`).

### 2. `job_relevance_score` (INTEGER, 1-10)
A comprehensive score combining keyword tier presence and frequency.

---

## Scoring Logic (1-10 Scale)

### Base Score (from Keyword Tiers)

| Base Score | Criteria | Description |
|------------|----------|-------------|
| **4** | Tier1 + Tier2 | Job has BOTH tier1 AND tier2 keywords |
| **3** | Tier1 Premium | Tier1 contains "optim" OR has multiple keywords (>1) |
| **2** | Tier1 Basic | Tier1 has single keyword (no premium) |
| **2** | Tier2 Multiple | Tier2 has multiple keywords (>1) |
| **1** | Tier2 Basic | Tier2 has single keyword only |
| **0** | No Keywords | No filter keywords assigned |

### Frequency Bonus (based on keyword occurrences in title/description)

| Bonus | Occurrences | Description |
|-------|-------------|-------------|
| **+1** | 1-2 | Minimal keyword presence |
| **+2** | 3-5 | Low keyword presence |
| **+3** | 6-10 | Moderate keyword presence |
| **+4** | 11-15 | Good keyword presence |
| **+5** | 16-20 | High keyword presence |
| **+6** | 21+ | Very high keyword presence |

### Final Score
```
job_relevance_score = min(base_score + frequency_bonus, 10)
```

---

## Implementation Details

### Code Location
- **File**: `taxonomy_standardization.py`
- **Functions**: 
  - `count_keyword_frequency(tier1, tier2, title, description)` - Counts keyword occurrences
  - `calculate_relevance_score(tier1, tier2, keyword_freq)` - Calculates 1-10 score
- **Task**: TASK 6 (added after Task 5: company_industry_std)

### High-Value Keywords (Tier1)
Currently defined: `["optim"]`
- Can be expanded to include other critical terms (e.g., "gurobi", "cplex", "milp")

### Example Calculations

```python
# Score 10: Both tiers + high frequency (22 occurrences)
tier1 = "scheduling"
tier2 = "scheduling optimization"
keyword_frequency = 22
→ Base: 4 + Bonus: 6 = Score: 10

# Score 9: Both tiers + high frequency (19 occurrences)
tier1 = "operations research"
tier2 = "operations research, integer programming"
keyword_frequency = 19
→ Base: 4 + Bonus: 5 = Score: 9

# Score 7: Premium tier1 + moderate frequency
tier1 = "optim"
tier2 = "operations research, linear programming"
keyword_frequency = 6
→ Base: 4 + Bonus: 3 = Score: 7

# Score 5: Single tier1 + moderate frequency
tier1 = "analytics"
tier2 = ""
keyword_frequency = 6
→ Base: 2 + Bonus: 3 = Score: 5

# Score 3: Tier2 multiple + low frequency
tier1 = ""
tier2 = "supply chain optimization, demand planning"
keyword_frequency = 2
→ Base: 2 + Bonus: 1 = Score: 3

# Score 2: Single tier2 + minimal frequency
tier1 = ""
tier2 = "operations research"
keyword_frequency = 1
→ Base: 1 + Bonus: 1 = Score: 2
```

---

## Distribution Results

**Database**: jobs_enriched.db (599 jobs, 34 columns)

### Keyword Frequency Distribution

| Occurrences | Count | Percentage |
|-------------|-------|------------|
| **0** | 29 | 4.8% |
| **1-2** | 234 | 39.1% |
| **3-5** | 154 | 25.7% |
| **6-10** | 109 | 18.2% |
| **11-15** | 56 | 9.3% |
| **16-20** | 10 | 1.7% |
| **21+** | 7 | 1.2% |

**Average Frequency**: 4.66 | **Max Frequency**: 28

### Job Relevance Score Distribution (1-10 Scale)

| Score | Count | Percentage | Interpretation |
|-------|-------|------------|----------------|
| **10** | 5 | 0.8% | Perfect match |
| **9** | 11 | 1.8% | Excellent |
| **8** | 23 | 3.8% | Very high |
| **7** | 54 | 9.0% | High |
| **6** | 91 | 15.2% | Good+ |
| **5** | 65 | 10.9% | Good |
| **4** | 91 | 15.2% | Medium+ |
| **3** | 89 | 14.9% | Medium |
| **2** | 168 | 28.0% | Low |
| **1** | 2 | 0.3% | Very low |

**Statistics**:
- Average Score: **4.26**
- Median Score: **4**

---

## Use Cases

### 1. Job Filtering & Prioritization
```sql
-- Get top-tier jobs (score 8-10)
SELECT * FROM jobs WHERE job_relevance_score >= 8;

-- Get high-relevance jobs with good keyword frequency
SELECT * FROM jobs 
WHERE job_relevance_score >= 6 AND keyword_frequency >= 5;

-- Filter by both metrics
SELECT actual_role, keyword_frequency, job_relevance_score 
FROM jobs 
ORDER BY job_relevance_score DESC, keyword_frequency DESC;
```

### 2. Dashboard Segmentation
- **Premium** (Score 8-10): 39 jobs (6.5%)
- **High** (Score 6-7): 145 jobs (24.2%)
- **Medium** (Score 4-5): 156 jobs (26.0%)
- **Low** (Score 1-3): 259 jobs (43.2%)

### 3. Quality Control
- Identify jobs with no keyword frequency (score 0) for review
- Monitor distribution over time for scraping quality
- Analyze correlation between frequency and relevance

### 4. Weighted Analytics
```python
# Weight analysis by relevance
weighted_avg = (industry_count * relevance_score).sum() / relevance_score.sum()

# Analyze keyword density by industry
df.groupby('company_industry_std')['keyword_frequency'].mean()
```

---

## Sample High-Relevance Jobs (Score 8-10)

1. **Supervisor, Shift Scheduling** (Score: 10)
   - Tier1: scheduling
   - Tier2: scheduling optimization
   - Keyword Frequency: 22

2. **Operations Research Analyst, Associate** (Score: 9)
   - Tier1: operations research
   - Tier2: operations research, integer programming
   - Keyword Frequency: 19

3. **Sr Data Scientist - Machine Learning** (Score: 8)
   - Tier1: data scientist, machine learning
   - Tier2: operations research, linear programming
   - Keyword Frequency: 12

---

## Integration Points

### Pipeline Stage
- Added in **Step 3: Enrichment & Standardization**
- Runs after Tasks 1-5 (taxonomy standardization)
- Before database save

### Report Generation
- Included in Report3.txt
- Shows frequency distribution and score distribution
- Sample jobs by score tier

### Database Schema
```sql
-- New columns added
keyword_frequency INTEGER      -- Range: 0-28+
job_relevance_score INTEGER    -- Range: 1-10
```

---

## Future Enhancements

### 1. Expand High-Value Keywords
```python
JOB_RELEVANCE_TIER1_HIGH_VALUE = [
    "optim",
    "gurobi", 
    "cplex",
    "milp",
    "integer programming"
]
```

### 2. Weighted Keyword Scoring
```python
# Different weights for title vs description
title_weight = 2.0
description_weight = 1.0
weighted_frequency = (title_matches * title_weight) + (desc_matches * description_weight)
```

### 3. Exact vs Partial Matching
```python
# Bonus for exact keyword matches in title
if keyword.lower() == title.lower():
    score += 1
```

### 4. Domain-Specific Scoring
```python
# Different scoring for different search terms
if search_term == "Gurobi":
    # Boost jobs mentioning Gurobi specifically
    score += bonus
```

### 5. Contextual Frequency Analysis
```python
# Weight frequency by keyword specificity
rare_keyword_bonus = 1.5  # "gurobi" is more specific than "analytics"
```

---

## Technical Notes

### Dependencies
- pandas (for DataFrame operations)
- Standard Python (no external libraries needed)

### Performance
- Computation time: ~1-2 seconds for 599 jobs
- In-memory processing (no additional database queries)

### Validation
- All 599 jobs received a score
- No NULL values
- Range validated: 0-5 only

---

## Related Fields

### Source Columns
- `filter_tier1_keywords` - Source for tier1 scoring
- `filter_tier2_keywords` - Source for tier2 scoring
- `actual_role` - Job title used for frequency counting
- `job_description_clean` - Description used for frequency counting

### Complementary Columns
- `job_function_std` - Can correlate with relevance
- `search_term` - Original search context
- `company_industry_std` - Industry context

### Potential Correlations
- Higher scores may correlate with specific industries (Technology & Software)
- Certain job_function_std categories may have higher average scores
- Remote jobs might have different score distributions
- Higher keyword frequency often correlates with more detailed job descriptions

---

## Maintenance

### Adding New High-Value Keywords
1. Edit `JOB_RELEVANCE_TIER1_HIGH_VALUE` list in taxonomy_standardization.py
2. Re-run pipeline
3. Compare score distribution before/after

### Adjusting Scoring Rules
1. Modify `calculate_relevance_score()` function
2. Adjust frequency bonus thresholds if needed
3. Update this documentation
4. Re-run pipeline on full dataset
5. Validate distribution changes

### Adjusting Frequency Counting
1. Modify `count_keyword_frequency()` function
2. Consider adding weighted counting (title vs description)
3. Re-run and validate

---

**Created**: January 9, 2026  
**Updated**: January 9, 2026  
**Version**: 2.0  
**Status**: Production

### Changelog
- **v2.0** (2026-01-09): 
  - Added `keyword_frequency` column
  - Changed score range from 0-5 to 1-10
  - Score now combines base tier points + frequency bonus
- **v1.0** (2026-01-09): Initial implementation with 0-5 scale
