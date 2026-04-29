# Migration Versions Guide

This folder is the history log of database structure changes.

## What this folder does
- Stores one migration file per schema change.
- Applies safe upgrade steps between backend versions.
- Keeps production and local DB structures aligned.

## Data Flow
```mermaid
flowchart LR
    A[Model or schema change] --> B[Create migration]
    B --> C[alembic/versions file]
    C --> D[Run migration]
    D --> E[Updated database schema]
```
