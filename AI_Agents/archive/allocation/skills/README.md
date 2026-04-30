# Allocation Skills Guide

This folder contains instructional skill documents used by the allocation agent.

## What this folder does
- Stores guardrails and recommendation policies.
- Keeps strategy notes reusable across allocation runs.
- Improves consistency of advisory outputs.

## Data Flow
```mermaid
flowchart LR
    A[Allocation request] --> B[Skill and guardrail docs]
    B --> C[LLM reasoning steps]
    C --> D[Safer recommendation output]
```
