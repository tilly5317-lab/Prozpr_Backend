# Wealth Core Guide

This folder provides reusable wealth-planning intelligence shared across backend flows.

## What this folder does
- Handles allocation and rebalancing reasoning helpers.
- Supports financial conversation and projection utilities.
- Keeps common wealth logic reusable and centralized.

## Data Flow
```mermaid
flowchart LR
    A[App services] --> B[wealth_core modules]
    B --> C[Reasoning and calculations]
    C --> D[Advisory-ready output]
```
