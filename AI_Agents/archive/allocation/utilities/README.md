# Allocation Utilities Guide

This folder contains helper utilities used by allocation workflows.

## What this folder does
- Loads supporting reference files.
- Provides shared helper logic for allocation stages.
- Keeps orchestration code cleaner.

## Data Flow
```mermaid
flowchart LR
    A[Allocation flow] --> B[Utility helper]
    B --> C[Reference data]
    C --> D[Prepared input for allocation]
```
