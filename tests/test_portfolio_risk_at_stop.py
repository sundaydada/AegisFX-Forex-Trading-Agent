import math
from dataclasses import FrozenInstanceError, fields
from inspect import Parameter, signature

import pytest


def _evaluate(**overrides):
    from execution.portfolio_risk_evaluator import evaluate_risk_at_stop

    inputs = {
        "nav": 100_000.0,
        "proposed_risk_amount": 500.0,
        "existing_portfolio_risk_amount": 0.0,
        "existing_same_currency_risk_amount": 0.0,
    }
    inputs.update(overrides)
    return evaluate_risk_at_stop(**inputs)


def test_empty_portfolio_returns_exact_frozen_approved_evidence():
    from execution.portfolio_risk_evaluator import (
        PortfolioRiskAtStopResult,
        evaluate_risk_at_stop,
    )

    parameters = signature(evaluate_risk_at_stop).parameters
    assert list(parameters) == [
        "nav",
        "proposed_risk_amount",
        "existing_portfolio_risk_amount",
        "existing_same_currency_risk_amount",
        "max_portfolio_risk_fraction",
        "max_same_currency_risk_fraction",
    ]
    assert all(
        parameter.kind is Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
    assert parameters["max_portfolio_risk_fraction"].default == 0.015
    assert parameters["max_same_currency_risk_fraction"].default == 0.01

    result = _evaluate()

    assert type(result) is PortfolioRiskAtStopResult
    assert [field.name for field in fields(result)] == [
        "approval_status",
        "reason",
        "nav",
        "proposed_risk_amount",
        "existing_portfolio_risk_amount",
        "existing_same_currency_risk_amount",
        "resulting_portfolio_risk_amount",
        "resulting_same_currency_risk_amount",
        "max_portfolio_risk_amount",
        "max_same_currency_risk_amount",
    ]
    assert result.approval_status == "Approved"
    assert result.nav == pytest.approx(100_000.0)
    assert result.proposed_risk_amount == pytest.approx(500.0)
    assert result.resulting_portfolio_risk_amount == pytest.approx(500.0)
    assert result.resulting_same_currency_risk_amount == pytest.approx(500.0)
    assert result.max_portfolio_risk_amount == pytest.approx(1_500.0)
    assert result.max_same_currency_risk_amount == pytest.approx(1_000.0)

    with pytest.raises(FrozenInstanceError):
        result.approval_status = "Rejected"


def test_approves_accumulated_risk_and_exact_limit_boundaries():
    cases = [
        {
            "existing_portfolio_risk_amount": 900.0,
            "existing_same_currency_risk_amount": 400.0,
            "resulting_portfolio": 1_400.0,
            "resulting_same_currency": 900.0,
        },
        {
            "existing_portfolio_risk_amount": 1_000.0,
            "existing_same_currency_risk_amount": 0.0,
            "resulting_portfolio": 1_500.0,
            "resulting_same_currency": 500.0,
        },
        {
            "existing_portfolio_risk_amount": 500.0,
            "existing_same_currency_risk_amount": 500.0,
            "resulting_portfolio": 1_000.0,
            "resulting_same_currency": 1_000.0,
        },
    ]

    for case in cases:
        result = _evaluate(
            existing_portfolio_risk_amount=(
                case["existing_portfolio_risk_amount"]
            ),
            existing_same_currency_risk_amount=(
                case["existing_same_currency_risk_amount"]
            ),
        )

        assert result.approval_status == "Approved"
        assert result.resulting_portfolio_risk_amount == pytest.approx(
            case["resulting_portfolio"]
        )
        assert result.resulting_same_currency_risk_amount == pytest.approx(
            case["resulting_same_currency"]
        )


def test_rejects_limit_excess_with_clear_deterministic_reasons():
    portfolio_excess = _evaluate(
        existing_portfolio_risk_amount=1_100.0,
        existing_same_currency_risk_amount=100.0,
    )
    same_currency_excess = _evaluate(
        existing_portfolio_risk_amount=900.0,
        existing_same_currency_risk_amount=600.0,
    )
    both_exceeded = _evaluate(
        existing_portfolio_risk_amount=1_100.0,
        existing_same_currency_risk_amount=600.0,
    )
    both_exceeded_again = _evaluate(
        existing_portfolio_risk_amount=1_100.0,
        existing_same_currency_risk_amount=600.0,
    )

    assert portfolio_excess.approval_status == "Rejected"
    assert "portfolio" in portfolio_excess.reason.lower()
    assert same_currency_excess.approval_status == "Rejected"
    assert "same" in same_currency_excess.reason.lower()
    assert "currency" in same_currency_excess.reason.lower()
    assert both_exceeded.approval_status == "Rejected"
    assert both_exceeded.reason == both_exceeded_again.reason
    assert both_exceeded.resulting_portfolio_risk_amount == pytest.approx(
        1_600.0
    )
    assert both_exceeded.resulting_same_currency_risk_amount == pytest.approx(
        1_100.0
    )


def test_rejects_invalid_or_missing_monetary_evidence():
    invalid_cases = [
        ("nav", None),
        ("nav", True),
        ("nav", "100000"),
        ("nav", 0.0),
        ("nav", -1.0),
        ("nav", math.nan),
        ("nav", math.inf),
        ("nav", -math.inf),
        ("proposed_risk_amount", None),
        ("proposed_risk_amount", False),
        ("proposed_risk_amount", "500"),
        ("proposed_risk_amount", 0.0),
        ("proposed_risk_amount", -1.0),
        ("proposed_risk_amount", math.nan),
        ("proposed_risk_amount", math.inf),
        ("proposed_risk_amount", -math.inf),
        ("existing_portfolio_risk_amount", None),
        ("existing_portfolio_risk_amount", True),
        ("existing_portfolio_risk_amount", "0"),
        ("existing_portfolio_risk_amount", -1.0),
        ("existing_portfolio_risk_amount", math.nan),
        ("existing_portfolio_risk_amount", math.inf),
        ("existing_portfolio_risk_amount", -math.inf),
        ("existing_same_currency_risk_amount", None),
        ("existing_same_currency_risk_amount", False),
        ("existing_same_currency_risk_amount", "0"),
        ("existing_same_currency_risk_amount", -1.0),
        ("existing_same_currency_risk_amount", math.nan),
        ("existing_same_currency_risk_amount", math.inf),
        ("existing_same_currency_risk_amount", -math.inf),
    ]

    for field_name, invalid_value in invalid_cases:
        with pytest.raises(ValueError):
            _evaluate(**{field_name: invalid_value})


def test_rejects_invalid_policy_fractions_and_inconsistent_existing_risk():
    invalid_fraction_values = [
        None,
        True,
        "0.01",
        0.0,
        -0.01,
        1.0001,
        math.nan,
        math.inf,
        -math.inf,
    ]
    for field_name in (
        "max_portfolio_risk_fraction",
        "max_same_currency_risk_fraction",
    ):
        for invalid_value in invalid_fraction_values:
            with pytest.raises(ValueError):
                _evaluate(**{field_name: invalid_value})

    with pytest.raises(ValueError):
        _evaluate(
            existing_portfolio_risk_amount=100.0,
            existing_same_currency_risk_amount=100.01,
        )
