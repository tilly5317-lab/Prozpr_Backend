"""general_chat domain — the fallback conversational module.

Unlike the specialist domains (asset_allocation, rebalancing, …) general chat
has no persisted artifacts; it just generates a tailored reply. It lives as its
own domain so the brain can call it uniformly via its ``run`` module service,
and so no per-intent logic remains inside ``ai_engine``.
"""
