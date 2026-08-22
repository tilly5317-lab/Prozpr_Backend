# App Layer Guide

This folder is the heart of the backend. It receives API requests, validates data, runs business logic, connects with AI modules, and returns responses.

## What this folder does
- Runs FastAPI lifecycle and app setup.
- Connects routes, services, schemas, and models.
- Handles data flow between API and database.
- Bridges backend workflows with AI capabilities.

## Main subfolders
- `core/`: config, database, auth dependencies, observability.
- `domains/`: one folder per business domain, each with its own `routers/`, `services/`, `models/`, `schemas/`.
- `routers/`: top-level aggregator (`all_routers`), health checks, and OpenAPI tag metadata.

## Data Flow
```mermaid
flowchart LR
    A[Frontend or Client] --> B[app/routers]
    B --> C[app/domains/*/routers]
    C --> D[app/domains/*/services]
    D --> E[app/domains/*/models + database]
    D --> F[ai_engine]
    F --> G[AI_Agents]
    E --> H[Response]
    G --> H
    H --> A
```
