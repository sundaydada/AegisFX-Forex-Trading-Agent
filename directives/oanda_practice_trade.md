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
   - proposal identity, pair, and direction;
   - risk fraction;
   - protective stop and the raw protective-stop input;
   - drawdown state;
   - entry price;
   - integer units;
   - monetary risk amount;
   - quote timestamp.

   The advisory proposal size and the final calculated integer units are different concepts; only the calculated integer units are submitted.
5. Understand what must stay fixed and what may move before confirming. Confirmation obtains fresh evidence, so the two groups behave differently.

   These operator-controlled decision fields must remain exact:
   - proposal identity;
   - pair;
   - direction;
   - risk fraction;
   - protective stop and raw stop input;
   - drawdown state.

   These market-derived fields may change, because confirmation re-resolves them:
   - entry price;
   - units;
   - risk amount;
   - quote timestamp.
6. Click `Confirm Practice Order` exactly once. It is a separate, explicit human action and is never combined with `Review Trade`. Stop immediately after clicking and wait for one clear success or failure result. Do not:
   - double-click;
   - confirm another proposal;
   - retry after an ambiguous result;
   - place a parallel manual order;
   - use any obsolete Execute Trade procedure.

   Confirmation fails closed and requires `Review Trade` again when:
   - any operator-controlled decision field changed;
   - the stored review is older than 120 seconds;
   - entry-price drift exceeds 2 pips;
   - the fresh quote, approval, drawdown, risk, or execution checks fail.

   The freshly recalculated units and risk amount are the confirmation-time values. The earlier reviewed values are indicative only and are not guaranteed broker-fill values.

   A failed confirmation means no broker order was submitted. Read the displayed error, correct the cause, and perform a new review. Never bypass, weaken, or repeatedly click through this check.

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
