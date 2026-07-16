def _filled_trade(request_id, pair, direction, units):
    return {
        "request_id": request_id,
        "currency_pair": pair,
        "direction": direction,
        "position_size": units,
        "status": "FILLED",
        "created_at": "2026-07-15T12:00:00+00:00",
    }


def _net(*, direction="Long", units=100_000, existing_trades=()):
    from execution.position_netting import net_position
    from execution.trade_state_manager import TradeStateManager

    state_manager = TradeStateManager()
    for trade in existing_trades:
        state_manager.record_trade(dict(trade))

    remaining_size, closed_count = net_position(
        state_manager,
        {
            "currency_pair": "EUR/USD",
            "direction": direction,
            "approved_position_size": units,
        },
    )
    return remaining_size, closed_count, state_manager


def test_no_opposing_position_preserves_exact_integer_units():
    remaining_size, closed_count, _ = _net()

    assert remaining_size == 100_000
    assert type(remaining_size) is int
    assert closed_count == 0


def test_same_direction_position_is_ignored_without_changing_integer_type():
    remaining_size, closed_count, state_manager = _net(
        existing_trades=(
            _filled_trade("EXISTING-1", "EUR/USD", "Long", 25_000),
        ),
    )

    assert remaining_size == 100_000
    assert type(remaining_size) is int
    assert closed_count == 0
    assert state_manager.get_all_trades()[0]["status"] == "FILLED"


def test_different_pair_position_is_ignored_without_changing_integer_type():
    remaining_size, closed_count, state_manager = _net(
        existing_trades=(
            _filled_trade("EXISTING-1", "GBP/USD", "Short", 25_000),
        ),
    )

    assert remaining_size == 100_000
    assert type(remaining_size) is int
    assert closed_count == 0
    assert state_manager.get_all_trades()[0]["status"] == "FILLED"


def test_partial_opposing_position_preserves_integer_remainder_and_closes_trade():
    remaining_size, closed_count, state_manager = _net(
        existing_trades=(
            _filled_trade("EXISTING-1", "EUR/USD", "Short", 25_000),
        ),
    )

    assert remaining_size == 75_000
    assert type(remaining_size) is int
    assert closed_count == 1
    assert state_manager.get_all_trades()[0]["status"] == "CLOSED"


def test_exact_full_offset_returns_integer_zero_and_closes_trade():
    remaining_size, closed_count, state_manager = _net(
        units=50_000,
        existing_trades=(
            _filled_trade("EXISTING-1", "EUR/USD", "Short", 50_000),
        ),
    )

    assert remaining_size == 0
    assert type(remaining_size) is int
    assert closed_count == 1
    assert state_manager.get_all_trades()[0]["status"] == "CLOSED"


def test_short_partial_offset_preserves_positive_integer_remainder():
    remaining_size, closed_count, state_manager = _net(
        direction="Short",
        existing_trades=(
            _filled_trade("EXISTING-1", "EUR/USD", "Long", 25_000),
        ),
    )

    assert remaining_size == 75_000
    assert type(remaining_size) is int
    assert closed_count == 1
    assert state_manager.get_all_trades()[0]["status"] == "CLOSED"
