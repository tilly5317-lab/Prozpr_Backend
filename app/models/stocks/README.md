# Stocks Models Guide

This folder stores stock-related database models.

## What this folder does
- Stores stock buy/sell transactions.
- Stores price history and company metadata.
- Supports stock-level analytics in portfolio features.

## Data Flow
```mermaid
flowchart LR
    A[Stock data ingestion] --> B[Stocks models]
    B --> C[Stocks tables]
    C --> D[Performance and allocation views]
```
