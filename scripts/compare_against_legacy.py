#!/usr/bin/env python3
"""Compare current converter outputs against a legacy converter command."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class ComparisonFailure:
    """Describe one file that differs or fails during legacy comparison."""

    input_path: Path
    reason: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for legacy output comparison."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the current converter and a legacy converter command on the same "
            "VRML 1.0 inputs, then compare their VRML 2.0 outputs."
        )
    )
    legacy_group = parser.add_mutually_exclusive_group(required=True)
    legacy_group.add_argument(
        "--legacy-cmd",
        help=(
            "Shell command template for the legacy converter. Use {input} and {output} "
            "placeholders, for example: "
            "\"wine /path/to/vrml1tovrml2.exe {input} {output}\""
        ),
    )
    legacy_group.add_argument(
        "--legacy-exe",
        help=(
            "Path to vrml1tovrml2.exe. In WSL mode the script will copy this exe into "
            "the Windows temp directory and run it there."
        ),
    )
    parser.add_argument(
        "--current-bin",
        default="./target/debug/vrml1tovrml2-rs",
        help="Path to the current converter binary. Default: ./target/debug/vrml1tovrml2-rs",
    )
    parser.add_argument(
        "--public-root",
        default="tests/data/public_v1_cases",
        help="Directory containing vendored public VRML 1.0 samples.",
    )
    parser.add_argument(
        "--include-checked-in-cases",
        action="store_true",
        help="Also compare the checked-in wrl/cases/*/input.v1.wrl samples.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary output files for manual inspection.",
    )
    parser.add_argument(
        "--windows-temp-root",
        help=(
            "Optional Windows temp directory override. Accepts either a Windows path "
            "like C:\\\\Temp or a WSL path like /mnt/c/Temp."
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def collect_public_inputs(root: Path) -> list[Path]:
    """Return all public VRML 1.0 sample files under the given root."""

    return sorted(path for path in root.rglob("*.wrl") if path.is_file())


def collect_checked_in_case_inputs(root: Path) -> list[Path]:
    """Return all checked-in case inputs stored under wrl/cases."""

    cases_root = root / "wrl" / "cases"
    return sorted(cases_root.glob("*/input.v1.wrl"))


def normalize_vrml_text(input_text: str) -> str:
    """Normalize VRML text so formatting-only differences compare equal."""

    header = ""
    remaining_text = input_text
    if remaining_text.startswith("#VRML"):
        header_line, separator, tail = remaining_text.partition("\n")
        header = header_line.strip()
        remaining_text = tail if separator else ""

    normalized: list[str] = []
    in_string = False
    escaped = False
    in_comment = False
    pending_space = False

    for character in remaining_text:
        if in_comment:
            if character == "\n":
                in_comment = False
                pending_space = True
            continue

        if in_string:
            normalized.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == "#":
            in_comment = True
            continue

        if character == '"':
            if pending_space and normalized and normalized[-1] not in "{[,":
                normalized.append(" ")
            pending_space = False
            in_string = True
            normalized.append(character)
            continue

        if character.isspace():
            pending_space = True
            continue

        if character in "{}[],":
            if normalized and normalized[-1] == " ":
                normalized.pop()
            normalized.append(character)
            pending_space = False
            continue

        if pending_space and normalized and normalized[-1] not in "{[,":
            normalized.append(" ")
        pending_space = False
        normalized.append(character)

    body = "".join(normalized).strip()
    if header:
        joined = header if not body else f"{header} {body}"
        return canonicalize_scene_equivalences(joined)
    return canonicalize_scene_equivalences(body)


def canonicalize_scene_equivalences(normalized_text: str) -> str:
    """Collapse known semantically empty scene variants to one canonical representation."""

    header = "#VRML V2.0 utf8"
    if not normalized_text.startswith(header):
        return normalized_text

    body = normalized_text[len(header) :].strip()
    empty_scene_bodies = {
        "",
        "Group{}",
        "Group{children[]}",
        "Collision{collide FALSE children[Group{}]}",
        "Collision{collide FALSE children[Group{children[]}]}",
    }
    if body in empty_scene_bodies:
        return f"{header} <EMPTY_SCENE>"
    return normalized_text


def run_command(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one shell command and capture stdout and stderr."""

    return subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )


def run_process(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run one command without shell interpolation and capture stdout and stderr."""

    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def find_windows_command(command_name: str) -> str:
    """Resolve one Windows interoperability command from PATH or common WSL locations."""

    direct = shutil.which(command_name)
    if direct:
        return direct

    fallbacks = {
        "cmd.exe": ["/mnt/c/Windows/System32/cmd.exe"],
        "powershell.exe": ["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"],
    }
    for candidate in fallbacks.get(command_name, []):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(f"Could not locate required Windows command: {command_name}")


def wsl_to_windows_path(path: Path) -> str:
    """Convert one WSL path into a Windows path string."""

    result = run_process(["wslpath", "-w", str(path)])
    if result.returncode != 0:
        raise RuntimeError(f"wslpath failed for {path}: {result.stderr.strip()}")
    return result.stdout.strip()


def windows_to_wsl_path(path: str) -> Path:
    """Convert one Windows path string into a WSL path."""

    result = run_process(["wslpath", "-u", path])
    if result.returncode != 0:
        raise RuntimeError(f"wslpath failed for {path}: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def detect_windows_temp_root(override: str | None) -> Path:
    """Resolve the Windows temp directory as a WSL-visible path."""

    if override:
        override_path = Path(override)
        if override_path.exists():
            return override_path.resolve()
        return windows_to_wsl_path(override)

    cmd_result = run_process([find_windows_command("cmd.exe"), "/c", "echo", "%TEMP%"])
    if cmd_result.returncode != 0:
        raise RuntimeError(f"Failed to query Windows temp directory: {cmd_result.stderr.strip()}")
    windows_temp = cmd_result.stdout.strip().splitlines()[-1].strip()
    return windows_to_wsl_path(windows_temp).resolve()


def ensure_legacy_exe(legacy_exe: str | None, repo_root: Path) -> Path | None:
    """Resolve the optional legacy exe path against the repository root."""

    if legacy_exe is None:
        return None
    exe_path = Path(legacy_exe)
    if exe_path.is_absolute():
        return exe_path.resolve()
    return (repo_root / exe_path).resolve()


def stage_legacy_directory(legacy_exe: Path, stage_dir: Path) -> Path:
    """Copy the legacy exe and sibling support files into one Windows-visible stage directory."""

    source_dir = legacy_exe.parent
    for source_path in source_dir.iterdir():
        if source_path.is_file():
            shutil.copy2(source_path, stage_dir / source_path.name)
    return stage_dir / legacy_exe.name


def build_legacy_windows_command(
    staged_exe: Path,
    staged_input: Path,
    staged_output: Path,
    windows_stage_dir: str,
) -> str:
    """Build the PowerShell command used to invoke the staged legacy converter."""

    windows_exe = wsl_to_windows_path(staged_exe)
    windows_input = wsl_to_windows_path(staged_input)
    windows_output = wsl_to_windows_path(staged_output)

    if staged_exe.name.lower() == "vr1tovr2.exe":
        invoke = f"& '{windows_exe}' -o '{windows_output}' '{windows_input}'"
    else:
        invoke = f"& '{windows_exe}' '{windows_input}' '{windows_output}'"
    return f"Set-Location '{windows_stage_dir}'; {invoke}"


def run_legacy_converter(
    input_path: Path,
    legacy_output: Path,
    legacy_cmd_template: str | None,
    legacy_exe: Path | None,
    repo_root: Path,
    linux_work_dir: Path,
    windows_temp_root: Path | None,
) -> tuple[int, str]:
    """Run the legacy converter either from a command template or via WSL Windows temp staging."""

    if legacy_cmd_template is not None:
        legacy_command = legacy_cmd_template.format(input=str(input_path), output=str(legacy_output))
        legacy_result = run_command(legacy_command, cwd=repo_root)
        return legacy_result.returncode, legacy_result.stderr.strip()

    if legacy_exe is None or windows_temp_root is None:
        return 1, "legacy exe mode is not fully configured"

    stage_dir = windows_temp_root / f"vrml1tovrml2-legacy-{os.getpid()}-{input_path.stem}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    staged_exe = stage_legacy_directory(legacy_exe, stage_dir)
    staged_input = stage_dir / input_path.name
    staged_output = stage_dir / "legacy.wrl"

    shutil.copy2(input_path, staged_input)

    windows_stage_dir = wsl_to_windows_path(stage_dir)
    powershell_command = build_legacy_windows_command(
        staged_exe=staged_exe,
        staged_input=staged_input,
        staged_output=staged_output,
        windows_stage_dir=windows_stage_dir,
    )
    legacy_result = run_process(
        [
            find_windows_command("powershell.exe"),
            "-NoProfile",
            "-Command",
            powershell_command,
        ],
        cwd=None,
    )
    if legacy_result.returncode == 0 and staged_output.exists():
        shutil.copy2(staged_output, legacy_output)
    else:
        return legacy_result.returncode, legacy_result.stderr.strip() or legacy_result.stdout.strip()

    if linux_work_dir.exists():
        staged_snapshot = linux_work_dir / "legacy_stage"
        if staged_snapshot.exists():
            shutil.rmtree(staged_snapshot)
        shutil.copytree(stage_dir, staged_snapshot)
    shutil.rmtree(stage_dir, ignore_errors=True)
    return 0, ""


def compare_one_input(
    input_path: Path,
    repo_root: Path,
    current_bin: Path,
    legacy_cmd_template: str | None,
    legacy_exe: Path | None,
    work_dir: Path,
    windows_temp_root: Path | None,
) -> ComparisonFailure | None:
    """Run both converters for one input and compare normalized outputs."""

    current_output = work_dir / "current.wrl"
    legacy_output = work_dir / "legacy.wrl"

    current_result = run_command(
        f'"{current_bin}" "{input_path}" "{current_output}"',
        cwd=repo_root,
    )
    if current_result.returncode != 0:
        return ComparisonFailure(
            input_path=input_path,
            reason=f"current converter failed: {current_result.stderr.strip()}",
        )

    legacy_returncode, legacy_error = run_legacy_converter(
        input_path=input_path,
        legacy_output=legacy_output,
        legacy_cmd_template=legacy_cmd_template,
        legacy_exe=legacy_exe,
        repo_root=repo_root,
        linux_work_dir=work_dir,
        windows_temp_root=windows_temp_root,
    )
    if legacy_returncode != 0:
        return ComparisonFailure(
            input_path=input_path,
            reason=f"legacy converter failed: {legacy_error}",
        )

    current_text = normalize_vrml_text(current_output.read_text(encoding="utf-8", errors="strict"))
    legacy_text = normalize_vrml_text(legacy_output.read_text(encoding="utf-8", errors="strict"))
    if current_text != legacy_text:
        return ComparisonFailure(
            input_path=input_path,
            reason="normalized outputs differ",
        )
    return None


def main(argv: Iterable[str] | None = None) -> int:
    """Run legacy comparisons for the selected VRML 1.0 sample set."""

    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    current_bin = (repo_root / args.current_bin).resolve() if not Path(args.current_bin).is_absolute() else Path(args.current_bin)
    public_root = (repo_root / args.public_root).resolve() if not Path(args.public_root).is_absolute() else Path(args.public_root)
    legacy_exe = ensure_legacy_exe(args.legacy_exe, repo_root)
    windows_temp_root = detect_windows_temp_root(args.windows_temp_root) if legacy_exe is not None else None

    inputs = collect_public_inputs(public_root)
    if args.include_checked_in_cases:
        inputs.extend(collect_checked_in_case_inputs(repo_root))
    inputs = sorted(dict.fromkeys(inputs))

    failures: list[ComparisonFailure] = []
    temp_dir_obj = tempfile.TemporaryDirectory(prefix="vrml1tovrml2-legacy-compare-")
    temp_dir = Path(temp_dir_obj.name)

    try:
        for input_path in inputs:
            case_dir = temp_dir / input_path.stem
            case_dir.mkdir(parents=True, exist_ok=True)
            failure = compare_one_input(
                input_path=input_path,
                repo_root=repo_root,
                current_bin=current_bin,
                legacy_cmd_template=args.legacy_cmd,
                legacy_exe=legacy_exe,
                work_dir=case_dir,
                windows_temp_root=windows_temp_root,
            )
            if failure is not None:
                failures.append(failure)
                print(f"FAIL {input_path}: {failure.reason}")
            else:
                print(f"PASS {input_path}")
    finally:
        if args.keep_temp:
            kept_path = repo_root / "tmp" / temp_dir.name
            kept_path.parent.mkdir(parents=True, exist_ok=True)
            if kept_path.exists():
                shutil.rmtree(kept_path)
            shutil.copytree(temp_dir, kept_path)
            print(f"[info] kept temporary outputs at {kept_path}")
        temp_dir_obj.cleanup()

    print(
        f"SUMMARY pass={len(inputs) - len(failures)} fail={len(failures)} total={len(inputs)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
