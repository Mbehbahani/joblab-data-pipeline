"""
Database operations for storing job data
"""
import sqlite3
from pathlib import Path
from typing import List
from datetime import datetime


class JobStorageOperations:
    """Handles storage of job data to SQLite database"""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()
    
    def _init_database(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                platform TEXT,
                actual_role TEXT,
                url TEXT,
                job_description TEXT,
                skills TEXT,
                company_name TEXT,
                country TEXT,
                location TEXT,
                search_term TEXT,
                posted_date TEXT,
                is_posted_date_assigned INTEGER,
                scraped_at TEXT,
                is_remote INTEGER,
                job_level TEXT,
                job_function TEXT,
                job_type TEXT,
                company_industry TEXT,
                company_url TEXT,
                company_num_employees TEXT,
                filter_tier1_keywords TEXT,
                filter_tier2_keywords TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def store_details(self, jobs: List) -> int:
        """Store job details to database"""
        if not jobs:
            return 0
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stored_count = 0
        for job in jobs:
            try:
                # Convert posted_date to string if it's a datetime
                posted_date_str = None
                if job.posted_date:
                    if isinstance(job.posted_date, datetime):
                        posted_date_str = job.posted_date.isoformat()
                    else:
                        posted_date_str = str(job.posted_date)
                
                cursor.execute("""
                    INSERT OR REPLACE INTO jobs (
                        job_id, platform, actual_role, url, job_description,
                        skills, company_name, country, location, search_term,
                        posted_date, is_posted_date_assigned, scraped_at, is_remote, job_level, job_function,
                        job_type, company_industry, company_url, company_num_employees,
                        filter_tier1_keywords, filter_tier2_keywords
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job.job_id,
                    job.platform,
                    job.actual_role,
                    job.url,
                    job.job_description,
                    job.skills,
                    job.company_name,
                    job.country,
                    job.location,
                    job.search_term,
                    posted_date_str,
                    1 if job.is_posted_date_assigned else 0,
                    datetime.now().isoformat(),
                    1 if job.is_remote else 0 if job.is_remote is not None else None,
                    job.job_level,
                    job.job_function,
                    job.job_type,
                    job.company_industry,
                    job.company_url,
                    job.company_num_employees,
                    job.filter_tier1_keywords,
                    job.filter_tier2_keywords
                ))
                stored_count += 1
            except Exception as e:
                print(f"Error storing job {job.job_id}: {e}")
                continue
        
        conn.commit()
        conn.close()
        
        return stored_count
