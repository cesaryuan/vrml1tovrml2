"""Shared constants, logging, data classes, and spool-backed helpers."""

from __future__ import annotations

import atexit
import copy
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Iterator


LOGGER = logging.getLogger("vrml1tovrml2")

VRML1_HEADER = "#VRML V1.0 ascii"
VRML2_HEADER = "#VRML V2.0 utf8"

SPOOL_TARGET_FIELDS: set[tuple[str, str]] = {
    ("Material", "ambientColor"),
    ("Material", "diffuseColor"),
    ("Material", "specularColor"),
    ("Material", "emissiveColor"),
    ("Material", "shininess"),
    ("Material", "transparency"),
    ("Coordinate3", "point"),
    ("Normal", "vector"),
    ("IndexedFaceSet", "coordIndex"),
    ("IndexedFaceSet", "materialIndex"),
    ("IndexedFaceSet", "normalIndex"),
    ("IndexedFaceSet", "textureCoordIndex"),
    ("IndexedLineSet", "coordIndex"),
    ("IndexedLineSet", "materialIndex"),
}

_SPOOL_PATHS: set[str] = set()


def cleanup_spool_files() -> None:
    """Remove temporary spool files created for large streamed field values."""

    for path in list(_SPOOL_PATHS):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.debug("Could not remove spool file %s during cleanup", path)


atexit.register(cleanup_spool_files)


@dataclass(slots=True)
class Token:
    """Represent a lexical token from the VRML 1.0 source."""

    kind: str
    value: str
    line: int
    column: int


@dataclass(slots=True)
class UseRef:
    """Represent a `USE` reference in either the source or generated tree."""

    name: str


@dataclass(slots=True)
class SpoolSequence:
    """Represent a stream-backed immutable sequence stored on disk line by line."""

    path: str
    count: int
    arity: int
    scalar_type: str

    def __iter__(self) -> Iterator[Any]:
        """Yield sequence items from the backing spool file."""

        with open(self.path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if self.arity == 1:
                    yield self._cast_scalar(line)
                    continue
                parts = line.split("\t")
                yield tuple(self._cast_scalar(part) for part in parts)

    def __len__(self) -> int:
        """Return the known logical length without reading the spool file."""

        return self.count

    def first(self) -> Any:
        """Return the first value from the spool sequence."""

        for value in self:
            return value
        raise IndexError("SpoolSequence is empty")

    def __deepcopy__(self, memo: dict[int, Any]) -> "SpoolSequence":
        """Reuse immutable spool-backed sequences across deep copies."""

        memo[id(self)] = self
        return self

    def _cast_scalar(self, value: str) -> Any:
        """Cast one stored scalar string back to its logical Python type."""

        if self.scalar_type == "int":
            return int(value)
        if self.scalar_type == "float":
            return float(value)
        return value


class SpoolSequenceBuilder:
    """Write a large multi-value field to a temporary disk-backed sequence."""

    def __init__(self, arity: int, scalar_type: str) -> None:
        """Create a fresh temporary file used to accumulate sequence items."""

        fd, path = tempfile.mkstemp(prefix="vrml1tovrml2_", suffix=".spool")
        os.close(fd)
        _SPOOL_PATHS.add(path)
        self.path = path
        self.arity = arity
        self.scalar_type = scalar_type
        self.count = 0
        self.handle = open(path, "w", encoding="utf-8")

    def append(self, value: Any) -> None:
        """Append one scalar or tuple item to the spool file."""

        if self.arity == 1:
            self.handle.write(f"{value}\n")
        else:
            self.handle.write("\t".join(str(part) for part in value))
            self.handle.write("\n")
        self.count += 1

    def finalize(self) -> SpoolSequence:
        """Close the spool file and return its immutable read-side view."""

        self.handle.close()
        return SpoolSequence(
            path=self.path,
            count=self.count,
            arity=self.arity,
            scalar_type=self.scalar_type,
        )


@dataclass(slots=True)
class DefinitionRef:
    """Represent a reusable value that should emit `DEF` once and `USE` afterwards."""

    name: str
    value: Any


@dataclass(slots=True)
class AstNode:
    """Represent a parsed VRML 1.0 node instance."""

    node_type: str
    fields: dict[str, Any] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    def_name: str | None = None


@dataclass(slots=True)
class OutNode:
    """Represent a VRML 2.0 node ready for text serialization."""

    node_type: str
    fields: list[tuple[str, Any]] = field(default_factory=list)
    def_name: str | None = None


@dataclass(slots=True)
class TransformSpec:
    """Represent one persistent VRML 1.0 transform state change."""

    kind: str
    value: Any


@dataclass(slots=True)
class MaterialState:
    """Store material values so geometry conversion can reuse them."""

    ambient_colors: Any
    diffuse_colors: Any
    specular_colors: Any
    emissive_colors: Any
    shininess: Any
    transparency: Any
    def_name: str | None = None


@dataclass(slots=True)
class ConversionState:
    """Track inherited VRML 1.0 render state while traversing the scene."""

    transforms: list[TransformSpec] = field(default_factory=list)
    material: MaterialState | UseRef | DefinitionRef | None = None
    material_binding: str | None = None
    normal_binding: str | None = None
    shape_hints: dict[str, Any] = field(default_factory=dict)
    coordinate: OutNode | UseRef | DefinitionRef | None = None
    normal: OutNode | UseRef | DefinitionRef | None = None
    tex_coord: OutNode | UseRef | DefinitionRef | None = None
    texture: OutNode | UseRef | DefinitionRef | None = None
    texture_transform: OutNode | UseRef | DefinitionRef | None = None
    font_style: OutNode | UseRef | DefinitionRef | None = None
    definitions: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "ConversionState":
        """Return a safe copy for nested traversal scopes."""

        return ConversionState(
            transforms=copy.deepcopy(self.transforms),
            material=self.material,
            material_binding=self.material_binding,
            normal_binding=self.normal_binding,
            shape_hints=copy.deepcopy(self.shape_hints),
            coordinate=self.coordinate,
            normal=self.normal,
            tex_coord=self.tex_coord,
            texture=self.texture,
            texture_transform=self.texture_transform,
            font_style=self.font_style,
            definitions=self.definitions,
        )


class VrmlError(RuntimeError):
    """Describe a parse or conversion error with source context."""
