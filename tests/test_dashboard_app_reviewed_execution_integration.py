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

    render_keywords = {
        keyword.arg: keyword.value
        for keyword in render_call.keywords
    }
    assert "on_execute" not in render_keywords, (
        "the legacy one-click on_execute callback must not return"
    )
    assert {"on_review", "on_confirm", "preview"} <= set(render_keywords), (
        "the approved row must be wired with on_review, on_confirm,"
        " and preview"
    )

    preview_value = render_keywords["preview"]
    assert isinstance(preview_value, ast.Name), (
        "preview must be the loop-local reviewed-preview variable"
    )
    preview_assignment = _single_name_assignment_value(
        loop,
        preview_value.id,
    )
    assert preview_assignment is not None, (
        "the reviewed preview must be assigned inside the approved"
        " loop"
    )
    assert isinstance(preview_assignment, ast.Call)
    assert isinstance(preview_assignment.func, ast.Attribute)
    assert preview_assignment.func.attr == "get"
    assert any(
        isinstance(inner, ast.Attribute)
        and inner.attr == "session_state"
        for inner in ast.walk(preview_assignment.func.value)
    ), "the reviewed preview must come from st.session_state"

    assert preview_assignment.args, (
        "the session-state lookup needs a preview key"
    )
    key_node = preview_assignment.args[0]
    if isinstance(key_node, ast.Name):
        resolved_key = _single_name_assignment_value(loop, key_node.id)
        assert resolved_key is not None, (
            "the preview key must be assigned inside the approved loop"
        )
        key_node = resolved_key
    key_strings = {
        inner.value
        for inner in ast.walk(key_node)
        if isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
    }
    assert any("reviewed_preview" in text for text in key_strings), (
        "the preview key must be the proposal-specific reviewed-preview"
        " key"
    )

    for keyword_name, helper_name in (
        ("on_review", "_review_approved_proposal"),
        ("on_confirm", "_confirm_approved_proposal"),
    ):
        handler = render_keywords[keyword_name]
        assert isinstance(handler, ast.Lambda), (
            f"{keyword_name} must bind the collected stop value per"
            " proposal"
        )
        body = handler.body
        assert isinstance(body, ast.Call)
        assert _call_name(body) == helper_name
        assert len(body.args) == 2

        helper_calls = [
            call
            for call in ast.walk(handler)
            if isinstance(call, ast.Call)
            and _call_name(call) == helper_name
        ]
        assert len(helper_calls) == 1, (
            f"{keyword_name} must dispatch to {helper_name} exactly"
            " once"
        )

        lambda_params = [param.arg for param in handler.args.args]
        defaults = handler.args.defaults
        defaulted = dict(
            zip(
                lambda_params[len(lambda_params) - len(defaults):],
                defaults,
            )
        )

        first_argument, second_argument = body.args
        assert isinstance(first_argument, ast.Name)
        assert first_argument.id in lambda_params
        assert first_argument.id not in defaulted, (
            "the proposal must arrive as the live callback argument"
        )

        assert isinstance(second_argument, ast.Name)
        bound_default = defaulted.get(second_argument.id)
        assert isinstance(bound_default, ast.Name), (
            "the raw stop must be bound into the callback via a lambda"
            " default"
        )
        assert bound_default.id == stop_variable


def test_confirm_callback_delegates_once_with_exact_mvp_arguments():
    tree = _app_tree()
    callback = _function_def(tree, "_confirm_approved_proposal")
    assert callback is not None

    parameters = [param.arg for param in callback.args.args]
    assert len(parameters) == 2, (
        "_confirm_approved_proposal must accept the proposal and the"
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

    callback_call_names = {
        _call_name(inner) for inner in _calls_in(callback)
    }
    assert not (
        callback_call_names
        & {
            "TradeOrchestrator",
            "ProposalExecutionBridge",
            "process_trade",
            "place_order",
            "mark_executed",
        }
    ), "confirmation must submit only through the execution controller"


def test_legacy_dashboard_execution_path_is_removed():
    tree = _app_tree()

    assert _function_def(tree, "_execute_approved_proposal") is None, (
        "the legacy _execute_approved_proposal helper must be deleted"
    )

    loop, render_call = _approved_render_loop(tree)
    assert loop is not None, "approved-proposal render loop not found"

    render_keywords = {kw.arg for kw in render_call.keywords}
    assert "on_execute" not in render_keywords, (
        "the approved row must not pass the legacy on_execute callback"
    )
    assert {"on_review", "on_confirm", "preview"} <= render_keywords, (
        "the approved row must pass on_review, on_confirm, and preview"
    )

    loop_strings = {
        node.value
        for node in ast.walk(loop)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    }
    assert not any("Execute Trade" in text for text in loop_strings), (
        "the approved row must contain no Execute Trade path"
    )

    loop_call_names = {_call_name(call) for call in _calls_in(loop)}
    assert "_execute_approved_proposal" not in loop_call_names


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

    def _assert_nav_path_forwarded(call):
        daily_path_keywords = [
            keyword
            for keyword in call.keywords
            if keyword.arg == "start_of_day_nav_db_path"
        ]
        assert len(daily_path_keywords) == 1
        forwarded_value = daily_path_keywords[0].value
        assert isinstance(forwarded_value, ast.Name)
        assert forwarded_value.id == "START_OF_DAY_NAV_DB_PATH"

    review = _function_def(tree, "_review_approved_proposal")
    assert review is not None
    review_preview_calls = [
        call
        for call in _calls_in(review)
        if _call_name(call) == _PREVIEW_FUNCTION
    ]
    assert len(review_preview_calls) == 1
    _assert_nav_path_forwarded(review_preview_calls[0])

    confirm = _function_def(tree, "_confirm_approved_proposal")
    assert confirm is not None
    confirm_preview_calls = [
        call
        for call in _calls_in(confirm)
        if _call_name(call) == _PREVIEW_FUNCTION
    ]
    assert len(confirm_preview_calls) == 1
    _assert_nav_path_forwarded(confirm_preview_calls[0])

    confirm_execute_calls = [
        call
        for call in _calls_in(confirm)
        if _call_name(call) == _CONTROLLER_FUNCTION
    ]
    assert len(confirm_execute_calls) == 1
    _assert_nav_path_forwarded(confirm_execute_calls[0])


_PREVIEW_FUNCTION = "preview_reviewed_proposal_from_dashboard"

_ACTION_MODULE_PATH = _APP_PATH.parent / "proposal_execution_action.py"
_PREVIEW_CONTROLLER_PATH = (
    _APP_PATH.parent / "reviewed_execution_controller.py"
)

_MINIMUM_COMPARED_FIELDS = {
    "proposal_id",
    "pair",
    "direction",
    "entry_price",
    "units",
    "risk_fraction",
    "risk_amount",
    "stop_loss_price",
    "drawdown_fraction",
    "quote_timestamp",
    "raw_stop_loss_price",
}


def _committed_preview_fields():
    action_tree = ast.parse(
        _ACTION_MODULE_PATH.read_text(encoding="utf-8")
    )
    preview_def = _function_def(
        action_tree,
        "preview_reviewed_proposal_action",
    )
    assert preview_def is not None, (
        "committed preview action missing from"
        " dashboard/proposal_execution_action.py"
    )

    controller_tree = ast.parse(
        _PREVIEW_CONTROLLER_PATH.read_text(encoding="utf-8")
    )
    assert _function_def(controller_tree, _PREVIEW_FUNCTION) is not None, (
        "committed preview controller missing from"
        " dashboard/reviewed_execution_controller.py"
    )

    for node in ast.walk(preview_def):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant)
            and isinstance(key.value, str)
        }
        if {"entry_price", "raw_stop_loss_price"} <= keys:
            return keys

    raise AssertionError(
        "committed preview evidence mapping not found in"
        " preview_reviewed_proposal_action"
    )


def test_app_blocks_confirmation_when_review_evidence_changes():
    tree = _app_tree()

    def _uses_session_state(node):
        return any(
            isinstance(inner, ast.Attribute)
            and inner.attr == "session_state"
            for inner in ast.walk(node)
        )

    def _string_constants(node):
        return {
            inner.value
            for inner in ast.walk(node)
            if isinstance(inner, ast.Constant)
            and isinstance(inner.value, str)
        }

    compared_fields = _committed_preview_fields()
    assert _MINIMUM_COMPARED_FIELDS <= compared_fields, (
        "committed preview evidence no longer exposes the minimum"
        " execution-critical fields"
    )

    controller_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == _CONTROLLER_MODULE
    ]
    imported_names = {
        alias.name
        for node in controller_imports
        for alias in node.names
    }
    assert _CONTROLLER_FUNCTION in imported_names
    assert _PREVIEW_FUNCTION in imported_names, (
        "dashboard/app.py must import the preview controller used by"
        " the review and confirmation actions"
    )

    loop, render_call = _approved_render_loop(tree)
    assert loop is not None, "approved-proposal render loop not found"
    render_keywords = {kw.arg for kw in render_call.keywords}
    assert {"on_review", "on_confirm", "preview"} <= render_keywords, (
        "the approved row must be wired with on_review, on_confirm,"
        " and preview"
    )
    assert "on_execute" not in render_keywords, (
        "the approved row must not expose the legacy one-click"
        " on_execute path"
    )

    proposal_scoped_stop_inputs = []
    for call in _calls_in(loop):
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "text_input"
        ):
            continue
        for keyword in call.keywords:
            if keyword.arg != "key":
                continue
            if any(
                "proposal_id" in text
                for text in _string_constants(keyword.value)
            ):
                proposal_scoped_stop_inputs.append(call)
    assert proposal_scoped_stop_inputs, (
        "the raw protective-stop input must remain proposal-specific"
    )

    review = _function_def(tree, "_review_approved_proposal")
    assert review is not None, (
        "dashboard/app.py must define _review_approved_proposal for"
        " the Review Trade action"
    )
    review_params = [param.arg for param in review.args.args]
    assert len(review_params) >= 2, (
        "the review action must receive the proposal and the raw stop"
    )
    review_preview_calls = [
        call
        for call in _calls_in(review)
        if _call_name(call) == _PREVIEW_FUNCTION
    ]
    assert len(review_preview_calls) == 1, (
        "Review Trade must obtain preview evidence exactly once"
    )
    review_preview_kwargs = {
        keyword.arg for keyword in review_preview_calls[0].keywords
    }
    assert {"proposal", "raw_stop_loss_price"} <= review_preview_kwargs, (
        "the review action must forward the proposal and the raw stop"
        " to the preview controller"
    )
    review_execute_calls = [
        call
        for call in _calls_in(review)
        if _call_name(call) == _CONTROLLER_FUNCTION
    ]
    assert review_execute_calls == [], (
        "the review action must never call the execution controller"
    )
    review_stores = [
        node
        for node in ast.walk(review)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Subscript)
        and _uses_session_state(node.targets[0].value)
    ]
    assert review_stores, (
        "successful preview evidence must be stored in st.session_state"
    )
    review_constants = _string_constants(review)
    assert "proposal_id" in review_constants, (
        "the preview session key must be proposal-specific"
    )
    review_messages = [
        call
        for call in _calls_in(review)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in {"error", "warning"}
    ]
    assert review_messages, (
        "preview failure must surface through Streamlit error handling"
    )

    confirm = _function_def(tree, "_confirm_approved_proposal")
    assert confirm is not None, (
        "dashboard/app.py must define _confirm_approved_proposal for"
        " the Confirm Practice Order action"
    )
    confirm_params = [param.arg for param in confirm.args.args]
    assert len(confirm_params) >= 2, (
        "the confirmation action must receive the proposal and the"
        " raw stop"
    )
    confirm_preview_calls = [
        call
        for call in _calls_in(confirm)
        if _call_name(call) == _PREVIEW_FUNCTION
    ]
    assert len(confirm_preview_calls) == 1, (
        "confirmation must obtain fresh preview evidence exactly once"
    )
    confirm_execute_calls = [
        call
        for call in _calls_in(confirm)
        if _call_name(call) == _CONTROLLER_FUNCTION
    ]
    assert len(confirm_execute_calls) == 1, (
        "confirmation must delegate to the execution controller"
        " exactly once"
    )
    confirm_execute_kwargs = {
        keyword.arg for keyword in confirm_execute_calls[0].keywords
    }
    assert {"proposal", "raw_stop_loss_price"} <= confirm_execute_kwargs, (
        "confirmation must forward the proposal and the raw stop to"
        " the execution controller"
    )

    stored_preview_lookups = [
        call
        for call in _calls_in(confirm)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
        and _uses_session_state(call.func.value)
    ] + [
        node
        for node in ast.walk(confirm)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        )
        and _uses_session_state(node)
    ]
    assert stored_preview_lookups, (
        "confirmation must require an existing stored preview from"
        " st.session_state"
    )

    confirm_constants = _string_constants(confirm)
    assert compared_fields <= confirm_constants, (
        "confirmation must compare every execution-critical field"
        " exposed by the committed preview evidence"
    )

    replacement_or_clear = [
        node
        for node in ast.walk(confirm)
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and _uses_session_state(node.targets[0].value)
        )
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "pop"
            and _uses_session_state(node.func.value)
        )
        or (
            isinstance(node, ast.Delete)
            and any(
                isinstance(target, ast.Subscript)
                and _uses_session_state(target.value)
                for target in node.targets
            )
        )
    ]
    assert replacement_or_clear, (
        "changed evidence must clear or replace the stored preview"
    )

    review_again_messages = [
        call
        for call in _calls_in(confirm)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in {"error", "warning"}
        and any(
            "review" in text.lower()
            for text in _string_constants(call)
        )
    ]
    assert review_again_messages, (
        "blocked confirmation must tell the operator to review again"
    )

    guarded_returns = [
        node
        for node in ast.walk(confirm)
        if isinstance(node, ast.If)
        and any(
            isinstance(inner, ast.Return)
            for inner in ast.walk(node)
        )
    ]
    assert len(guarded_returns) >= 2, (
        "confirmation must fail closed before execution when the raw"
        " stop or compared evidence differs"
    )

    forbidden_call_names = {
        "TradeOrchestrator",
        "ProposalExecutionBridge",
        "process_trade",
        "place_order",
        "mark_executed",
        "_execute_approved_proposal",
    }
    for action in (review, confirm):
        action_call_names = {
            _call_name(call) for call in _calls_in(action)
        }
        assert not (action_call_names & forbidden_call_names), (
            "the execution controller must remain the sole submission"
            " path for the reviewed actions"
        )
