"""
Skill extraction from job descriptions
"""
import json
from pathlib import Path
from typing import List


def extract_skills_advanced(description: str, skills_reference_path: str) -> List[str]:
    """
    Extract skills from job description using a reference skills list
    
    Args:
        description: Job description text
        skills_reference_path: Path to skills reference JSON file
    
    Returns:
        List of extracted skills
    """
    if not description:
        return []
    
    skills_path = Path(skills_reference_path)
    
    # If skills reference file doesn't exist, return empty list
    if not skills_path.exists():
        return []
    
    try:
        with open(skills_path, 'r', encoding='utf-8') as f:
            skills_data = json.load(f)
        
        # Extract skills list from the JSON structure
        # Adapt this based on your actual skills_reference_2025.json structure
        if isinstance(skills_data, dict):
            skills_list = skills_data.get('skills', [])
        elif isinstance(skills_data, list):
            skills_list = skills_data
        else:
            return []
        
        # Simple keyword matching (case-insensitive)
        description_lower = description.lower()
        found_skills = []
        
        for skill in skills_list:
            skill_str = skill if isinstance(skill, str) else skill.get('name', '')
            if skill_str.lower() in description_lower:
                found_skills.append(skill_str)
        
        return found_skills
    
    except Exception as e:
        print(f"Error extracting skills: {e}")
        return []
