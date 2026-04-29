# Portfolio Query Agent Guide

This module answers natural-language questions about portfolio data.

## What this folder does
- Interprets user portfolio questions.
- Applies query guardrails.
- Produces structured portfolio answers.

## Data Flow
```mermaid
flowchart LR
    A[Portfolio question] --> B[orchestrator.py]
    B --> C[guardrails and rules]
    C --> D[models.py response build]
    D --> E[Portfolio answer]
```
