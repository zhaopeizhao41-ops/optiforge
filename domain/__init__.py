"""
Domain module for Zemax rules, knowledge base, and optical templates.
"""

from domain.operand_kb import get_operand_info, search_operands, OPERAND_DATABASE
from domain.zemax_rules import OpticalRuleCheck
from domain.design_templates import get_template, list_templates

__all__ = [
    "get_operand_info",
    "search_operands",
    "OPERAND_DATABASE",
    "OpticalRuleCheck",
    "get_template",
    "list_templates",
]
