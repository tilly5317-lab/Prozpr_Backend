# Profile Models Guide

This folder stores customer profile models used for personalization.

## What this folder does
- Persists personal finance and investment profile data.
- Stores risk, tax, and constraint-related records.
- Feeds advisory and allocation decision engines.

## Data Flow
```mermaid
flowchart LR
    A[Onboarding/Profile APIs] --> B[Profile models]
    B --> C[Profile tables]
    C --> D[Risk and recommendation services]
```
