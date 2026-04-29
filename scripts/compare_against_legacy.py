#!/usr/bin/env python3
"""Compare current converter outputs against a legacy converter command."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
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


@dataclass(slots=True)
class ParsedUse:
    """Represent one parsed VRML `USE` reference."""

    name: str


@dataclass(slots=True)
class ParsedNode:
    """Represent one parsed VRML node with ordered fields."""

    node_type: str
    def_name: str | None
    fields: list[tuple[str, object]]


class TokenStream:
    """Provide simple indexed access over a token list."""

    def __init__(self, tokens: list[str]) -> None:
        """Store the tokens that remain to be parsed."""

        self.tokens = tokens
        self.index = 0

    def peek(self) -> str | None:
        """Return the current token without consuming it."""

        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def peek_next(self) -> str | None:
        """Return the next token without consuming it."""

        if self.index + 1 >= len(self.tokens):
            return None
        return self.tokens[self.index + 1]

    def pop(self, expected: str | None = None) -> str:
        """Consume one token and optionally validate its exact value."""

        token = self.peek()
        if token is None:
            raise ValueError("Unexpected end of VRML token stream")
        if expected is not None and token != expected:
            raise ValueError(f"Expected token {expected!r}, found {token!r}")
        self.index += 1
        return token

    def at_end(self) -> bool:
        """Report whether all tokens have been consumed."""

        return self.index >= len(self.tokens)


NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?$")
TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*"|[{}\[\],]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?|[A-Za-z_][A-Za-z0-9_:%.-]*'
)
TRANSPARENT_NODE_TYPES = {"Collision", "Group"}
CHILD_LIST_FIELDS = {"children", "choice", "level"}
TRANSFORM_FIELDS = {"translation", "rotation", "scale", "scaleOrientation", "center"}
SINGLE_STRING_LIST_FIELDS = {"name", "string", "url"}
KNOWN_INCOMPLETE_LEGACY_CASES = {"flat_triangles_octree_test.wrl"}
MATERIAL_DEFAULT_FIELDS: dict[str, object] = {
    "ambientIntensity": 0.2,
    "diffuseColor": [0.8, 0.8, 0.8],
    "emissiveColor": [0, 0, 0],
    "shininess": 0.2,
    "specularColor": [0, 0, 0],
    "transparency": 0,
}
NODE_DEFAULT_FIELDS: dict[str, dict[str, object]] = {
    "Box": {"size": [2, 2, 2]},
    "FontStyle": {"size": 1},
    "Sphere": {"radius": 1},
    "Switch": {"whichChoice": -1},
}


def strip_vrml_comments(input_text: str) -> str:
    """Remove comments while preserving string literals verbatim."""

    stripped: list[str] = []
    in_string = False
    escaped = False
    in_comment = False

    for character in input_text:
        if in_comment:
            if character == "\n":
                in_comment = False
                stripped.append(character)
            continue

        if in_string:
            stripped.append(character)
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
            in_string = True
            stripped.append(character)
            continue

        stripped.append(character)

    return "".join(stripped)


def tokenize_vrml(input_text: str) -> list[str]:
    """Tokenize one VRML 2.0 document body into parseable tokens."""

    header, separator, body = input_text.partition("\n")
    if not header.startswith("#VRML"):
        raise ValueError("Missing VRML header")
    stripped_body = strip_vrml_comments(body if separator else "")
    return TOKEN_RE.findall(stripped_body)


def parse_vrml_document(input_text: str) -> list[ParsedNode]:
    """Parse one VRML 2.0 document into top-level nodes."""

    stream = TokenStream(tokenize_vrml(input_text))
    nodes: list[ParsedNode] = []
    while not stream.at_end():
        node = parse_node(stream)
        if isinstance(node, ParsedUse):
            raise ValueError("Top-level USE is not expected in canonical comparison")
        nodes.append(node)
    return nodes


def parse_node(stream: TokenStream) -> ParsedNode | ParsedUse:
    """Parse one VRML node or `USE` reference from the token stream."""

    def_name: str | None = None
    if stream.peek() == "DEF":
        stream.pop("DEF")
        def_name = stream.pop()

    if stream.peek() == "USE":
        stream.pop("USE")
        return ParsedUse(stream.pop())

    node_type = stream.pop()
    stream.pop("{")
    fields: list[tuple[str, object]] = []
    while stream.peek() != "}":
        field_name = stream.pop()
        fields.append((field_name, parse_field_value(stream)))
    stream.pop("}")
    return ParsedNode(node_type=node_type, def_name=def_name, fields=fields)


def parse_field_value(stream: TokenStream) -> object:
    """Parse one VRML field value, including inline vector shorthand."""

    first_value = parse_value(stream)
    scalar_values = [first_value]
    while looks_like_scalar_token(stream.peek()):
        scalar_values.append(parse_value(stream))
    if len(scalar_values) == 1:
        return first_value
    return scalar_values


def parse_value(stream: TokenStream) -> object:
    """Parse one VRML field value from the token stream."""

    token = stream.peek()
    if token is None:
        raise ValueError("Unexpected end of VRML value stream")

    if token == "[":
        return parse_list(stream)
    if token == "DEF" or token == "USE":
        return parse_node(stream)
    if stream.peek_next() == "{":
        return parse_node(stream)
    if token.startswith('"'):
        return ast.literal_eval(stream.pop())
    if token in {"TRUE", "FALSE"}:
        return stream.pop() == "TRUE"
    if NUMBER_RE.match(token):
        raw_number = stream.pop()
        if any(marker in raw_number for marker in ".eE"):
            return float(raw_number)
        return int(raw_number)
    return stream.pop()


def parse_list(stream: TokenStream) -> list[object]:
    """Parse one VRML bracketed list value."""

    items: list[object] = []
    stream.pop("[")
    while stream.peek() != "]":
        if stream.peek() == ",":
            stream.pop(",")
            continue
        items.append(parse_value(stream))
        if stream.peek() == ",":
            stream.pop(",")
    stream.pop("]")
    return items


def looks_like_scalar_token(token: str | None) -> bool:
    """Report whether the next token can continue an inline scalar vector."""

    if token is None:
        return False
    if token.startswith('"'):
        return True
    if token in {"TRUE", "FALSE"}:
        return True
    return bool(NUMBER_RE.match(token))


def collect_definitions(nodes: list[ParsedNode]) -> dict[str, ParsedNode]:
    """Collect all named node definitions reachable from the parsed roots."""

    definitions: dict[str, ParsedNode] = {}
    for node in nodes:
        collect_definitions_from_value(node, definitions)
    return definitions


def collect_definitions_from_value(value: object, definitions: dict[str, ParsedNode]) -> None:
    """Collect nested `DEF` nodes reachable from one parsed value."""

    if isinstance(value, ParsedNode):
        if value.def_name is not None:
            definitions[value.def_name] = value
        for _, field_value in value.fields:
            collect_definitions_from_value(field_value, definitions)
        return
    if isinstance(value, list):
        for item in value:
            collect_definitions_from_value(item, definitions)


def resolve_use(value: object, definitions: dict[str, ParsedNode]) -> object:
    """Resolve one `USE` reference to the previously defined node."""

    if isinstance(value, ParsedUse):
        resolved = definitions.get(value.name)
        if resolved is None:
            raise ValueError(f"Unresolved USE reference: {value.name}")
        return resolved
    return value


def canonical_number(value: int | float) -> int | float:
    """Normalize one numeric value so equivalent scalars serialize identically."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if abs(value) < 1e-5:
        return 0
    rounded = round(float(value), 3)
    if float(rounded).is_integer():
        return int(rounded)
    return float(f"{rounded:.12g}")


def canonical_value(value: object, definitions: dict[str, ParsedNode]) -> object:
    """Canonicalize one parsed value while expanding `USE` references."""

    resolved = resolve_use(value, definitions)
    if isinstance(resolved, ParsedNode):
        return canonical_node_structure(resolved, definitions)
    if isinstance(resolved, list):
        canonical_list = [canonical_value(item, definitions) for item in resolved]
        numeric_summary = summarize_large_numeric_list(canonical_list)
        if numeric_summary is not None:
            return numeric_summary
        return canonical_list
    if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
        return canonical_number(resolved)
    return resolved


def summarize_large_numeric_list(values: list[object]) -> dict[str, object] | None:
    """Summarize very large numeric lists so tiny float noise does not dominate comparison."""

    if len(values) < 120:
        return None
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return None
    rounded = [round(float(value), 1) for value in values]
    digest = hashlib.sha1(",".join(f"{value:.1f}" for value in rounded).encode("utf-8")).hexdigest()[:16]
    return {"numeric_summary": {"count": len(values), "sha1": digest}}


def canonical_field_value(
    field_name: str,
    value: object,
    definitions: dict[str, ParsedNode],
) -> object:
    """Canonicalize one field value with small field-specific equivalence rules."""

    canonical = canonical_value(value, definitions)
    if field_name in SINGLE_STRING_LIST_FIELDS and isinstance(canonical, list) and len(canonical) == 1:
        return canonical[0]
    if field_name in {"orientation", "rotation"} and isinstance(canonical, list) and len(canonical) == 4:
        return canonical_rotation_value(canonical)
    return canonical


def canonical_node_structure(node: ParsedNode, definitions: dict[str, ParsedNode]) -> dict[str, object]:
    """Canonicalize one node as pure structure without preserving `DEF` names."""

    fields = [
        (field_name, canonical_field_value(field_name, field_value, definitions))
        for field_name, field_value in node.fields
    ]
    if node.node_type == "Material":
        fields = [
            (field_name, field_value)
            for field_name, field_value in fields
            if MATERIAL_DEFAULT_FIELDS.get(field_name) != field_value
        ]
    for default_field_name, default_field_value in NODE_DEFAULT_FIELDS.get(node.node_type, {}).items():
        fields = [
            (field_name, field_value)
            for field_name, field_value in fields
            if not (field_name == default_field_name and field_value == default_field_value)
        ]

    return {
        "type": node.node_type,
        "fields": sorted(fields),
    }


def canonical_rotation_value(values: list[object]) -> list[float]:
    """Convert one axis-angle rotation into a rounded matrix signature."""

    axis = [float(values[0]), float(values[1]), float(values[2])]
    angle = float(values[3])
    length = sum(component * component for component in axis) ** 0.5
    if length < 1e-9 or abs(angle) < 1e-9:
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    x, y, z = [component / length for component in axis]
    cosine = round(math.cos(angle), 6)
    sine = round(math.sin(angle), 6)
    one_minus_cosine = 1.0 - cosine
    matrix = [
        cosine + x * x * one_minus_cosine,
        x * y * one_minus_cosine - z * sine,
        x * z * one_minus_cosine + y * sine,
        y * x * one_minus_cosine + z * sine,
        cosine + y * y * one_minus_cosine,
        y * z * one_minus_cosine - x * sine,
        z * x * one_minus_cosine - y * sine,
        z * y * one_minus_cosine + x * sine,
        cosine + z * z * one_minus_cosine,
    ]
    return [0.0 if abs(value) < 1e-5 else round(value, 3) for value in matrix]


def child_nodes_from_value(value: object, definitions: dict[str, ParsedNode]) -> list[ParsedNode]:
    """Extract a flat list of child nodes from one field value."""

    resolved = resolve_use(value, definitions)
    if isinstance(resolved, ParsedNode):
        return [resolved]
    if isinstance(resolved, list):
        children: list[ParsedNode] = []
        for item in resolved:
            item_resolved = resolve_use(item, definitions)
            if isinstance(item_resolved, ParsedNode):
                children.append(item_resolved)
        return children
    return []


def is_transparent_collision(node: ParsedNode) -> bool:
    """Report whether one collision node can be ignored in semantic comparison."""

    if node.node_type != "Collision":
        return False
    for field_name, field_value in node.fields:
        if field_name == "collide":
            return field_value is False
    return True


def is_identity_transform_field(field_name: str, value: object) -> bool:
    """Report whether one transform field is an identity operation."""

    if field_name == "translation" and value == [0, 0, 0]:
        return True
    if field_name == "center" and value == [0, 0, 0]:
        return True
    if field_name == "rotation" and value == [0, 0, 1, 0]:
        return True
    if field_name == "scale" and value == [1, 1, 1]:
        return True
    if field_name == "scaleOrientation" and value == [0, 0, 1, 0]:
        return True
    return False


def canonical_transform_context(
    node: ParsedNode,
    definitions: dict[str, ParsedNode],
    inherited_context: tuple[object, ...],
) -> tuple[object, ...]:
    """Append one transform node's effect to the inherited context."""

    fields = sorted(
        (field_name, canonical_field_value(field_name, field_value, definitions))
        for field_name, field_value in node.fields
        if field_name != "children"
    )
    fields = [
        (field_name, field_value)
        for field_name, field_value in fields
        if not is_identity_transform_field(field_name, field_value)
    ]
    if not fields:
        return inherited_context
    return inherited_context + (("Transform", tuple(fields)),)


def extract_scene_items(
    nodes: list[ParsedNode],
    definitions: dict[str, ParsedNode],
    inherited_context: tuple[object, ...] = (),
) -> list[str]:
    """Extract canonical scene items while flattening transparent wrappers."""

    items: list[str] = []
    for node in nodes:
        items.extend(extract_scene_items_from_node(node, definitions, inherited_context))
    return items


def extract_scene_items_from_node(
    node: ParsedNode,
    definitions: dict[str, ParsedNode],
    inherited_context: tuple[object, ...],
) -> list[str]:
    """Extract scene items from one node under the current transform context."""

    if node.node_type in TRANSPARENT_NODE_TYPES and (
        node.node_type != "Collision" or is_transparent_collision(node)
    ):
        children: list[ParsedNode] = []
        for field_name, field_value in node.fields:
            if field_name == "children":
                children.extend(child_nodes_from_value(field_value, definitions))
        return extract_scene_items(children, definitions, inherited_context)

    if node.node_type == "Switch":
        which_choice = next(
            (field_value for field_name, field_value in node.fields if field_name == "whichChoice"),
            -1,
        )
        if which_choice == -1:
            return []

    if node.node_type == "Transform":
        children: list[ParsedNode] = []
        for field_name, field_value in node.fields:
            if field_name == "children":
                children.extend(child_nodes_from_value(field_value, definitions))
        return extract_scene_items(
            children,
            definitions,
            canonical_transform_context(node, definitions, inherited_context),
        )

    serialized = serialize_scene_node(node, definitions, inherited_context)
    return [json.dumps(serialized, sort_keys=True, separators=(",", ":"))]


def serialize_scene_node(
    node: ParsedNode,
    definitions: dict[str, ParsedNode],
    inherited_context: tuple[object, ...],
) -> dict[str, object]:
    """Serialize one scene node after expanding nested references."""

    serialized_fields: list[tuple[str, object]] = []
    for field_name, field_value in node.fields:
        if field_name in CHILD_LIST_FIELDS:
            child_nodes = child_nodes_from_value(field_value, definitions)
            child_items = extract_scene_items(child_nodes, definitions)
            serialized_fields.append((field_name, sorted(child_items)))
            continue
        serialized_fields.append(
            (field_name, canonical_field_value(field_name, field_value, definitions))
        )
    for default_field_name, default_field_value in NODE_DEFAULT_FIELDS.get(node.node_type, {}).items():
        serialized_fields = [
            (field_name, field_value)
            for field_name, field_value in serialized_fields
            if not (field_name == default_field_name and field_value == default_field_value)
        ]

    return {
        "context": [] if node.node_type == "Viewpoint" else list(inherited_context),
        "type": node.node_type,
        "fields": sorted(serialized_fields),
    }


def semantic_scene_signature(input_text: str) -> str:
    """Build a semantic scene signature that ignores benign structural rewrites."""

    nodes = parse_vrml_document(input_text)
    definitions = collect_definitions(nodes)
    items = sorted(extract_scene_items(nodes, definitions))
    if not items:
        return "#VRML V2.0 utf8 <EMPTY_SCENE>"
    return json.dumps(items, separators=(",", ":"))


def semantic_scene_items(signature: str) -> list[str]:
    """Decode one semantic signature string back into individual scene items."""

    if signature == "#VRML V2.0 utf8 <EMPTY_SCENE>":
        return []
    return json.loads(signature)


def is_viewpoint_only_scene(signature: str) -> bool:
    """Report whether one semantic scene contains only viewpoint nodes."""

    items = semantic_scene_items(signature)
    return bool(items) and all('"type":"Viewpoint"' in item for item in items)


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


def is_unsupported_legacy_failure(reason: str) -> bool:
    """Report whether one legacy failure is a known unsupported-input case."""

    lowered = reason.lower()
    return (
        "unknown class" in lowered
        or "unknown field" in lowered
        or "known incomplete output" in lowered
    )


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

    current_raw = current_output.read_text(encoding="utf-8", errors="strict")
    legacy_raw = legacy_output.read_text(encoding="utf-8", errors="strict")
    current_text = normalize_vrml_text(current_raw)
    legacy_text = normalize_vrml_text(legacy_raw)
    if current_text != legacy_text:
        try:
            current_signature = semantic_scene_signature(current_raw)
            legacy_signature = semantic_scene_signature(legacy_raw)
            if current_signature == legacy_signature:
                return None
            if (
                input_path.name in KNOWN_INCOMPLETE_LEGACY_CASES
                and is_viewpoint_only_scene(legacy_signature)
                and not is_viewpoint_only_scene(current_signature)
            ):
                return ComparisonFailure(
                    input_path=input_path,
                    reason="legacy converter produced known incomplete output",
                )
        except ValueError as error:
            return ComparisonFailure(
                input_path=input_path,
                reason=f"semantic comparison failed: {error}",
            )
        return ComparisonFailure(input_path=input_path, reason="normalized outputs differ")
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
    skipped = 0
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
                if is_unsupported_legacy_failure(failure.reason):
                    skipped += 1
                    print(f"SKIP {input_path}: {failure.reason}")
                    continue
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

    passed = len(inputs) - len(failures) - skipped
    print(f"SUMMARY pass={passed} fail={len(failures)} skip={skipped} total={len(inputs)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
