# AegisFX System Architecture Directive

## Core Principle
The system is modular, multi-agent, and governance-enforced.
No agent may bypass Risk & Capital Control.
Execution logic must be deterministic and isolated from orchestration.

## Agent Hierarchy (Mandatory Order)
1. Market Assessment Agent
2. Regime Detection Agent
3. Strategy Evaluation Agent
4. Model Evaluation Agent
5. Risk & Capital Control Agent
6. Execution Monitoring Agent
7. Explainability Agent
8. Self-Adjustment Agent

## Structural Requirements
- Agents contain no executable trading logic.
- Execution layer contains deterministic functions only.
- Directives define decision flow.
- Tests are mandatory and gate execution.
- Risk Control must approve all trades before execution.
- No agent may directly call execution functions except through Risk Control approval.
