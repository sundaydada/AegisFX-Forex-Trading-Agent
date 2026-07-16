"""AST-level integration contract for dashboard/app.py.

dashboard/app.py is intentionally not import-safe (it renders the page,
constructs a broker, and opens databases at module scope), so these
tests parse its source with ast and never import or execute it.
"""

import ast
from pathlib import Path


_APP_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "app.py"

_CONTROLLER_MODULE = "dashboard.reviewed_execution_controller"
_CONTROLLER_FUNCTION = "execute_reviewed_proposal_from_dashboard"

_CONTROLLER_KEYWORDS = {
    "proposal",
    "raw_stop_loss_price",
    "api_key",
    "account_id",
    "base_url",
    "trade_state_db_path",
    "drawdown_db_path",
    "approval_db_path",
    "max_currency_exposure",
    "max_quote_age_seconds",
    "now_utc",
}


def _app_tree():
    return ast.parse(_APP_PATH.read_text(encoding="utf-8"))


def _calls_in(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _function_def(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _single_name_assignment_value(scope, name):
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return node.value
    return None


def _resolved_value_node(tree, callback, node):
    if isinstance(node, ast.Name):
        resolved = _single_name_assignment_value(callback, node.id)
        if resolved is None:
            resolved = _single_name_assignment_value(tree, node.id)
        if resolved is not None:
            return resolved
    return node


def _resolved_number(tree, callback, node):
    resolved = _resolved_value_node(tree, callback, node)
    if isinstance(resolved, ast.Constant):
        return resolved.value
    return None


def _resolved_string_constants(tree, callback, node):
    resolved = _resolved_value_node(tree, callback, node)
    return {
        inner.value
        for inner in ast.walk(resolved)
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
    }


def _approved_render_loop(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        for call in _calls_in(node):
            if _call_name(call) == "render_approved_proposal_row":
                return node, call
    return None, None


def test_app_imports_reviewed_execution_controller():
    tree = _app_tree()

    controller_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == _CONTROLLER_MODULE
    ]
    assert controller_imports, (
        "dashboard/app.py must import from"
        f" {_CONTROLLER_MODULE}"
    )

    imported_names = {
        alias.name
        for node in controller_imports
        for alias in node.names
    }
    assert _CONTROLLER_FUNCTION in imported_names


def test_approved_proposals_collect_unique_raw_absolute_stop():
    tree = _app_tree()
    loop, render_call = _approved_render_loop(tree)
    assert loop is not None, "approved-proposal render loop not found"

    stop_assignments = []
    for node in ast.walk(loop):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "text_input"
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "st"
        ):
            stop_assignments.append((node, value))

    assert stop_assignments, (
        "the approved-proposal loop must collect a raw stop via"
        " st.text_input"
    )

    matching = None
    for assignment, call in stop_assignments:
        label = ""
        if call.args and isinstance(call.args[0], ast.Constant):
            label = call.args[0].value
        if not isinstance(label, str) or "stop" not in label.lower():
            continue

        key_keywords = [kw for kw in call.keywords if kw.arg == "key"]
        if not key_keywords:
            continue
        key_strings = {
            inner.value
            for inner in ast.walk(key_keywords[0].value)
            if isinstance(inner, ast.Constant)
            and isinstance(inner.value, str)
        }
        if not any("proposal_id" in text for text in key_strings):
            continue

        if (
            len(assignment.targets) == 1
            and isinstance(assignment.targets[0], ast.Name)
        ):
            matching = (assignment.targets[0].id, call)
            break

    assert matching is not None, (
        "the stop text_input must mention the stop in its label, use a"
        " proposal_id-scoped key, and assign directly to a local"
        " variable with no numeric conversion"
    )
    stop_variable, stop_call = matching

    for keyword in stop_call.keywords:
        if keyword.arg == "value":
            assert isinstance(keyword.value, ast.Constant)
            assert keyword.value.value in ("", None), (
                "no default stop price may be supplied"
            )

    on_execute = [
        kw for kw in render_call.keywords if kw.arg == "on_execute"
    ]
    assert on_execute, "render_approved_proposal_row needs on_execute"
    handler = on_execute[0].value

    assert isinstance(handler, ast.Lambda), (
        "on_execute must bind the collected stop value per proposal"
    )
    body = handler.body
    assert isinstance(body, ast.Call)
    assert _call_name(body) == "_execute_approved_proposal"
    assert len(body.args) == 2

    lambda_params = [param.arg for param in handler.args.args]
    defaults = handler.args.defaults
    defaulted = dict(
        zip(lambda_params[len(lambda_params) - len(defaults):], defaults)
    )

    first_argument, second_argument = body.args
    assert isinstance(first_argument, ast.Name)
    assert first_argument.id in lambda_params

    assert isinstance(second_argument, ast.Name)
    bound_default = defaulted.get(second_argument.id)
    assert isinstance(bound_default, ast.Name), (
        "the raw stop must be bound into the callback via a lambda"
        " default"
    )
    assert bound_default.id == stop_variable


def test_execute_callback_delegates_once_with_exact_mvp_arguments():
    tree = _app_tree()
    callback = _function_def(tree, "_execute_approved_proposal")
    assert callback is not None

    parameters = [param.arg for param in callback.args.args]
    assert len(parameters) == 2, (
        "_execute_approved_proposal must accept the proposal and the"
        " raw operator stop"
    )

    controller_calls = [
        call
        for call in _calls_in(callback)
        if _call_name(call) == _CONTROLLER_FUNCTION
    ]
    assert len(controller_calls) == 1
    call = controller_calls[0]

    keywords = {kw.arg: kw.value for kw in call.keywords}
    expected_keywords = set(_CONTROLLER_KEYWORDS)
    if _single_name_assignment_value(
        tree,
        "START_OF_DAY_NAV_DB_PATH",
    ) is not None:
        expected_keywords.add("start_of_day_nav_db_path")
    assert set(keywords) == expected_keywords

    assert isinstance(keywords["proposal"], ast.Name)
    assert keywords["proposal"].id == parameters[0]

    assert isinstance(keywords["raw_stop_loss_price"], ast.Name)
    assert keywords["raw_stop_loss_price"].id == parameters[1]

    assert _resolved_number(
        tree,
        callback,
        keywords["max_quote_age_seconds"],
    ) == 60.0
    assert _resolved_number(
        tree,
        callback,
        keywords["max_currency_exposure"],
    ) == 100.0

    now_value = _resolved_value_node(tree, callback, keywords["now_utc"])
    assert isinstance(now_value, ast.Call)
    assert isinstance(now_value.func, ast.Attribute)
    assert now_value.func.attr == "now"
    utc_mentions = [
        inner
        for inner in ast.walk(now_value)
        if (isinstance(inner, ast.Attribute) and inner.attr == "utc")
        or (isinstance(inner, ast.Name) and inner.id == "utc")
    ]
    assert utc_mentions, "now_utc must be an aware UTC clock reading"

    assert "drawdown_high_water.db" in _resolved_string_constants(
        tree,
        callback,
        keywords["drawdown_db_path"],
    )
    assert "dry_run_sustained.db" in _resolved_string_constants(
        tree,
        callback,
        keywords["trade_state_db_path"],
    )
    assert "proposal_approvals.db" in _resolved_string_constants(
        tree,
        callback,
        keywords["approval_db_path"],
    )


def test_legacy_dashboard_execution_path_is_removed():
    tree = _app_tree()
    callback = _function_def(tree, "_execute_approved_proposal")
    assert callback is not None

    call_names = {
        _call_name(call)
        for call in _calls_in(callback)
    }

    assert "TradeOrchestrator" not in call_names
    assert "execute_approved_proposal" not in call_names
    assert "ProposalApprovalQueue" not in call_names
    assert "mark_executed" not in call_names
    assert "place_order" not in call_names

    forbidden_evidence_calls = {
        "get_quote",
        "get_account_snapshot",
        "get_drawdown_fraction",
        "size_trade_proposal",
        "build_fx_risk_inputs",
        "calculate_position_size",
    }
    assert not (call_names & forbidden_evidence_calls)

    bridge_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "ai.proposal_execution_bridge"
        for node in ast.walk(tree)
    )
    if bridge_imported:
        callback_node_ids = set(map(id, ast.walk(callback)))
        bridge_uses_outside_callback = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id == "ProposalExecutionBridge"
            and id(node) not in callback_node_ids
        ]
        assert bridge_uses_outside_callback, (
            "the ProposalExecutionBridge import has no non-execution"
            " use and must be removed"
        )


def test_app_forwards_root_start_of_day_nav_database_path():
    tree = _app_tree()
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "START_OF_DAY_NAV_DB_PATH"
    ]
    assert len(assignments) == 1

    daily_path_value = assignments[0].value
    drawdown_path_value = _single_name_assignment_value(
        tree,
        "DRAWDOWN_DB_PATH",
    )
    assert isinstance(daily_path_value, ast.Call)
    assert isinstance(drawdown_path_value, ast.Call)
    assert ast.dump(daily_path_value.func) == ast.dump(
        drawdown_path_value.func
    )
    assert len(daily_path_value.args) == 2
    assert len(drawdown_path_value.args) == 2
    assert ast.dump(daily_path_value.args[0]) == ast.dump(
        drawdown_path_value.args[0]
    )
    assert isinstance(daily_path_value.args[1], ast.Constant)
    assert daily_path_value.args[1].value == "start_of_day_nav.db"

    forbidden_path_sources = {
        "getenv",
        "text_input",
        "selectbox",
        "radio",
    }
    assert not any(
        isinstance(node, ast.Call)
        and _call_name(node) in forbidden_path_sources
        for node in ast.walk(daily_path_value)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "session_state"
        for node in ast.walk(daily_path_value)
    )

    callback = _function_def(tree, "_execute_approved_proposal")
    assert callback is not None
    controller_calls = [
        call
        for call in _calls_in(callback)
        if _call_name(call) == _CONTROLLER_FUNCTION
    ]
    assert len(controller_calls) == 1
    daily_path_keywords = [
        keyword
        for keyword in controller_calls[0].keywords
        if keyword.arg == "start_of_day_nav_db_path"
    ]
    assert len(daily_path_keywords) == 1
    forwarded_value = daily_path_keywords[0].value
    assert isinstance(forwarded_value, ast.Name)
    assert forwarded_value.id == "START_OF_DAY_NAV_DB_PATH"
