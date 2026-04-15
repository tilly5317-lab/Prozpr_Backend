# Effective Risk Profile Guide

This folder computes a practical risk profile that helps produce better portfolio recommendations.

## What this folder does
- Reads profile and investment-related inputs.
- Calculates risk components.
- Merges calculation outputs into one effective risk result.

## Key files
- `inputs.py`: input extraction and structure.
- `calculation.py`: scoring logic.
- `merge.py`: combines intermediate outcomes.
- `service.py`: public orchestration entry point.

## Data Flow
```mermaid
flowchart LR
    A[Profile inputs] --> B[inputs.py]
    B --> C[calculation.py]
    C --> D[merge.py]
    D --> E[service.py result]
    E --> F[Allocation services]
```
