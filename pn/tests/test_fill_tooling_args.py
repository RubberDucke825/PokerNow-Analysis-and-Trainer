"""Plan 16 compute split (keys review 2026-10-15): the GCP boxes (tools/cloud_fill.sh start/resume)
walk the one fill order from its light end (`--reverse`), the GitHub Actions waves
(tools/gha/fill.yml) walk it forward with otherwise the same arguments, so the two pools meet in
the middle instead of solving the same head boards twice."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
PLAN16 = ["--configs", "0", "--subset", "184", "--lib", "8x100", "--lib", "6x200", "--lib", "8x200",
          "--new-only", "--accuracy", "1", "--depth-accuracy", "200=2"]  # fmt: skip


def _box_args() -> list[str]:
    text = (TOOLS / "cloud_fill.sh").read_text()
    m = re.search(r"^DEFAULT_FILL_ARGS=\((.*)\)", text, re.MULTILINE)
    assert m, "DEFAULT_FILL_ARGS not found"
    return shlex.split(m[1])


def _gha_args() -> list[str]:
    text = (TOOLS / "gha" / "fill.yml").read_text()
    m = re.search(r"args:\n(?:\s+\w+:.*\n)*?\s+default:\s*\"(.*)\"", text)
    assert m, "fill.yml args default not found"
    return shlex.split(m[1])


def test_boxes_walk_the_plan16_order_in_reverse() -> None:
    args = _box_args()
    assert args.count("--reverse") == 1
    assert [a for a in args if a != "--reverse"] == PLAN16


def test_gha_waves_walk_the_same_order_forward() -> None:
    args = _gha_args()
    assert "--reverse" not in args and args == PLAN16
