# Skill extraction module
"""
Skill Extraction Module
=======================

This module provides skill extraction from job descriptions using the 
comprehensive skills_reference.json with 127 skills across 30 categories.

Usage:
    from src.analysis.skill_extraction import extract_skills, SkillExtractor
    
    # Quick extraction (uses singleton)
    skills = extract_skills(job_description)
    
    # With custom skills reference
    extractor = SkillExtractor("/path/to/skills_reference.json")
    skills = extractor.extract_skills(job_description)
    skills_string = extractor.extract_skills_string(job_description)

Note: Skill extraction was moved from Stage 1 (Scraping) to Stage 3 
(Enrichment + Standardization) for better accuracy using comprehensive 
regex patterns from skills_reference.json.
"""

from .skill_extractor import (
    SkillExtractor,
    extract_skills,
    extract_skills_string,
    extract_skills_advanced,  # Backward compatibility
    get_extractor,
)

__all__ = [
    "SkillExtractor",
    "extract_skills",
    "extract_skills_string", 
    "extract_skills_advanced",
    "get_extractor",
]