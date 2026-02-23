# Execution Monitoring Agent Directive

## Agent Name
Execution Monitoring Agent

## Purpose
Execute approved trades in a deterministic and controlled manner.
Must only act on validated trade approval objects.

## Inputs
- Trade approval object (validated)
- Market price feed (simulated or real)
- Execution configuration parameters

## Decision Logic
- Verify trade approval object has passed validation.
- Simulate or execute trade placement.
- Record execution details (fill price, timestamp).
- Monitor order status.

No strategy logic allowed.
No risk recalculation allowed.

## Constraints
- Must reject any trade not validated.
- Must not modify approved position size.
- Must not alter stop-loss or take-profit levels.
- Must log all executions.
- Must not interpret additional fields beyond contract.

## Outputs
- Execution confirmation (filled / rejected)
- Execution details (fill price, time)
- Execution log entry

## Failure Handling
If validation fails or execution error occurs:
- Reject trade.
- Log error explicitly.
- Do not attempt automatic correction.
