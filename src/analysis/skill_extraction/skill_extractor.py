"""
Advanced Skill Extraction Module
================================
Uses comprehensive skills_reference.json with regex patterns for accurate skill extraction.

This module is designed to be used at the Enrichment + Standardization stage (Stage 3)
where we have clean, preprocessed job descriptions.

Key Features:
- Pattern-based matching using regex for each skill
- Category-aware extraction (e.g., "Python" category vs general "Programming Languages")
- Handles case variations and common abbreviations
- Returns both skill names and their categories

Usage:
    from src.analysis.skill_extraction.skill_extractor import SkillExtractor
    
    extractor = SkillExtractor()
    skills = extractor.extract_skills(job_description)
    # Returns: ["Python", "SQL", "Machine Learning", ...]
    
    skills_with_categories = extractor.extract_skills_with_categories(job_description)
    # Returns: [{"name": "Python", "category": "Python"}, ...]
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Set


class SkillExtractor:
    """
    Extract skills from job descriptions using pattern-based matching.
    
    Uses the comprehensive skills_reference.json with regex patterns for 
    accurate skill identification across 127 skills in 30 categories.
    """
    
    # Default path to skills reference file
    DEFAULT_SKILLS_PATH = Path(__file__).parent.parent.parent / "config" / "skills_reference.json"
    
    def __init__(self, skills_reference_path: Optional[str] = None):
        """
        Initialize the skill extractor.
        
        Args:
            skills_reference_path: Path to skills_reference.json. 
                                   If None, uses default path.
        """
        self.skills_path = Path(skills_reference_path) if skills_reference_path else self.DEFAULT_SKILLS_PATH
        self._skills_data = None
        self._compiled_patterns: Dict[str, List[re.Pattern]] = {}
        self._skill_to_category: Dict[str, str] = {}
        self._load_skills()
    
    def _load_skills(self) -> None:
        """Load and compile skills patterns from reference file."""
        if not self.skills_path.exists():
            raise FileNotFoundError(f"Skills reference file not found: {self.skills_path}")
        
        with open(self.skills_path, 'r', encoding='utf-8') as f:
            self._skills_data = json.load(f)
        
        # Compile regex patterns for each skill
        skills = self._skills_data.get('skills', [])
        for skill in skills:
            name = skill.get('name', '')
            category = skill.get('category', '')
            patterns = skill.get('patterns', [])
            
            if not name or not patterns:
                continue
            
            self._skill_to_category[name] = category
            
            # Compile all patterns for this skill
            compiled = []
            for pattern in patterns:
                try:
                    # Handle case-insensitive matching
                    compiled.append(re.compile(pattern, re.IGNORECASE))
                except re.error as e:
                    print(f"Warning: Invalid regex pattern for {name}: {pattern} - {e}")
                    continue
            
            if compiled:
                self._compiled_patterns[name] = compiled
    
    def extract_skills(self, text: str) -> List[str]:
        """
        Extract skill names from text.
        
        Args:
            text: Job description or any text to extract skills from
            
        Returns:
            List of unique skill names found in the text
        """
        if not text or not isinstance(text, str):
            return []
        
        found_skills: Set[str] = set()
        
        for skill_name, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    found_skills.add(skill_name)
                    break  # Found this skill, no need to check more patterns
        
        # Return sorted list for consistent output
        return sorted(list(found_skills))
    
    def extract_skills_with_categories(self, text: str) -> List[Dict[str, str]]:
        """
        Extract skills with their categories from text.
        
        Args:
            text: Job description or any text to extract skills from
            
        Returns:
            List of dicts with 'name' and 'category' keys
        """
        skill_names = self.extract_skills(text)
        
        return [
            {
                'name': name,
                'category': self._skill_to_category.get(name, 'Unknown')
            }
            for name in skill_names
        ]
    
    def extract_skills_by_category(self, text: str) -> Dict[str, List[str]]:
        """
        Extract skills grouped by category.
        
        Args:
            text: Job description or any text to extract skills from
            
        Returns:
            Dictionary mapping category names to lists of skill names
        """
        skills_with_cats = self.extract_skills_with_categories(text)
        
        by_category: Dict[str, List[str]] = {}
        for item in skills_with_cats:
            category = item['category']
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(item['name'])
        
        return by_category
    
    def extract_skills_string(self, text: str) -> str:
        """
        Extract skills and return as comma-separated string.
        
        This format matches the database schema for the 'skills' column.
        
        Args:
            text: Job description or any text to extract skills from
            
        Returns:
            Comma-separated string of skill names
        """
        skills = self.extract_skills(text)
        return ', '.join(skills)
    
    def extract_categories(self, text: str) -> List[str]:
        """
        Extract skill categories from text.
        
        Returns unique categories for all skills found in the text.
        This provides a higher-level granularity using the categories
        defined in skills_reference.json.
        
        Args:
            text: Job description or any text to extract skills from
            
        Returns:
            List of unique category names found in the text (sorted)
        """
        if not text or not isinstance(text, str):
            return []
        
        found_categories: Set[str] = set()
        
        for skill_name, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    category = self._skill_to_category.get(skill_name)
                    if category:
                        found_categories.add(category)
                    break  # Found this skill, no need to check more patterns
        
        # Return sorted list for consistent output
        return sorted(list(found_categories))
    
    def extract_categories_string(self, text: str) -> str:
        """
        Extract skill categories and return as comma-separated string.
        
        This provides category-level granularity for the 'skills' column,
        using the categories defined in skills_reference.json.
        
        Args:
            text: Job description or any text to extract skills from
            
        Returns:
            Comma-separated string of category names
        """
        categories = self.extract_categories(text)
        return ', '.join(categories)
    
    def get_all_categories(self) -> List[str]:
        """Get list of all skill categories."""
        return self._skills_data.get('categories', [])
    
    def get_skills_count(self) -> int:
        """Get total number of skills in the reference."""
        return self._skills_data.get('total_skills', len(self._compiled_patterns))
    
    def get_skill_category(self, skill_name: str) -> Optional[str]:
        """
        Get the category for a specific skill.
        
        Args:
            skill_name: Name of the skill
            
        Returns:
            Category name or None if skill not found
        """
        return self._skill_to_category.get(skill_name)


# Singleton instance for easy import
_default_extractor: Optional[SkillExtractor] = None


def get_extractor() -> SkillExtractor:
    """Get the singleton skill extractor instance."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = SkillExtractor()
    return _default_extractor


def extract_skills(text: str, skills_reference_path: Optional[str] = None) -> List[str]:
    """
    Convenience function to extract skills from text.
    
    Args:
        text: Job description or any text to extract skills from
        skills_reference_path: Optional path to skills reference JSON.
                               If None, uses the comprehensive skills_reference.json
                               
    Returns:
        List of unique skill names found in the text
    """
    if skills_reference_path:
        extractor = SkillExtractor(skills_reference_path)
        return extractor.extract_skills(text)
    else:
        return get_extractor().extract_skills(text)


def extract_skills_string(text: str, skills_reference_path: Optional[str] = None) -> str:
    """
    Convenience function to extract skills as comma-separated string.
    
    This matches the database schema for the 'skills' column.
    
    Args:
        text: Job description or any text to extract skills from
        skills_reference_path: Optional path to skills reference JSON
        
    Returns:
        Comma-separated string of skill names
    """
    if skills_reference_path:
        extractor = SkillExtractor(skills_reference_path)
        return extractor.extract_skills_string(text)
    else:
        return get_extractor().extract_skills_string(text)


# For backward compatibility with the old extractor.py
def extract_skills_advanced(description: str, skills_reference_path: str) -> List[str]:
    """
    Legacy function for backward compatibility.
    
    This function is kept to maintain compatibility with existing code that
    uses the old extract_skills_advanced function from extractor.py.
    
    For new code, use the SkillExtractor class or the extract_skills function.
    """
    return extract_skills(description, skills_reference_path)


if __name__ == "__main__":
    # Quick test
    extractor = SkillExtractor()
    
    test_text = """
    We are looking for a Data Scientist with strong Python programming skills.
    Experience with machine learning, SQL databases, and cloud platforms like AWS is required.
    Knowledge of Gurobi or CPLEX for optimization is a plus.
    The ideal candidate has experience with TensorFlow or PyTorch for deep learning.
    """
    
    print("Skills Reference Stats:")
    print(f"  Total skills: {extractor.get_skills_count()}")
    print(f"  Categories: {len(extractor.get_all_categories())}")
    print()
    
    print("Extracted Skills:")
    skills = extractor.extract_skills(test_text)
    for skill in skills:
        category = extractor.get_skill_category(skill)
        print(f"  - {skill} ({category})")
    
    print()
    print("Skills by Category:")
    by_category = extractor.extract_skills_by_category(test_text)
    for category, skill_list in sorted(by_category.items()):
        print(f"  {category}: {', '.join(skill_list)}")
    
    print()
    print("As String (DB format):")
    print(f"  {extractor.extract_skills_string(test_text)}")
