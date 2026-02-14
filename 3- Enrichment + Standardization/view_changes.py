#!/usr/bin/env python3
"""
View Enrichment Changes
=======================
Compare jobs_processed.db vs jobs_enriched.db to see transformation details
"""

import sqlite3
import pandas as pd
from pathlib import Path
from collections import Counter

# Paths
BEFORE_DB = Path(__file__).parent.parent / "2- Preprocessed" / "jobs_processed.db"
AFTER_DB = Path(__file__).parent / "jobs_enriched.db"

def load_data():
    """Load both databases"""
    conn_before = sqlite3.connect(BEFORE_DB)
    df_before = pd.read_sql_query("SELECT * FROM jobs;", conn_before)
    conn_before.close()
    
    conn_after = sqlite3.connect(AFTER_DB)
    df_after = pd.read_sql_query("SELECT * FROM jobs;", conn_after)
    conn_after.close()
    
    return df_before, df_after


def show_header():
    """Display header"""
    print("\n" + "="*100)
    print("ENRICHMENT CHANGES ANALYSIS")
    print("="*100)
    print(f"BEFORE: {BEFORE_DB}")
    print(f"AFTER:  {AFTER_DB}")
    print("="*100 + "\n")


def show_task_1_changes(df_before, df_after):
    """Task 1: job_type_filled - Show what was filled"""
    print("\n" + "="*100)
    print("TASK 1: job_type_filled - Filled Missing Job Types")
    print("="*100)
    
    # Find rows where job_type was empty but now filled
    empty_mask = df_before['job_type'].apply(lambda x: pd.isna(x) or str(x).strip() == '')
    filled_count = empty_mask.sum()
    
    print(f"\nRows with empty job_type: {filled_count} ({filled_count/len(df_before)*100:.1f}%)")
    print(f"\nHow they were filled:")
    
    filled_series = df_after[empty_mask]['job_type_filled']
    for job_type, count in filled_series.value_counts().items():
        print(f"  → {job_type:<20}: {count:>4} records ({count/filled_count*100:.1f}%)")
    
    print(f"\nSample filled records:")
    samples_idx = df_after[empty_mask].head(10).index
    for idx in samples_idx:
        title = str(df_after.loc[idx, 'actual_role'])[:60]
        job_type = df_after.loc[idx, 'job_type_filled']
        print(f"  • {title:<60} → {job_type}")


def show_task_2_changes(df_before, df_after):
    """Task 2: edu_level_filled - Show education level improvements"""
    print("\n" + "="*100)
    print("TASK 2: edu_level_filled - Education Level Analysis")
    print("="*100)
    
    # Count NA values
    na_before = (df_before['education_level'] == 'NA').sum() + df_before['education_level'].isna().sum()
    na_after = (df_after['edu_level_filled'] == 'NA').sum() + df_after['edu_level_filled'].isna().sum()
    
    print(f"\nNA values:")
    print(f"  BEFORE: {na_before} ({na_before/len(df_before)*100:.1f}%)")
    print(f"  AFTER:  {na_after} ({na_after/len(df_after)*100:.1f}%)")
    print(f"  Change: {na_before - na_after} records filled")
    
    print(f"\nDistribution comparison:")
    print(f"  {'Education Level':<40} {'BEFORE':<15} {'AFTER':<15}")
    print("  " + "-"*70)
    
    all_values = set(df_before['education_level'].unique()) | set(df_after['edu_level_filled'].unique())
    all_values = sorted([v for v in all_values if pd.notna(v)])[:15]
    
    for val in all_values:
        count_before = (df_before['education_level'] == val).sum()
        count_after = (df_after['edu_level_filled'] == val).sum()
        if count_before > 0 or count_after > 0:
            print(f"  {str(val)[:40]:<40} {count_before:>6}          {count_after:>6}")


def show_task_3_changes(df_before, df_after):
    """Task 3: job_level_std - Show standardization mapping"""
    print("\n" + "="*100)
    print("TASK 3: job_level_std - Job Level Standardization")
    print("="*100)
    
    print(f"\nStandardization reduced: {df_before['job_level'].nunique()} → {df_after['job_level_std'].nunique()} categories")
    
    print(f"\nDetailed Mapping:")
    print(f"  {'Original Value':<30} → {'Standardized':<20} {'Count':<10}")
    print("  " + "-"*60)
    
    mapping_df = df_after.groupby(['job_level', 'job_level_std']).size().reset_index(name='count')
    mapping_df = mapping_df.sort_values('count', ascending=False)
    
    for _, row in mapping_df.iterrows():
        orig = str(row['job_level'])[:30]
        std = str(row['job_level_std'])[:20]
        count = row['count']
        print(f"  {orig:<30} → {std:<20} {count:<10}")


def show_task_4_changes(df_before, df_after):
    """Task 4: job_function_std - Show function categorization"""
    print("\n" + "="*100)
    print("TASK 4: job_function_std - Job Function Standardization")
    print("="*100)
    
    print(f"\nStandardization reduced: {df_before['job_function'].nunique()} → {df_after['job_function_std'].nunique()} categories")
    
    print(f"\nTop 15 Original → Standardized mappings:")
    print(f"  {'Original Function':<45} → {'Standardized':<30} {'Count':<10}")
    print("  " + "-"*85)
    
    mapping_df = df_after.groupby(['job_function', 'job_function_std']).size().reset_index(name='count')
    mapping_df = mapping_df.sort_values('count', ascending=False).head(15)
    
    for _, row in mapping_df.iterrows():
        orig = str(row['job_function'])[:45]
        std = str(row['job_function_std'])[:30]
        count = row['count']
        print(f"  {orig:<45} → {std:<30} {count:<10}")


def show_task_5_changes(df_before, df_after):
    """Task 5: company_industry_std - Show industry standardization"""
    print("\n" + "="*100)
    print("TASK 5: company_industry_std - Industry Standardization")
    print("="*100)
    
    # Count missing values
    missing_before = df_before['company_industry'].apply(lambda x: pd.isna(x) or str(x).strip() == '').sum()
    other_after = (df_after['company_industry_std'] == 'Other').sum()
    
    print(f"\nMissing/Unknown handling:")
    print(f"  BEFORE missing: {missing_before} ({missing_before/len(df_before)*100:.1f}%)")
    print(f"  AFTER 'Other':  {other_after} ({other_after/len(df_after)*100:.1f}%)")
    print(f"  Improvement:    {missing_before - other_after} records classified ({(missing_before - other_after)/missing_before*100:.1f}% of missing)")
    
    print(f"\nStandardization reduced: {df_before['company_industry'].nunique()} → {df_after['company_industry_std'].nunique()} categories")
    
    print(f"\nTop 20 Original → Standardized mappings:")
    print(f"  {'Original Industry':<50} → {'Standardized':<35} {'Cnt':<8}")
    print("  " + "-"*93)
    
    mapping_df = df_after.groupby(['company_industry', 'company_industry_std']).size().reset_index(name='count')
    mapping_df = mapping_df.sort_values('count', ascending=False).head(20)
    
    for _, row in mapping_df.iterrows():
        orig = str(row['company_industry'])[:50]
        if orig == 'nan' or orig == '':
            orig = '[empty]'
        std = str(row['company_industry_std'])[:35]
        count = row['count']
        print(f"  {orig:<50} → {std:<35} {count:<8}")


def show_sample_records(df_before, df_after):
    """Show sample records before/after"""
    print("\n" + "="*100)
    print("SAMPLE RECORDS - Before vs After")
    print("="*100)
    
    # Select 5 diverse samples
    samples = [0, 100, 200, 300, 400]
    
    for idx in samples:
        if idx >= len(df_before):
            continue
            
        print(f"\n--- Record {idx+1} ---")
        print(f"Title: {df_after.iloc[idx]['actual_role']}")
        print(f"Company: {df_after.iloc[idx].get('company_name', 'N/A')}")
        print()
        
        # Show changes
        changes = [
            ('job_type', 'job_type_filled'),
            ('education_level', 'edu_level_filled'),
            ('job_level', 'job_level_std'),
            ('job_function', 'job_function_std'),
            ('company_industry', 'company_industry_std'),
        ]
        
        for before_field, after_field in changes:
            before_val = str(df_before.iloc[idx][before_field])[:50]
            after_val = str(df_after.iloc[idx][after_field])[:50]
            
            if before_val == '' or before_val == 'nan':
                before_val = '[empty]'
            
            changed = "✓" if before_val != after_val else " "
            print(f"  {changed} {before_field:<20}: {before_val:<50} → {after_val}")


def show_summary_stats(df_before, df_after):
    """Show overall summary statistics"""
    print("\n" + "="*100)
    print("OVERALL SUMMARY")
    print("="*100)
    
    print(f"\nDatabase Comparison:")
    print(f"  Records:        {len(df_before)} → {len(df_after)}")
    print(f"  Columns:        {len(df_before.columns)} → {len(df_after.columns)} (+{len(df_after.columns) - len(df_before.columns)})")
    
    print(f"\nNew Columns Added:")
    new_cols = set(df_after.columns) - set(df_before.columns)
    for col in sorted(new_cols):
        filled_pct = df_after[col].notna().sum() / len(df_after) * 100
        unique_vals = df_after[col].nunique()
        print(f"  • {col:<25} - {filled_pct:>5.1f}% filled, {unique_vals:>3} unique values")
    
    print(f"\nData Quality Improvement:")
    
    improvements = [
        ("job_type completeness", 
         df_before['job_type'].apply(lambda x: pd.notna(x) and str(x).strip() != '').sum() / len(df_before) * 100,
         df_after['job_type_filled'].notna().sum() / len(df_after) * 100),
        
        ("job_level standardization",
         df_before['job_level'].nunique(),
         df_after['job_level_std'].nunique()),
        
        ("job_function categorization",
         df_before['job_function'].nunique(),
         df_after['job_function_std'].nunique()),
        
        ("company_industry categorization",
         df_before['company_industry'].nunique(),
         df_after['company_industry_std'].nunique()),
    ]
    
    for metric, before, after in improvements:
        if 'completeness' in metric:
            print(f"  • {metric:<35}: {before:>6.1f}% → {after:>6.1f}% ({after-before:+.1f}%)")
        else:
            print(f"  • {metric:<35}: {before:>6.0f} → {after:>6.0f} categories")


def main():
    """Main execution"""
    show_header()
    
    print("Loading data...")
    df_before, df_after = load_data()
    print(f"✓ Loaded {len(df_before)} records from both databases\n")
    
    # Show all analyses
    show_task_1_changes(df_before, df_after)
    show_task_2_changes(df_before, df_after)
    show_task_3_changes(df_before, df_after)
    show_task_4_changes(df_before, df_after)
    show_task_5_changes(df_before, df_after)
    show_sample_records(df_before, df_after)
    show_summary_stats(df_before, df_after)
    
    print("\n" + "="*100)
    print("✓ Analysis Complete")
    print("="*100)
    print(f"\nFor full report, see: {Path(__file__).parent / 'Report3.txt'}")
    print()


if __name__ == "__main__":
    main()
