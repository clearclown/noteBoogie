"""Best-effort logging of quality verdicts (Book Navigator, ADVANCED_ROADMAP §4-3).

Gate/refusal decisions land in the quality_event table (migration 30) so
thresholds can be calibrated from real traffic and, later, distilled into a
reward model (RL stage 3). Logging must never break the calling flow.
"""

from loguru import logger


async def log_quality_event(
    kind: str, name: str, score: float, verdict: str, details: dict | None = None
) -> None:
    from open_notebook.database.repository import repo_insert

    try:
        await repo_insert(
            "quality_event",
            [
                {
                    "kind": kind,
                    "name": (name or "")[:200],
                    "score": round(float(score), 3),
                    "verdict": verdict,
                    "details": details,
                }
            ],
        )
    except Exception as e:  # noqa: BLE001 - observability must not affect behavior
        logger.warning(f"quality_event insert failed: {e}")
