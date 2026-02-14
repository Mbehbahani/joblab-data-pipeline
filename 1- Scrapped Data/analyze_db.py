#!/usr/bin/env python3
"""
Analyze jobs.db to extract field information and frequencies
Generates a comprehensive report about the scraped data
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from collections import Counter
import json

def analyze_jobs_database(db_path: str):
    """
    Analyze the jobs database and return comprehensive field information
    
    Args:
        db_path: Path to the jobs.db file
        
    Returns:
        dict: Analysis results including schema, frequencies, and statistics
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    results = {
        "database_path": db_path,
        "schema": {},
        "statistics": {},
        "field_frequencies": {},
        "sample_data": {}
    }
    
    # Get table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    for table_name in tables:
        table_name = table_name[0]
        print(f"\n{'='*80}")
        print(f"Analyzing Table: {table_name}")
        print(f"{'='*80}")
        
        results["schema"][table_name] = {}
        results["statistics"][table_name] = {}
        results["field_frequencies"][table_name] = {}
        
        # Get table schema
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns_info = cursor.fetchall()
        
        print(f"\nTable Schema:")
        print(f"{'Column Name':<30} {'Type':<15} {'Not Null':<10} {'Default':<20}")
        print("-" * 80)
        
        for col in columns_info:
            col_id, col_name, col_type, not_null, default_val, pk = col
            results["schema"][table_name][col_name] = {
                "type": col_type,
                "not_null": bool(not_null),
                "default": default_val,
                "primary_key": bool(pk)
            }
            print(f"{col_name:<30} {col_type:<15} {bool(not_null)!s:<10} {str(default_val):<20}")
        
        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
        row_count = cursor.fetchone()[0]
        results["statistics"][table_name]["total_rows"] = row_count
        print(f"\nTotal rows in {table_name}: {row_count}")
        
        if row_count == 0:
            print("No data in this table.")
            continue
        
        # For each column, get fill rate and unique values
        print(f"\nField Analysis:")
        print(f"{'Field Name':<30} {'Fill Rate':<12} {'Unique':<10} {'Sample Values'}")
        print("-" * 80)
        
        for col_info in columns_info:
            col_name = col_info[1]
            
            # Count non-null values
            cursor.execute(f"SELECT COUNT({col_name}) FROM {table_name} WHERE {col_name} IS NOT NULL AND {col_name} != '';")
            filled_count = cursor.fetchone()[0]
            fill_rate = (filled_count / row_count * 100) if row_count > 0 else 0
            
            # Count unique values
            cursor.execute(f"SELECT COUNT(DISTINCT {col_name}) FROM {table_name} WHERE {col_name} IS NOT NULL AND {col_name} != '';")
            unique_count = cursor.fetchone()[0]
            
            # Get sample values (up to 3)
            cursor.execute(f"SELECT DISTINCT {col_name} FROM {table_name} WHERE {col_name} IS NOT NULL AND {col_name} != '' LIMIT 3;")
            samples = [str(row[0])[:50] for row in cursor.fetchall()]
            sample_str = ", ".join(samples) if samples else "N/A"
            
            results["field_frequencies"][table_name][col_name] = {
                "filled_count": filled_count,
                "fill_rate": round(fill_rate, 2),
                "unique_count": unique_count,
                "samples": samples
            }
            
            print(f"{col_name:<30} {fill_rate:>6.1f}%     {unique_count:<10} {sample_str}")
        
        # Analyze categorical fields with value distributions
        categorical_fields = []
        for col_info in columns_info:
            col_name = col_info[1]
            cursor.execute(f"SELECT COUNT(DISTINCT {col_name}) FROM {table_name} WHERE {col_name} IS NOT NULL AND {col_name} != '';")
            unique_count = cursor.fetchone()[0]
            
            # If field has fewer than 50 unique values and more than 1, it's likely categorical
            if 1 < unique_count < 50:
                categorical_fields.append(col_name)
        
        if categorical_fields:
            print(f"\nCategorical Field Distributions:")
            for field in categorical_fields:
                print(f"\n  {field}:")
                cursor.execute(f"""
                    SELECT {field}, COUNT(*) as count 
                    FROM {table_name} 
                    WHERE {field} IS NOT NULL AND {field} != ''
                    GROUP BY {field}
                    ORDER BY count DESC
                    LIMIT 10;
                """)
                distribution = cursor.fetchall()
                results["field_frequencies"][table_name][f"{field}_distribution"] = {
                    str(val): count for val, count in distribution
                }
                for value, count in distribution:
                    percentage = (count / row_count * 100)
                    print(f"    {str(value):<40} {count:>6} ({percentage:>5.1f}%)")
        
        # Get sample records
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 3;")
        sample_rows = cursor.fetchall()
        col_names = [desc[0] for desc in cursor.description]
        
        results["sample_data"][table_name] = []
        for row in sample_rows:
            results["sample_data"][table_name].append(dict(zip(col_names, row)))
    
    conn.close()
    return results


def generate_report(results: dict, output_file: str):
    """Generate a formatted report file"""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("="*100 + "\n")
        f.write("DATABASE ANALYSIS REPORT - SCRAPED JOBS DATA\n")
        f.write("="*100 + "\n\n")
        f.write(f"Database: {results['database_path']}\n")
        f.write(f"Generated: {datetime.now()}\n\n")
        
        for table_name in results["schema"].keys():
            f.write("\n" + "="*100 + "\n")
            f.write(f"TABLE: {table_name}\n")
            f.write("="*100 + "\n\n")
            
            # Total rows
            total_rows = results["statistics"][table_name]["total_rows"]
            f.write(f"Total Records: {total_rows:,}\n\n")
            
            if total_rows == 0:
                f.write("No data available in this table.\n")
                continue
            
            # Schema section
            f.write("SCHEMA INFORMATION:\n")
            f.write("-"*100 + "\n")
            f.write(f"{'Field Name':<30} {'Type':<15} {'Not Null':<10} {'Primary Key':<12}\n")
            f.write("-"*100 + "\n")
            
            for field_name, field_info in results["schema"][table_name].items():
                f.write(f"{field_name:<30} {field_info['type']:<15} {str(field_info['not_null']):<10} {str(field_info['primary_key']):<12}\n")
            
            # Field frequencies section
            f.write("\n\nFIELD FREQUENCIES & COMPLETENESS:\n")
            f.write("-"*100 + "\n")
            f.write(f"{'Field Name':<30} {'Filled Records':<15} {'Fill Rate':<12} {'Unique Values':<15}\n")
            f.write("-"*100 + "\n")
            
            for field_name, freq_info in results["field_frequencies"][table_name].items():
                if not field_name.endswith("_distribution"):
                    filled = freq_info['filled_count']
                    fill_rate = freq_info['fill_rate']
                    unique = freq_info['unique_count']
                    f.write(f"{field_name:<30} {filled:>10,}     {fill_rate:>6.1f}%       {unique:>10,}\n")
            
            # Categorical distributions
            f.write("\n\nCATEGORICAL FIELD DISTRIBUTIONS:\n")
            f.write("-"*100 + "\n")
            
            for field_name, freq_info in results["field_frequencies"][table_name].items():
                if field_name.endswith("_distribution"):
                    original_field = field_name.replace("_distribution", "")
                    f.write(f"\n{original_field}:\n")
                    for value, count in freq_info.items():
                        percentage = (count / total_rows * 100)
                        f.write(f"  {value:<50} {count:>8,} ({percentage:>5.1f}%)\n")
            
            # Sample values
            f.write("\n\nSAMPLE VALUES FOR KEY FIELDS:\n")
            f.write("-"*100 + "\n")
            
            for field_name, freq_info in results["field_frequencies"][table_name].items():
                if not field_name.endswith("_distribution") and freq_info.get("samples"):
                    f.write(f"\n{field_name}:\n")
                    for i, sample in enumerate(freq_info["samples"][:3], 1):
                        f.write(f"  {i}. {sample}\n")
        
        f.write("\n\n" + "="*100 + "\n")
        f.write("END OF REPORT\n")
        f.write("="*100 + "\n")


if __name__ == "__main__":
    # Path to database
    db_path = Path(__file__).parent / "jobs.db"
    output_report = Path(__file__).parent / "Report1.txt"
    
    print(f"Analyzing database: {db_path}")
    print(f"Output report: {output_report}")
    
    if not db_path.exists():
        print(f"ERROR: Database file not found at {db_path}")
        exit(1)
    
    # Run analysis
    results = analyze_jobs_database(str(db_path))
    
    # Generate report
    generate_report(results, str(output_report))
    
    print(f"\n✅ Analysis complete! Report saved to: {output_report}")
