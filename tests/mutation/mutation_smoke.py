from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MutationCase:
    name: str
    path: Path
    original: str
    mutated: str
    pytest_args: tuple[str, ...]


MUTATIONS = [
    MutationCase(
        name="sql_security_allows_forbidden_pattern",
        path=ROOT / "src/agent/security.py",
        original="if re.search(pattern, sql_upper, re.IGNORECASE):",
        mutated="if False and re.search(pattern, sql_upper, re.IGNORECASE):",
        pytest_args=("tests/unit/test_agent_security.py",),
    ),
    MutationCase(
        name="sql_limit_clamps_small_limits_instead_of_large",
        path=ROOT / "src/agent/security.py",
        original="if current_limit > max_rows:",
        mutated="if current_limit < max_rows:",
        pytest_args=("tests/unit/test_agent_security.py", "tests/unit/test_agent_core_more.py"),
    ),
    MutationCase(
        name="bronze_missing_event_id_not_marked_duplicate",
        path=ROOT / "src/bronze/ingest.py",
        original='df["is_duplicate"] = seen_in_bronze | repeated_in_batch | missing_event_id',
        mutated='df["is_duplicate"] = seen_in_bronze | repeated_in_batch',
        pytest_args=("tests/unit/test_bronze_ingest.py", "tests/unit/test_schema_security_bronze_more.py"),
    ),
    MutationCase(
        name="silver_keeps_last_duplicate_instead_of_first",
        path=ROOT / "src/silver/pipeline_silver.py",
        original='canonical = with_event_id.drop_duplicates(subset=["_event_id_norm"], keep="first")',
        mutated='canonical = with_event_id.drop_duplicates(subset=["_event_id_norm"], keep="last")',
        pytest_args=("tests/unit/test_silver_pipeline.py", "tests/unit/test_silver_more.py"),
    ),
    MutationCase(
        name="gold_failure_rate_ignores_failed_transactions_denominator",
        path=ROOT / "src/gold/pipeline_gold.py",
        original='(gold["total_transactions"] + gold["failed_transactions"])',
        mutated='gold["total_transactions"]',
        pytest_args=("tests/unit/test_gold_pipeline.py", "tests/unit/test_gold_more.py"),
    ),
]


def _python() -> str:
    return sys.executable


def _apply_mutation(case: MutationCase) -> str:
    original_text = case.path.read_text(encoding="utf-8")
    if case.original not in original_text:
        raise RuntimeError(f"No se encontro el patron original para {case.name}: {case.path}")
    mutated_text = original_text.replace(case.original, case.mutated, 1)
    case.path.write_text(mutated_text, encoding="utf-8")
    return original_text


def _run_pytest(case: MutationCase, timeout: int) -> subprocess.CompletedProcess[str]:
    command = [_python(), "-m", "pytest", "-q", *case.pytest_args]
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def run_mutation(case: MutationCase, timeout: int) -> bool:
    backup = _apply_mutation(case)
    try:
        result = _run_pytest(case, timeout)
    finally:
        case.path.write_text(backup, encoding="utf-8")

    killed = result.returncode != 0
    status = "KILLED" if killed else "SURVIVED"
    print(f"\n[{status}] {case.name}")
    print(f"  file: {case.path.relative_to(ROOT)}")
    print(f"  tests: {' '.join(case.pytest_args)}")
    if not killed:
        print("  El mutante sobrevivio: falta una asercion que detecte este cambio.")
    return killed


def main() -> int:
    parser = argparse.ArgumentParser(description="Mutation smoke tests for critical fintech logic.")
    parser.add_argument("--list", action="store_true", help="Lista los mutantes disponibles.")
    parser.add_argument("--filter", default="", help="Ejecuta solo mutantes cuyo nombre contenga este texto.")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout por mutante en segundos.")
    args = parser.parse_args()

    selected = [case for case in MUTATIONS if args.filter.lower() in case.name.lower()]
    if args.list:
        for case in selected:
            print(case.name)
        return 0

    if not selected:
        print("No hay mutantes seleccionados.")
        return 2

    killed = 0
    for case in selected:
        if run_mutation(case, args.timeout):
            killed += 1

    survived = len(selected) - killed
    print("\nMUTATION SMOKE SUMMARY")
    print(f"  killed:   {killed}")
    print(f"  survived: {survived}")
    print(f"  total:    {len(selected)}")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
