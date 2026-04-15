# Market Commentary Agent Guide

This module generates market commentary for advisory conversations.

## What this folder does
- Collects market signals.
- Builds readable market commentary.
- Supports Q&A-style market responses.

## Data Flow
```mermaid
flowchart LR
    A[Market query] --> B[scraper.py]
    B --> C[document_generator.py]
    C --> D[agent.py and main.py]
    D --> E[Market commentary output]
```
