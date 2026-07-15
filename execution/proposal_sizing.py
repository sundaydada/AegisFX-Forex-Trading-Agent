from dataclasses import dataclass

from brokers.broker_interface import AccountSnapshot
from execution.fx_risk_inputs import build_fx_risk_inputs
from execution.position_sizing import calculate_position_size
from execution.risk_budget import risk_fraction_for_drawdown


@dataclass(frozen=True)
class ProposalSizingResult:
    pair: str
    side: str
    account_currency: str
    nav: float
    drawdown_fraction: float
    risk_fraction: float
    risk_amount: float
    entry_price: float
    stop_distance_pips: float
    stop_loss_price: float
    pip_size: float
    pip_value_per_unit: float
    loss_per_unit_at_stop: float
    units: int


def size_trade_proposal(
    *,
    account_snapshot: AccountSnapshot,
    pair: str,
    side: str,
    entry_price: float,
    stop_distance_pips: float,
    drawdown_fraction: float,
) -> ProposalSizingResult:
    if not isinstance(account_snapshot, AccountSnapshot):
        raise ValueError("account_snapshot must be an AccountSnapshot instance")

    risk_fraction = risk_fraction_for_drawdown(drawdown_fraction)
    validated_drawdown_fraction = float(drawdown_fraction)

    fx_inputs = build_fx_risk_inputs(
        pair=pair,
        side=side,
        account_currency=account_snapshot.currency,
        entry_price=entry_price,
        stop_distance_pips=stop_distance_pips,
    )
    position_size = calculate_position_size(
        nav=account_snapshot.nav,
        risk_fraction=risk_fraction,
        stop_distance_pips=fx_inputs.stop_distance_pips,
        pip_value_per_unit=fx_inputs.pip_value_per_unit,
    )

    return ProposalSizingResult(
        pair=fx_inputs.pair,
        side=fx_inputs.side,
        account_currency=fx_inputs.account_currency,
        nav=position_size.nav,
        drawdown_fraction=validated_drawdown_fraction,
        risk_fraction=position_size.risk_fraction,
        risk_amount=position_size.risk_amount,
        entry_price=fx_inputs.entry_price,
        stop_distance_pips=fx_inputs.stop_distance_pips,
        stop_loss_price=fx_inputs.stop_loss_price,
        pip_size=fx_inputs.pip_size,
        pip_value_per_unit=fx_inputs.pip_value_per_unit,
        loss_per_unit_at_stop=position_size.loss_per_unit_at_stop,
        units=position_size.units,
    )
