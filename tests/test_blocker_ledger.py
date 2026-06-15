from __future__ import annotations

import json
from pathlib import Path

from r2a.tools.workflow_decision import BlockerLedger


def test_blocker_ledger_counts_consecutive_iterations_without_double_counting_same_iteration(tmp_path: Path) -> None:
    ledger = BlockerLedger(tmp_path)
    blocker = {
        "blocker_id": "missing_source:official",
        "type": "missing_source",
        "reason_code": "OFFICIAL_SOURCE_NOT_AVAILABLE",
        "requires_user_input": True,
        "last_message": "Official source is missing.",
    }

    first = ledger.update([blocker], iteration=1)
    repeated_same_iteration = ledger.update([blocker], iteration=1)
    second_iteration = ledger.update([blocker], iteration=2)

    assert first[0]["count"] == 1
    assert repeated_same_iteration[0]["count"] == 1
    assert second_iteration[0]["count"] == 2
    data = json.loads((tmp_path / ".r2a" / "BLOCKER_LEDGER.json").read_text(encoding="utf-8"))
    assert data["blockers"]["missing_source:OFFICIAL_SOURCE_NOT_AVAILABLE"]["count"] == 2
