#This is a Python __init__.py file for the wealth_core module. It acts as the package initializer and public API definition. 
#This file makes wealth_core a Python package and controls what gets exposed when someone imports from it.

#Imports from models.py (Data Models & Database). These are Pydantic models (for validation) and SQLAlchemy models (for database storage).
from .models import (
    ClientBackground,
    Goal,
    ReturnObjective,
    RiskTolerance,
    FinancialNeeds,
    StrategicAssetAllocation,
    InvestmentGuidelines,
    TimeHorizon,
    TaxProfile,
    ReviewProcess,
    ClientSnapshot,
    Base,
    ClientRecord,
    engine,
    SessionLocal,
)
from wealth_core.projection import build_client_projection  # noqa: F401
#Imports from services.py (Business Logic). 
#These are service functions that handle: Database operations (save/load), Financial calculations (cash flow, balance sheet), Data transformations (snapshot building)

from .services import (
    save_client_to_db,
    load_all_clients,
    generate_balance_sheet,
    build_snapshot_from_state,
)

#Imports from conversation.py (AI Conversation Flow). These manage the conversational AI workflow for collecting client information:
#FIELDS_SEQUENCE: Defines the order to ask questions (e.g., name → age → goals → risk tolerance)
#SYSTEM_PROMPT: Instructions for the AI assistant
#get_next_unfilled_field(): Progressive disclosure logic
#normalise_answer(): Parse and validate user inputs

from .conversation import (
    FIELDS_SEQUENCE,
    SYSTEM_PROMPT,
    get_next_unfilled_field,
    normalise_answer,
)
#  Imports from ai_client.py (Anthropic/Claude Integration). LLM for chat and completions.

from .ai_client import get_anthropic_client, llm_chat

# __all__ Declaration (Public API)
#Benefits: API clarity: Documents the public interface; IDE autocomplete: Better tooling support; Namespace control: Prevents internal implementation leaks

__all__ = [
    "ClientBackground",
    "Goal",
    "ReturnObjective",
    "RiskTolerance",
    "FinancialNeeds",
    "StrategicAssetAllocation",
    "InvestmentGuidelines",
    "TimeHorizon",
    "TaxProfile",
    "ReviewProcess",
    "ClientSnapshot",
    "Base",
    "ClientRecord",
    "engine",
    "SessionLocal",
    "save_client_to_db",
    "load_all_clients",
    "generate_yearly_cash_flow",
    "generate_balance_sheet",
    "build_snapshot_from_state",
    "FIELDS_SEQUENCE",
    "SYSTEM_PROMPT",
    "get_next_unfilled_field",
    "normalise_answer",
    "get_anthropic_client",
    "llm_chat",
]
