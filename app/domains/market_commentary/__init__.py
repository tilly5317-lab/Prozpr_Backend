"""Market commentary domain — the ONLY domain allowed to import
``AI_Agents.market_commentary``.

Public surface::

    from app.domains.market_commentary.services.market_commentary_module_service import run

The chat brain runs this module as the first step of the
``general_market_query`` sequence (then ``general_chat`` tailors the macro
doc into a final reply).
"""
