"""
Zemax Design Rules Validation & Manual Knowledge Tools
Provides rule-checking against Zemax User Manual fabrication criteria and operand lookup.
"""

from typing import Any, Dict, List, Optional
from domain.operand_kb import get_operand_info, search_operands
from domain.zemax_rules import OpticalRuleCheck
from tools.system_tools import zemax_get_system_data


def zemax_validate_design_rules() -> Dict[str, Any]:
    """
    Perform a complete optical manufacturing and design integrity audit on the current system
    based on the criteria set forth in the Zemax OpticStudio User Manual.
    
    Checks include:
    - Knife-edge / negative edge thickness (lens geometry self-intersection)
    - Minimum glass center thickness (polishing fracture risk)
    - Minimum glass edge thickness (mounting & beveling feasibility)
    - Minimum center & edge air clearance (element collision risk)
    - Element aspect ratio (rigidity check)
    - High-field / large NA Ray Aiming requirement
    """
    system_summary = zemax_get_system_data()
    checker = OpticalRuleCheck()
    validation_report = checker.validate_system(system_summary)
    
    return {
        "status": "success",
        "audit_result": validation_report["status"],
        "summary": {
            "critical_errors": validation_report["critical_count"],
            "warnings": validation_report["warning_count"],
            "total_findings": validation_report["total_findings"],
        },
        "findings": validation_report["findings"],
    }


def zemax_lookup_manual(query: str) -> Dict[str, Any]:
    """
    Search the embedded Zemax OpticStudio Manual Knowledge Base for optimization operands,
    design rules, coordinate conventions, and aberration control principles.
    query: Keyword, operand code (e.g. 'EFFL', 'MNCA', 'SPHA', 'MTFT', 'Air', 'Glass').
    """
    results = search_operands(query)
    
    # Check if exact match
    exact = get_operand_info(query)
    
    return {
        "status": "success",
        "query": query,
        "exact_match": exact,
        "matching_operands_count": len(results),
        "results": results[:10],
    }
