# Controlled OANDA Practice Trade Runbook

## Scope

This runbook permits one operator-reviewed trade in an OANDA practice/demo account only. The operator must remain present throughout the procedure. Unattended trading and live-money trading are prohibited. Stop immediately if any required evidence is missing, invalid, or uncertain.

## Preconditions

- Work from a clean, synchronized main branch.
- Configure practice-account credentials through the repository's existing approved mechanism.
- Never paste credentials into chat, source, logs, screenshots, or this runbook.
- Confirm the dashboard uses the OANDA practice base URL.
- Confirm the persistent trade-state, drawdown, approval, and start-of-day NAV database paths are writable.
- Confirm no unresolved pending trade exists.
- Require successful resolution of the account snapshot, current quote, protective stop, drawdown, portfolio risk, same-currency risk, and daily-loss evidence.

## First-trade constraints

- Use exactly one approved proposal and one operator-entered absolute protective stop.
- Submit no more than one order.
- Use positive integer units calculated by the system, never the proposal's suggested size.
- Apply the normal risk budget of 0.50% of NAV, reduced to 0.25% when drawdown is at least 4%.
- Enforce maximum portfolio risk of 1.50% and maximum same-currency risk of 1.00%.
- Enforce the daily-loss stop at 2.00%; exactly 2.00% blocks new exposure.
- Reject rather than bypass any failed gate.

## Procedure

1. Confirm practice-account mode and the intended account identity without recording secrets.
2. Launch the reviewed dashboard using the repository's approved local command.
3. Inspect the single proposal's ID, instrument, direction, calculated integer units, monetary risk amount, current entry quote, and operator-entered absolute protective stop.
4. Approve only when every field and the resulting risk are understood and acceptable.
5. Submit exactly once and remain present while the result resolves.
6. Do not retry automatically after an error, timeout, or uncertain response.

## Required success evidence

Record the following sanitized evidence without secrets:

- UTC timestamp and safely masked practice account identifier.
- Proposal ID, instrument, and direction.
- Integer units, entry price, stop price, and calculated monetary risk.
- OANDA practice transaction or order ID and final dashboard result.
- Confirmation that the proposal became `EXECUTED`.
- Confirmation that persistent trade-state, drawdown baseline, and start-of-day NAV baseline exist.

## Stop conditions

No order may proceed or be retried when:

- Account mode or account identity is uncertain.
- The protective stop is missing, invalid, or on the wrong side of the entry.
- Quote, account snapshot, persistence, risk, or daily-loss evidence fails.
- Units are non-positive or non-integer.
- An unresolved pending trade exists.
- The broker response is missing, ambiguous, rejected, or timed out.
- The operator cannot explain the proposed risk.

## After the order

- Verify the order directly in the OANDA practice interface.
- Compare the broker details with the dashboard result and persistent trade state.
- Stop after this single validation order.
- Capture sanitized evidence and document any discrepancy.
- Do not place a second order during this validation session.
