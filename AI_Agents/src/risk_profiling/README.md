# Risk Profiling Agent Guide

This module estimates customer risk level from profile and behavior inputs.

## What this folder does
- Calculates risk scores.
- Applies prompt-based interpretation.
- Returns a structured risk profile.

## Data Flow
```mermaid
flowchart LR
    A[Risk inputs] --> B[scoring.py]
    B --> C[chain.py and prompts.py]
    C --> D[models.py output]
    D --> E[Risk profile result]
```
