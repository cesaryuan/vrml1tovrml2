#!/usr/bin/env python3
"""Compare large VRML 2.0 files by structure instead of raw numeric payloads."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TextIO


NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
NODE_RE = re.compile(r"^\s*(?:DEF\s+\S+\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*$")
FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b")
NUMERIC_LIST_FIELDS: dict[str, int] = {
    "point": 3,
    "vector": 3,
    "color": 3,
    "coordIndex": 1,
    "colorIndex": 1,
    "normalIndex": 1,
    "texCoordIndex": 1,
    "texCoord": 2,
}


@dataclass(slots=True)
class StructureSummary:
    """Hold the normalized structure lines and aggregate counts for one file."""

    normalized_lines: list[str]
    node_counts: Counter[str]
    field_counts: Counter[str]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the structure comparison tool."""

    parser = argparse.ArgumentParser(
        description="Compare VRML 2.0 files by structure while collapsing huge numeric lists.",
    )
    parser.add_argument("left", help="First VRML 2.0 file")
    parser.add_argument("right", help="Second VRML 2.0 file")
    parser.add_argument(
        "--write-normalized",
        action="store_true",
        help="Write normalized structure files next to the compared inputs",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def summarize_file(path: Path) -> StructureSummary:
    """Build a structural summary from one VRML 2.0 file."""

    normalized_lines: list[str] = []
    node_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        pending_list_field: tuple[str, str] | None = None
        list_buffer: list[str] = []
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if pending_list_field is not None:
                list_buffer.append(line)
                if "]" in line:
                    field_name, indent = pending_list_field
                    normalized_lines.append(summarize_numeric_list(field_name, indent, list_buffer))
                    pending_list_field = None
                    list_buffer = []
                continue

            node_match = NODE_RE.match(line)
            if node_match:
                node_counts[node_match.group(1)] += 1

            field_match = FIELD_RE.match(line)
            if field_match:
                field_name = field_match.group(1)
                field_counts[field_name] += 1
                if field_name in NUMERIC_LIST_FIELDS and "[" in line:
                    pending_list_field = (field_name, line[: len(line) - len(line.lstrip())])
                    list_buffer = [line]
                    if "]" in line:
                        normalized_lines.append(summarize_numeric_list(field_name, pending_list_field[1], list_buffer))
                        pending_list_field = None
                        list_buffer = []
                    continue

            normalized_lines.append(strip_numeric_scalars(line))

    return StructureSummary(
        normalized_lines=normalized_lines,
        node_counts=node_counts,
        field_counts=field_counts,
    )


def summarize_numeric_list(field_name: str, indent: str, lines: list[str]) -> str:
    """Replace a huge numeric list with a one-line structural summary."""

    joined = "\n".join(lines)
    numbers = NUMBER_RE.findall(joined)
    scalar_count = len(numbers)
    arity = NUMERIC_LIST_FIELDS.get(field_name, 1)
    item_count = scalar_count if arity == 1 else scalar_count // arity
    digest = hashlib.sha1(" ".join(numbers).encode("utf-8")).hexdigest()[:12] if numbers else "empty"
    return (
        f"{indent}{field_name} [ "
        f"<numeric-list items={item_count} scalars={scalar_count} arity={arity} sha1={digest}> ]"
    )


def strip_numeric_scalars(line: str) -> str:
    """Replace scalar numeric payloads with a stable placeholder outside of huge lists."""

    if '"' in line:
        return replace_numbers_outside_strings(line)
    return NUMBER_RE.sub("<n>", line)


def replace_numbers_outside_strings(line: str) -> str:
    """Replace numbers while preserving quoted string literals verbatim."""

    parts = line.split('"')
    for index in range(0, len(parts), 2):
        parts[index] = NUMBER_RE.sub("<n>", parts[index])
    return '"'.join(parts)


def format_counter_diff(title: str, left: Counter[str], right: Counter[str]) -> str:
    """Format count differences for either node names or field names."""

    lines = [title]
    keys = sorted(set(left) | set(right))
    for key in keys:
        if left[key] != right[key]:
            lines.append(f"  {key}: left={left[key]} right={right[key]}")
    if len(lines) == 1:
        lines.append("  no count differences")
    return "\n".join(lines)


def write_normalized(path: Path, lines: list[str]) -> Path:
    """Write a normalized structure sidecar file and return its path."""

    output_path = path.with_suffix(path.suffix + ".structure.txt")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def main(argv: Iterable[str] | None = None) -> int:
    """Run structural comparison for two VRML 2.0 files."""

    args = parse_args(argv)
    left_path = Path(args.left)
    right_path = Path(args.right)
    left_summary = summarize_file(left_path)
    right_summary = summarize_file(right_path)

    print(format_counter_diff("Node count differences:", left_summary.node_counts, right_summary.node_counts))
    print()
    print(format_counter_diff("Field count differences:", left_summary.field_counts, right_summary.field_counts))
    print()

    diff_lines = list(
        difflib.unified_diff(
            left_summary.normalized_lines,
            right_summary.normalized_lines,
            fromfile=str(left_path),
            tofile=str(right_path),
            n=3,
        )
    )
    if diff_lines:
        print("Structure diff:")
        print("\n".join(diff_lines[:400]))
        if len(diff_lines) > 400:
            print("\n... diff truncated ...")
    else:
        print("Structure diff:\n  no structural line differences")

    if args.write_normalized:
        left_out = write_normalized(left_path, left_summary.normalized_lines)
        right_out = write_normalized(right_path, right_summary.normalized_lines)
        print()
        print(f"Normalized files written to:\n  {left_out}\n  {right_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
