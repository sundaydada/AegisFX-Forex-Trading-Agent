from collections.abc import Callable, Mapping, Sequence

from dashboard.theme import (
    build_hero_html,
    build_proposal_card_html,
    build_status_tile_html,
)


def render_production_hero(
    st_module,
    *,
    label: str,
    value: str,
    status_label: str,
    status_tone: str,
) -> None:
    st_module.markdown(
        build_hero_html(
            eyebrow=label,
            subtitle=value,
            status_label=status_label,
            status_tone=status_tone,
        ),
        unsafe_allow_html=True,
    )


def render_pending_proposal_row(
    st_module,
    proposal: Mapping[str, object],
    *,
    on_approve: Callable[[str], None],
    on_reject: Callable[[str], None],
) -> None:
    content_column, approve_column, reject_column = st_module.columns([3, 1, 1])
    content_column.markdown(
        build_proposal_card_html(
            pair=str(proposal["pair"]),
            status="PENDING",
            tone="warning",
            direction=str(proposal["direction"]),
            confidence=f"{proposal['confidence']}%",
        ),
        unsafe_allow_html=True,
    )
    content_column.caption(
        f"size {proposal['suggested_size']} · {proposal['strategy']}"
    )
    content_column.caption(str(proposal["reason"]))

    proposal_id = str(proposal["proposal_id"])
    if approve_column.button(
        "Approve",
        key=f"approve_{proposal_id}",
    ):
        on_approve(proposal_id)

    if reject_column.button(
        "Reject",
        key=f"reject_{proposal_id}",
    ):
        on_reject(proposal_id)


def render_approved_proposal_row(
    st_module,
    proposal: Mapping[str, object],
    *,
    on_execute: Callable[[Mapping[str, object]], None],
) -> None:
    content_column, execute_column = st_module.columns([3, 1])
    content_column.markdown(
        build_proposal_card_html(
            pair=str(proposal["pair"]),
            status="APPROVED",
            tone="success",
            direction=str(proposal["direction"]),
            confidence=f"{proposal['confidence']}%",
        ),
        unsafe_allow_html=True,
    )
    content_column.caption(
        f"size {proposal['suggested_size']} · {proposal['strategy']}"
    )
    content_column.caption(str(proposal["reason"]))

    proposal_id = str(proposal["proposal_id"])
    if execute_column.button(
        "Execute Trade",
        key=f"execute_{proposal_id}",
    ):
        on_execute(proposal)


def render_recent_decision_row(
    st_module,
    decision: Mapping[str, object],
    *,
    tone: str,
) -> None:
    st_module.markdown(
        build_proposal_card_html(
            pair=str(decision["pair"]),
            status=str(decision["status"]),
            tone=tone,
            direction=str(decision["direction"]),
            confidence=f"{decision['confidence']}%",
        ),
        unsafe_allow_html=True,
    )
    reviewed_at = decision.get("reviewed_at")
    reviewed_text = str(reviewed_at)[:19] if reviewed_at else "-"
    st_module.caption(
        f"size {decision['suggested_size']} · reviewed {reviewed_text}"
    )


def render_system_status_tiles(
    st_module,
    statuses: Sequence[Mapping[str, str]],
) -> None:
    columns = st_module.columns(len(statuses))
    for column, status in zip(columns, statuses):
        column.markdown(
            build_status_tile_html(
                label=status["label"],
                value=status["value"],
                tone=status["tone"],
            ),
            unsafe_allow_html=True,
        )
