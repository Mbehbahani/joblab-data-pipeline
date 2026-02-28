"""
Tool Extraction Module
======================
Uses tools_reference.json with regex patterns for accurate extraction of
optimization solvers, modeling languages, and specialized OR tools.

This module mirrors the SkillExtractor pattern but operates on a separate
tools_reference.json to keep optimization tools distinct from general skills.

Usage:
    from src.analysis.skill_extraction.tool_extractor import ToolExtractor
    
    extractor = ToolExtractor()
    tools = extractor.extract_tools(job_description)
    # Returns: ["Gurobi", "CPLEX", "OR-Tools", ...]
    
    tools_string = extractor.extract_tools_string(job_description)
    # Returns: "CPLEX, Gurobi, OR-Tools"
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Set


class ToolExtractor:
    """
    Extract optimization tools from job descriptions using pattern-based matching.
    
    Uses tools_reference.json with regex patterns for accurate tool identification
    across optimization solvers, modeling languages, and specialized libraries.
    """
    
    DEFAULT_TOOLS_PATH = Path(__file__).parent.parent.parent / "config" / "tools_reference.json"
    
    def __init__(self, tools_reference_path: Optional[str] = None):
        """
        Initialize the tool extractor.
        
        Args:
            tools_reference_path: Path to tools_reference.json.
                                  If None, uses default path.
        """
        self.tools_path = Path(tools_reference_path) if tools_reference_path else self.DEFAULT_TOOLS_PATH
        self._tools_data = None
        self._compiled_patterns: Dict[str, List[re.Pattern]] = {}
        self._tool_to_category: Dict[str, str] = {}
        self._load_tools()
    
    def _load_tools(self) -> None:
        """Load and compile tool patterns from reference file."""
        if not self.tools_path.exists():
            raise FileNotFoundError(f"Tools reference file not found: {self.tools_path}")
        
        with open(self.tools_path, 'r', encoding='utf-8') as f:
            self._tools_data = json.load(f)
        
        tools = self._tools_data.get('tools', [])
        for tool in tools:
            name = tool.get('name', '')
            category = tool.get('category', '')
            patterns = tool.get('patterns', [])
            
            if not name or not patterns:
                continue
            
            self._tool_to_category[name] = category
            
            compiled = []
            for pattern in patterns:
                try:
                    compiled.append(re.compile(pattern, re.IGNORECASE))
                except re.error as e:
                    print(f"Warning: Invalid regex pattern for {name}: {pattern} - {e}")
                    continue
            
            if compiled:
                self._compiled_patterns[name] = compiled
    
    def extract_tools(self, text: str) -> List[str]:
        """
        Extract tool names from text.
        
        Args:
            text: Job description or any text to extract tools from
            
        Returns:
            List of unique tool names found in the text
        """
        if not text or not isinstance(text, str):
            return []
        
        found_tools: Set[str] = set()
        
        for tool_name, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    found_tools.add(tool_name)
                    break
        
        return sorted(list(found_tools))
    
    def extract_tools_string(self, text: str) -> str:
        """
        Extract tools and return as comma-separated string.
        
        This format matches the database schema for the 'tools' column.
        
        Args:
            text: Job description or any text to extract tools from
            
        Returns:
            Comma-separated string of tool names
        """
        tools = self.extract_tools(text)
        return ', '.join(tools)
    
    def extract_tools_with_categories(self, text: str) -> List[Dict[str, str]]:
        """
        Extract tools with their categories from text.
        
        Returns:
            List of dicts with 'name' and 'category' keys
        """
        tool_names = self.extract_tools(text)
        return [
            {
                'name': name,
                'category': self._tool_to_category.get(name, 'Unknown')
            }
            for name in tool_names
        ]
    
    def get_all_tool_names(self) -> List[str]:
        """Get list of all tool names in the reference."""
        return sorted(list(self._compiled_patterns.keys()))
    
    def get_tools_count(self) -> int:
        """Get total number of tools in the reference."""
        return self._tools_data.get('total_tools', len(self._compiled_patterns))


# Singleton instance
_default_extractor: Optional[ToolExtractor] = None


def get_tool_extractor() -> ToolExtractor:
    """Get the singleton tool extractor instance."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = ToolExtractor()
    return _default_extractor


def extract_tools(text: str, tools_reference_path: Optional[str] = None) -> List[str]:
    """
    Convenience function to extract tools from text.
    """
    if tools_reference_path:
        extractor = ToolExtractor(tools_reference_path)
        return extractor.extract_tools(text)
    else:
        return get_tool_extractor().extract_tools(text)


def extract_tools_string(text: str, tools_reference_path: Optional[str] = None) -> str:
    """
    Convenience function to extract tools as comma-separated string.
    """
    if tools_reference_path:
        extractor = ToolExtractor(tools_reference_path)
        return extractor.extract_tools_string(text)
    else:
        return get_tool_extractor().extract_tools_string(text)


if __name__ == "__main__":
    extractor = ToolExtractor()
    
    test_text = """
    We are looking for an Operations Research Scientist with strong experience
    in mathematical optimization. Proficiency in Gurobi or CPLEX for solving
    large-scale mixed-integer programs is required. Experience with Pyomo or
    OR-Tools for modeling is a plus. Knowledge of AMPL or GAMS is desirable.
    """
    
    print("Tools Reference Stats:")
    print(f"  Total tools: {extractor.get_tools_count()}")
    print()
    
    print("Extracted Tools:")
    tools = extractor.extract_tools(test_text)
    for tool in tools:
        print(f"  - {tool}")
    
    print()
    print("As String (DB format):")
    print(f"  {extractor.extract_tools_string(test_text)}")
