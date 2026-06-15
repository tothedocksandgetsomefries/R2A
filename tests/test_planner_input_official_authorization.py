from __future__ import annotations

from pathlib import Path

from r2a.core.state import make_initial_state
from r2a.tools.planner_input_builder import build_planner_input


def test_planner_input_authorizes_official_download_when_user_allows_with_budget(tmp_path: Path) -> None:
    state = _planner_state(
        tmp_path,
        allow_official_dataset_download=True,
        download_budget_gb=20,
        contract_mode="official_reduced",
    )

    bundle = build_planner_input(state)

    assert bundle["allow_official_dataset_download"] is True
    assert bundle["download_budget_gb"] == 20
    assert bundle["max_dataset_download_gb"] == 20
    assert bundle["contract_mode"] == "official_reduced"
    assert bundle["official_input_authorized"] is True
    assert bundle["user_approved_official_download"] is True
    assert bundle["authorization_reason"] == "user_allowed_official_dataset_download_with_sufficient_budget"

    authorization = bundle["official_input_authorization"]
    assert authorization["raw_user_approved_official_download"] is False
    assert authorization["allow_official_dataset_download"] is True
    assert authorization["download_budget_gb"] == 20
    assert authorization["contract_mode"] == "official_reduced"
    assert authorization["official_input_authorized"] is True


def test_planner_input_does_not_authorize_when_user_did_not_allow_download(tmp_path: Path) -> None:
    state = _planner_state(
        tmp_path,
        allow_official_dataset_download=False,
        download_budget_gb=20,
        contract_mode="official_reduced",
    )

    bundle = build_planner_input(state)

    assert bundle["allow_official_dataset_download"] is False
    assert bundle["official_input_authorized"] is False
    assert bundle["user_approved_official_download"] is False
    assert bundle["authorization_reason"] == "official_dataset_download_not_allowed"


def test_planner_input_does_not_authorize_when_budget_is_zero(tmp_path: Path) -> None:
    state = _planner_state(
        tmp_path,
        allow_official_dataset_download=True,
        download_budget_gb=0,
        contract_mode="official_reduced",
    )

    bundle = build_planner_input(state)

    assert bundle["allow_official_dataset_download"] is True
    assert bundle["download_budget_gb"] == 0
    assert bundle["official_input_authorized"] is False
    assert bundle["user_approved_official_download"] is False
    assert bundle["authorization_reason"] == "insufficient_download_budget"


def test_planner_input_keeps_raw_config_budget_contract_and_canonical_reason(tmp_path: Path) -> None:
    state = _planner_state(
        tmp_path,
        allow_official_dataset_download=True,
        download_budget_gb=20,
        contract_mode="official_reduced",
    )

    bundle = build_planner_input(state)

    assert "allow_official_dataset_download" in bundle
    assert "download_budget_gb" in bundle
    assert "max_dataset_download_gb" in bundle
    assert "contract_mode" in bundle
    assert "official_input_authorized" in bundle
    assert "authorization_reason" in bundle
    assert "official_input_authorization" in bundle
    assert bundle["official_input_authorization"]["user_allows_official_dataset_download"] is True
    assert bundle["official_input_authorization"]["target_requires_official_input"] is True
    assert bundle["official_input_authorization"]["contract_mode_requires_official_input"] is True


def test_latest_run_regression_no_allow_true_user_approved_false_conflict(tmp_path: Path) -> None:
    state = _planner_state(
        tmp_path,
        allow_official_dataset_download=True,
        download_budget_gb=20,
        contract_mode="official_reduced",
    )
    state.pop("user_approved_official_download", None)

    bundle = build_planner_input(state)

    assert bundle["allow_official_dataset_download"] is True
    assert bundle["official_input_authorized"] is True
    assert bundle["user_approved_official_download"] is True
    assert not (
        bundle["allow_official_dataset_download"] is True
        and bundle["official_input_authorized"] is True
        and bundle["user_approved_official_download"] is False
    )


def test_planner_input_network_authorization_defaults_false_and_explicit_scope(tmp_path: Path) -> None:
    state = _planner_state(
        tmp_path,
        allow_official_dataset_download=True,
        download_budget_gb=20,
        contract_mode="official_reduced",
    )

    unauthorised = build_planner_input(state)

    assert unauthorised["network_authorized"] is False
    assert unauthorised["allow_network"] is False
    assert unauthorised["allowed_network_scope"] == []
    assert unauthorised["network_authorization_reason"] == "network_not_authorized"
    assert unauthorised["network_authorization"]["network_authorized"] is False

    state.update(
        {
            "allow_network": True,
            "network_authorized": True,
            "allowed_network_scope": ["external_git_clone_for_algorithm_dependencies"],
            "network_authorization_reason": "explicit_user_allowed_network",
        }
    )
    authorised = build_planner_input(state)

    assert authorised["network_authorized"] is True
    assert authorised["allow_network"] is True
    assert authorised["allowed_network_scope"] == ["external_git_clone_for_algorithm_dependencies"]
    assert authorised["network_authorization"]["network_authorized"] is True


def _planner_state(
    repo: Path,
    *,
    allow_official_dataset_download: bool,
    download_budget_gb: int,
    contract_mode: str,
) -> dict:
    target = "L4_reduced_paper_aligned"
    state = make_initial_state(
        repo,
        goal="reproduce L4 with official reduced input",
        target_reproduction_level=target,
        allow_official_dataset_download=allow_official_dataset_download,
        download_budget_gb=download_budget_gb,
    )
    state["planner_readiness"] = {
        "ready": True,
        "reason_code": "PLANNER_READY",
        "blockers": [],
        "constraints": {
            "target_level": target,
            "contract_mode": contract_mode,
            "max_target_level": target,
        },
    }
    return state
