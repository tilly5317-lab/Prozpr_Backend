# MF Mapping Test Output Guide

This folder stores output artifacts generated from mapping test runs.

## What this folder does
- Captures expected or actual run outputs.
- Helps compare correctness between versions.
- Supports regression checks for mapping changes.

## Data Flow
```mermaid
flowchart LR
    A[Testing_data inputs] --> B[Mapping execution]
    B --> C[output artifacts]
    C --> D[Manual or scripted comparison]
```
