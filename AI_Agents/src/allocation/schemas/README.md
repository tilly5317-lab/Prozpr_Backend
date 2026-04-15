# Allocation Schemas Guide

This folder contains structured schema definitions used by the allocation pipeline.

## What this folder does
- Standardizes allocation input/output contracts.
- Keeps multi-step allocation flow type-safe.
- Reduces errors between orchestration stages.

## Data Flow
```mermaid
flowchart LR
    A[Allocation stage output] --> B[Schema validation]
    B --> C[Next allocation stage]
    C --> D[Final allocation payload]
```
