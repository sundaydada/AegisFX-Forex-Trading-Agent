# Trade Approval Contract

## Purpose
Define the required structure for any trade submitted to the execution layer.

Execution may only process trades that conform exactly to this structure.

## Required Fields

- approval_status (string: "Approved" or "Rejected")
- currency_pair (string)
- direction (string: "Long" or "Short")
- approved_position_size (float)
- stop_loss_price (float)
- take_profit_price (float)
- risk_rationale (string)
- timestamp (ISO 8601 string)

## Rules

- If approval_status is "Rejected", execution must not place any trade.
- Execution must validate all required fields before processing.
- Missing or malformed fields must result in automatic rejection.
- No additional fields may be interpreted as execution instructions.
