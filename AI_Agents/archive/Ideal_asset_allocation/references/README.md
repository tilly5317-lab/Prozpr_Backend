# Ideal Allocation References Guide

This folder contains reference documents used by ideal-allocation logic.

## What this folder does
- Stores allocation guardrails and presentation references.
- Provides asset-class and subgroup context.
- Helps keep recommendations consistent and explainable.

## Data Flow
```mermaid
flowchart LR
    A[Ideal allocation module] --> B[Reference docs]
    B --> C[Prompt context build]
    C --> D[Allocation recommendation]
```
