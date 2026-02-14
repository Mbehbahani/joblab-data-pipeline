"""
Job data models for the pipeline
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class JobDetailModel:
    """Model representing a job posting with all metadata"""
    platform: str
    job_id: str
    actual_role: str
    url: str
    job_description: Optional[str] = None
    company_name: str = ""
    country: str = ""
    location: Optional[str] = None
    search_term: str = ""
    posted_date: Optional[datetime] = None
    is_posted_date_assigned: bool = False  # True if posted_date was assigned (not from source)
    skills: str = ""
    is_remote: Optional[bool] = None
    job_level: str = ""
    job_function: str = ""
    job_type: str = ""
    company_industry: str = ""
    company_url: str = ""
    company_num_employees: str = ""
    filter_tier1_keywords: str = ""
    filter_tier2_keywords: str = ""
