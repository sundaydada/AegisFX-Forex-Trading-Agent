# Controlled OANDA Practice Trade Runbook

## Scope

This runbook permits one operator-reviewed trade in an OANDA practice/demo account only. The operator must remain present throughout the procedure. Unattended trading and live-money trading are prohibited. Stop immediately if any required evidence is missing, invalid, or uncertain.

## Preconditions

- Work from a clean, synchronized main branch.
- Configure practice-account credentials through the repository's existing approved mechanism.
- Never paste credentials into chat, source, logs, screenshots, or this runbook.
- Confirm the persistent trade-state, drawdown, approval, and start-of-day NAV database paths are writable.
- Confirm no unresolved pending trade exists.

## Pre-submit baseline

Confirm all of the following before any review or submission, without recording secrets, account IDs, tokens, or balances:

- The configured broker URL is the OANDA practice URL.
- The OANDA portal visibly shows a DEMO/practice account.
- OANDA shows zero open trades and zero open orders.
- The AegisFX dashboard shows zero current positions and zero exposure.
- Exactly one approved proposal is selected for the supervised attempt.
- No other proposal is approved, reviewed, confirmed, or submitted during the attempt.

## First-trade constraints

- Use exactly one approved proposal and one operator-entered absolute protective stop.
- Submit no more than one order.
- Use positive integer units calculated by the system, never the proposal's suggested size.
- Apply the normal risk budget of 0.50% of NAV, reduced to 0.25% when drawdown is at least 4%.
- Enforce maximum portfolio risk of 1.50% and maximum same-currency risk of 1.00%.
- Enforce the daily-loss stop at 2.00%; exactly 2.00% blocks new exposure.
- Reject rather than bypass any failed gate.

## Procedure

1. Confirm the pre-submit baseline above.
2. From the repository root, launch the reviewed dashboard with:

       python -m streamlit run dashboard/app.py

3. Enter an explicit absolute protective-stop price for the selected approved proposal. The stop:
   - must be finite and greater than zero;
   - must not be blank;
   - is never invented, inferred, defaulted, or substituted by the system;
   - should be directionally valid: below the entry for a LONG proposal and above the entry for a SHORT proposal.
4. Click `Review Trade`. This action must not submit an order. Inspect every displayed evidence value:
   - proposal ID, pair, and direction;
   - entry price;
   - integer units;
   - risk fraction and monetary risk amount;
   - protective stop;
   - drawdown fraction;
   - quote timestamp;
   - raw protective-stop input.

   The advisory proposal size and the final calculated integer units are different concepts; only the calculated integer units are submitted.
5. Review again whenever:
   - the protective-stop input changes;
   - the quote, units, risk, drawdown, timestamp, or any other displayed evidence changes;
   - the dashboard reports that evidence changed or says to review again;
   - preview or quote resolution fails.

   Never bypass, weaken, or repeatedly race this check.
6. Click `Confirm Practice Order` exactly once, and only after the displayed evidence has been reviewed and remains unchanged. Stop immediately after clicking and wait for one clear success or failure result. Do not:
   - double-click;
   - confirm another proposal;
   - retry after an ambiguous result;
   - place a parallel manual order;
   - use any obsolete Execute Trade procedure.

## Verification evidence

On success, record without secrets:

- proposal ID;
- dashboard execution result;
- broker order or trade ID;
- pair, direction, and units;
- protective stop;
- number of open OANDA positions;
- UTC timestamp.

Verify in the OANDA DEMO portal that exactly one expected practice position exists. On any mismatch, duplicate, unclear result, or missing broker ID, stop without another submission.

## Closure

After verification, close the single practice position through the OANDA DEMO portal. Then confirm:

- OANDA shows zero open trades;
- OANDA shows zero open orders;
- the dashboard has been refreshed;
- any remaining local position is treated as a reconciliation issue and is not closed by submitting another broker action;
- final evidence of zero broker exposure is recorded.

## Stop conditions

No order may proceed or be retried when:

- Account mode or account identity is uncertain.
- The protective stop is missing, invalid, or on the wrong side of the entry.
- Quote, account snapshot, persistence, risk, or daily-loss evidence fails.
- Units are non-positive or non-integer.
- An unresolved pending trade exists.
- The broker response is missing, ambiguous, rejected, or timed out.
- The operator cannot explain the proposed risk.

## Failure rule

One attempt, one order maximum, and stop on any unexpected result.
