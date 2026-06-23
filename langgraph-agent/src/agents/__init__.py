"""Specialized agents for the triage workflow"""

from .spam_evaluator import SpamEvaluator
from .ticket_router import TicketRouter

__all__ = [
    "SpamEvaluator",
    "TicketRouter",
]
