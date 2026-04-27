#!/usr/bin/env python3
"""Convert a subset of VRML 1.0 scene graphs into VRML 2.0 text."""

from __future__ import annotations

import argparse
import copy
import logging
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger("vrml1tovrml2")

VRML1_HEADER = "#VRML V1.0 ascii"
VRML2_HEADER = "#VRML V2.0 utf8"


NODE_FIELD_SPECS: dict[str, dict[str, str]] = {
    "AsciiText": {
        "string": "mfstring",
        "spacing": "float",
        "justification": "enum",
        "width": "float",
    },
    "Cone": {
        "parts": "bitmask",
        "bottomRadius": "float",
        "height": "float",
    },
    "Coordinate3": {"point": "mfvec3"},
    "Cube": {"width": "float", "height": "float", "depth": "float"},
    "Cylinder": {"parts": "bitmask", "radius": "float", "height": "float"},
    "DirectionalLight": {
        "on": "bool",
        "intensity": "float",
        "color": "color",
        "direction": "vec3",
    },
    "FontStyle": {"size": "float", "family": "enum", "style": "enum"},
    "IndexedFaceSet": {
        "coordIndex": "mfint",
        "materialIndex": "mfint",
        "normalIndex": "mfint",
        "textureCoordIndex": "mfint",
    },
    "IndexedLineSet": {"coordIndex": "mfint", "materialIndex": "mfint"},
    "LOD": {"range": "mffloat"},
    "Material": {
        "ambientColor": "mfcolor",
        "diffuseColor": "mfcolor",
        "specularColor": "mfcolor",
        "emissiveColor": "mfcolor",
        "shininess": "mffloat",
        "transparency": "mffloat",
    },
    "MaterialBinding": {"value": "enum"},
    "MatrixTransform": {"matrix": "matrix"},
    "Normal": {"vector": "mfvec3"},
    "NormalBinding": {"value": "enum"},
    "OrthographicCamera": {
        "position": "vec3",
        "orientation": "rotation",
        "focalDistance": "float",
        "nearDistance": "float",
        "farDistance": "float",
        "height": "float",
    },
    "PerspectiveCamera": {
        "position": "vec3",
        "orientation": "rotation",
        "focalDistance": "float",
        "nearDistance": "float",
        "farDistance": "float",
        "heightAngle": "float",
    },
    "PointLight": {
        "on": "bool",
        "intensity": "float",
        "color": "color",
        "location": "vec3",
    },
    "PointSet": {"startIndex": "int", "numPoints": "int"},
    "Rotation": {"rotation": "rotation"},
    "Scale": {"scaleFactor": "vec3"},
    "ShapeHints": {
        "vertexOrdering": "enum",
        "shapeType": "enum",
        "faceType": "enum",
        "creaseAngle": "float",
    },
    "Sphere": {"radius": "float"},
    "SpotLight": {
        "on": "bool",
        "intensity": "float",
        "color": "color",
        "location": "vec3",
        "direction": "vec3",
        "dropOffRate": "float",
        "cutOffAngle": "float",
    },
    "Switch": {"whichChild": "int"},
    "Texture2": {
        "filename": "mfstring",
        "wrapS": "enum",
        "wrapT": "enum",
        "image": "mfint",
    },
    "Texture2Transform": {
        "translation": "vec2",
        "rotation": "float",
        "scaleFactor": "vec2",
        "center": "vec2",
    },
    "Texture2Transformation": {
        "translation": "vec2",
        "rotation": "float",
        "scaleFactor": "vec2",
        "center": "vec2",
    },
    "TextureCoordinate2": {"point": "mfvec2"},
    "Transform": {
        "translation": "vec3",
        "rotation": "rotation",
        "scaleFactor": "vec3",
        "scaleOrientation": "rotation",
        "center": "vec3",
    },
    "Translation": {"translation": "vec3"},
    "WWWAnchor": {"name": "mfstring", "description": "string", "map": "bool"},
    "WWWInline": {
        "name": "mfstring",
        "bboxCenter": "vec3",
        "bboxSize": "vec3",
    },
}


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
class DefinitionRef:
    """Represent a reusable value that should emit `DEF` once and `USE` afterwards."""

    name: str
    value: Any


@dataclass(slots=True)
class AstNode:
    """Represent a parsed VRML 1.0 node instance."""

    node_type: str
    fields: OrderedDict[str, Any] = field(default_factory=OrderedDict)
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

    ambient_colors: list[tuple[float, float, float]]
    diffuse_colors: list[tuple[float, float, float]]
    specular_colors: list[tuple[float, float, float]]
    emissive_colors: list[tuple[float, float, float]]
    shininess: list[float]
    transparency: list[float]
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


class VrmlTokenizer:
    """Turn VRML 1.0 text into a stream of reusable tokens."""

    def __init__(self, text: str) -> None:
        """Store the source text that will be tokenized."""

        self.text = text

    def tokenize(self) -> list[Token]:
        """Tokenize the source text while skipping whitespace and comments."""

        tokens: list[Token] = []
        line = 1
        column = 1
        index = 0
        length = len(self.text)
        while index < length:
            char = self.text[index]
            if char == "\n":
                line += 1
                column = 1
                index += 1
                continue
            if char in " \t\r,":
                column += 1
                index += 1
                continue
            if char == "#":
                while index < length and self.text[index] != "\n":
                    index += 1
                continue
            if char in "{}[]":
                tokens.append(Token("symbol", char, line, column))
                column += 1
                index += 1
                continue
            if char == '"':
                token, index, line, column = self._read_string(index, line, column)
                tokens.append(token)
                continue
            if char.isdigit() or char in "+-.":
                number_token, new_index = self._maybe_read_number(index, line, column)
                if number_token is not None:
                    tokens.append(number_token)
                    column += new_index - index
                    index = new_index
                    continue
            token, index, column = self._read_identifier(index, line, column)
            tokens.append(token)
        return tokens

    def _read_string(
        self, index: int, line: int, column: int
    ) -> tuple[Token, int, int, int]:
        """Read one quoted VRML string token."""

        start_column = column
        index += 1
        column += 1
        buffer: list[str] = []
        while index < len(self.text):
            char = self.text[index]
            if char == "\\" and index + 1 < len(self.text):
                escaped = self.text[index + 1]
                escape_table = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
                buffer.append(escape_table.get(escaped, escaped))
                index += 2
                column += 2
                continue
            if char == '"':
                index += 1
                column += 1
                return Token("string", "".join(buffer), line, start_column), index, line, column
            if char == "\n":
                line += 1
                column = 1
                index += 1
                buffer.append("\n")
                continue
            buffer.append(char)
            index += 1
            column += 1
        raise VrmlError(f"Unterminated string at line {line}, column {start_column}")

    def _maybe_read_number(
        self, index: int, line: int, column: int
    ) -> tuple[Token | None, int]:
        """Read a numeric token when the current bytes match a VRML number."""

        start = index
        text = self.text
        if text[index] in "+-":
            index += 1
        has_digit = False
        while index < len(text) and text[index].isdigit():
            has_digit = True
            index += 1
        if index < len(text) and text[index] == ".":
            index += 1
            while index < len(text) and text[index].isdigit():
                has_digit = True
                index += 1
        if not has_digit:
            return None, start
        if index < len(text) and text[index] in "eE":
            exp_index = index + 1
            if exp_index < len(text) and text[exp_index] in "+-":
                exp_index += 1
            exp_has_digit = False
            while exp_index < len(text) and text[exp_index].isdigit():
                exp_has_digit = True
                exp_index += 1
            if exp_has_digit:
                index = exp_index
        return Token("number", text[start:index], line, column), index

    def _read_identifier(self, index: int, line: int, column: int) -> tuple[Token, int, int]:
        """Read one identifier-like token until the next delimiter."""

        start = index
        while index < len(self.text) and self.text[index] not in " \t\r\n,{}[]\"#":
            index += 1
        value = self.text[start:index]
        return Token("identifier", value, line, column), index, column + (index - start)


class VrmlParser:
    """Parse VRML 1.0 tokens into an abstract syntax tree."""

    def __init__(self, text: str) -> None:
        """Tokenize the provided source text and prepare parse state."""

        self.text = text
        self.tokens = VrmlTokenizer(text).tokenize()
        self.position = 0

    def parse(self) -> list[Any]:
        """Parse the full source file into a list of root statements."""

        LOGGER.info("Parsing VRML 1.0 source")
        self._validate_header()
        statements: list[Any] = []
        while not self._at_end():
            statements.append(self._parse_statement())
        LOGGER.info("Parsed %d top-level statements", len(statements))
        return statements

    def _validate_header(self) -> None:
        """Ensure the input begins with the VRML 1.0 header expected by the tool."""

        stripped = self.text.lstrip()
        if not stripped.startswith(VRML1_HEADER):
            raise VrmlError("File does not have a valid VRML 1.0 header string")

    def _parse_statement(self) -> Any:
        """Parse one statement, including DEF/USE wrappers."""

        if self._match_identifier("DEF"):
            def_name = self._consume("identifier", "Expected name after DEF").value
            node = self._parse_statement()
            if isinstance(node, AstNode):
                node.def_name = def_name
                return node
            raise VrmlError(f"DEF {def_name} must target a node")
        if self._match_identifier("USE"):
            name = self._consume("identifier", "Expected name after USE").value
            return UseRef(name)
        return self._parse_node()

    def _parse_node(self) -> AstNode:
        """Parse one VRML 1.0 node body with fields and/or child nodes."""

        node_type = self._consume("identifier", "Expected node type").value
        self._consume_symbol("{", f"Expected '{{' after node type {node_type}")
        fields: OrderedDict[str, Any] = OrderedDict()
        children: list[Any] = []
        node_field_specs = NODE_FIELD_SPECS.get(node_type, {})
        while not self._check_symbol("}") and not self._at_end():
            if self._looks_like_statement():
                children.append(self._parse_statement())
                continue
            field_name = self._consume("identifier", f"Expected field name in {node_type}").value
            field_kind = node_field_specs.get(field_name, "auto")
            fields[field_name] = self._parse_field_value(node_type, field_name, field_kind)
        self._consume_symbol("}", f"Expected '}}' after node {node_type}")
        return AstNode(node_type=node_type, fields=fields, children=children)

    def _parse_field_value(self, node_type: str, field_name: str, field_kind: str) -> Any:
        """Parse one field value according to the node-specific field kind."""

        if field_kind == "bool":
            return self._parse_bool()
        if field_kind == "int":
            return int(self._consume_number("Expected integer").value)
        if field_kind == "float":
            return float(self._consume_number("Expected float").value)
        if field_kind == "vec2":
            return self._parse_vector(2)
        if field_kind == "vec3":
            return self._parse_vector(3)
        if field_kind == "rotation":
            return self._parse_vector(4)
        if field_kind == "matrix":
            return self._parse_number_list(16)
        if field_kind == "color":
            return self._parse_vector(3)
        if field_kind == "enum":
            return self._consume("identifier", f"Expected enum value for {field_name}").value
        if field_kind == "bitmask":
            return self._parse_bitmask(node_type)
        if field_kind == "mfint":
            return self._parse_multi_numeric_values(1, cast=int)
        if field_kind == "mffloat":
            return self._parse_multi_numeric_values(1, cast=float)
        if field_kind == "mfvec2":
            return self._parse_multi_numeric_values(2, cast=float)
        if field_kind == "mfvec3":
            return self._parse_multi_numeric_values(3, cast=float)
        if field_kind == "mfcolor":
            return self._parse_multi_numeric_values(3, cast=float)
        if field_kind == "mfstring":
            return self._parse_multi_strings()
        return self._parse_auto_value(field_name)

    def _parse_auto_value(self, field_name: str) -> Any:
        """Parse a best-effort field value for rarely used or unknown fields."""

        if self._match_identifier("DEF"):
            def_name = self._consume("identifier", "Expected name after DEF").value
            value = self._parse_statement()
            if isinstance(value, AstNode):
                value.def_name = def_name
                return value
            raise VrmlError(f"DEF {def_name} must target a node")
        if self._match_identifier("USE"):
            return UseRef(self._consume("identifier", "Expected name after USE").value)
        if self._check_symbol("["):
            return self._parse_generic_list()
        if self._peek_kind("string"):
            return self._advance().value
        if self._peek_kind("number"):
            return float(self._advance().value)
        if self._looks_like_statement():
            return self._parse_statement()
        if self._peek_kind("identifier"):
            return self._advance().value
        token = self._peek()
        raise VrmlError(
            f"Unsupported value while reading field {field_name} at line {token.line}, column {token.column}"
        )

    def _parse_generic_list(self) -> list[Any]:
        """Parse a bracketed list without imposing a specific element type."""

        values: list[Any] = []
        self._consume_symbol("[", "Expected '['")
        while not self._check_symbol("]") and not self._at_end():
            if self._looks_like_statement():
                values.append(self._parse_statement())
                continue
            if self._peek_kind("string"):
                values.append(self._advance().value)
                continue
            if self._peek_kind("number"):
                values.append(float(self._advance().value))
                continue
            if self._peek_kind("identifier"):
                values.append(self._advance().value)
                continue
            raise VrmlError("Unsupported list value")
        self._consume_symbol("]", "Expected ']'")
        return values

    def _parse_multi_strings(self) -> list[str]:
        """Parse one or more string values, optionally inside brackets."""

        values: list[str] = []
        if self._check_symbol("["):
            self._consume_symbol("[", "Expected '['")
            while not self._check_symbol("]") and not self._at_end():
                token = self._peek()
                if token.kind not in {"string", "identifier"}:
                    raise VrmlError(f"Expected string value at line {token.line}, column {token.column}")
                values.append(self._advance().value)
            self._consume_symbol("]", "Expected ']'")
            return values
        token = self._peek()
        if token.kind not in {"string", "identifier"}:
            raise VrmlError(f"Expected string value at line {token.line}, column {token.column}")
        return [self._advance().value]

    def _parse_multi_numeric_values(self, arity: int, cast: Any) -> list[Any]:
        """Parse a multi-value field either as one value or as a bracketed list."""

        if not self._check_symbol("["):
            single = self._parse_multi_value_item(arity, cast)
            return [single]
        values: list[Any] = []
        self._consume_symbol("[", "Expected '['")
        while not self._check_symbol("]") and not self._at_end():
            values.append(self._parse_multi_value_item(arity, cast))
        self._consume_symbol("]", "Expected ']'")
        return values

    def _parse_multi_value_item(self, arity: int, cast: Any) -> Any:
        """Parse one multi-field item with the requested numeric arity."""

        numbers = [cast(self._consume_number("Expected numeric value").value) for _ in range(arity)]
        if arity == 1:
            return numbers[0]
        return tuple(numbers)

    def _parse_vector(self, arity: int) -> tuple[float, ...]:
        """Parse one fixed-width numeric vector."""

        return tuple(float(self._consume_number("Expected numeric vector value").value) for _ in range(arity))

    def _parse_number_list(self, count: int) -> list[float]:
        """Parse a fixed-size list of floats, optionally bracketed."""

        values: list[float] = []
        if self._check_symbol("["):
            self._consume_symbol("[", "Expected '['")
        for _ in range(count):
            values.append(float(self._consume_number("Expected numeric matrix value").value))
        if self._check_symbol("]"):
            self._consume_symbol("]", "Expected ']'")
        return values

    def _parse_bitmask(self, node_type: str) -> list[str]:
        """Parse a sequence of symbolic bitmask parts until the next field boundary."""

        values: list[str] = []
        known_fields = set(NODE_FIELD_SPECS.get(node_type, {}))
        while not self._at_end():
            if self._check_symbol("}") or self._check_symbol("]"):
                break
            token = self._peek()
            if token.kind != "identifier":
                break
            if values and token.value in known_fields:
                break
            values.append(self._advance().value)
        if not values:
            token = self._peek()
            raise VrmlError(f"Expected symbolic value at line {token.line}, column {token.column}")
        return values

    def _parse_bool(self) -> bool:
        """Parse a VRML boolean literal."""

        value = self._consume("identifier", "Expected TRUE or FALSE").value.upper()
        if value == "TRUE":
            return True
        if value == "FALSE":
            return False
        raise VrmlError(f"Illegal value for QvSFBool: {value}")

    def _looks_like_statement(self) -> bool:
        """Return whether the current token begins a child statement."""

        if self._peek_value("DEF") or self._peek_value("USE"):
            return True
        if self._peek_kind("identifier") and self._peek_next_symbol("{"):
            return True
        return False

    def _consume(self, kind: str, message: str) -> Token:
        """Consume one token of the expected kind."""

        if self._peek_kind(kind):
            return self._advance()
        token = self._peek()
        raise VrmlError(f"{message} at line {token.line}, column {token.column}")

    def _consume_number(self, message: str) -> Token:
        """Consume one numeric token."""

        return self._consume("number", message)

    def _consume_symbol(self, value: str, message: str) -> Token:
        """Consume one symbol token with the expected spelling."""

        if self._peek_kind("symbol") and self._peek().value == value:
            return self._advance()
        token = self._peek()
        raise VrmlError(f"{message} at line {token.line}, column {token.column}")

    def _match_identifier(self, value: str) -> bool:
        """Conditionally consume one identifier when it matches the given value."""

        if self._peek_kind("identifier") and self._peek().value == value:
            self._advance()
            return True
        return False

    def _check_symbol(self, value: str) -> bool:
        """Return whether the current token is the requested symbol."""

        return self._peek_kind("symbol") and self._peek().value == value

    def _peek_kind(self, kind: str) -> bool:
        """Return whether the current token has the requested kind."""

        return not self._at_end() and self._peek().kind == kind

    def _peek_value(self, value: str) -> bool:
        """Return whether the current token has the requested string value."""

        return not self._at_end() and self._peek().value == value

    def _peek_next_symbol(self, value: str) -> bool:
        """Return whether the next token is the requested symbol."""

        if self.position + 1 >= len(self.tokens):
            return False
        next_token = self.tokens[self.position + 1]
        return next_token.kind == "symbol" and next_token.value == value

    def _advance(self) -> Token:
        """Consume and return the current token."""

        token = self.tokens[self.position]
        self.position += 1
        return token

    def _peek(self) -> Token:
        """Return the current token or a synthetic EOF token."""

        if self._at_end():
            return Token("eof", "", -1, -1)
        return self.tokens[self.position]

    def _at_end(self) -> bool:
        """Return whether all tokens have already been consumed."""

        return self.position >= len(self.tokens)


class VrmlConverter:
    """Convert parsed VRML 1.0 nodes into VRML 2.0 nodes."""

    def convert(self, statements: list[Any]) -> list[OutNode]:
        """Convert the root statement list into VRML 2.0 output nodes."""

        LOGGER.info("Converting parsed scene graph to VRML 2.0")
        self.emitted_defs: set[str] = set()
        self.generated_material_names: dict[tuple[Any, ...], str] = {}
        self.generated_material_counter = 0
        state = ConversionState()
        nodes = self._convert_sequence(statements, state)
        if nodes:
            nodes = [self._wrap_root(nodes)]
        LOGGER.info("Generated %d VRML 2.0 top-level nodes", len(nodes))
        return nodes

    def _wrap_root(self, nodes: list[OutNode]) -> OutNode:
        """Wrap the scene in the same root structure used by the original converter."""

        group = OutNode("Group", fields=[("children", nodes)])
        return OutNode("Collision", fields=[("collide", False), ("children", [group])])

    def _convert_sequence(self, statements: list[Any], state: ConversionState) -> list[OutNode]:
        """Convert one ordered list of statements under the same traversal state."""

        emitted: list[OutNode] = []
        for statement in statements:
            emitted.extend(self._convert_statement(statement, state))
        return emitted

    def _convert_statement(self, statement: Any, state: ConversionState) -> list[OutNode]:
        """Convert one statement and return any emitted VRML 2.0 nodes."""

        if isinstance(statement, UseRef):
            return self._apply_use_reference(statement, state)
        node: AstNode = statement
        LOGGER.debug("Visiting node %s", node.node_type)
        if node.node_type == "Material":
            state.material = self._convert_material_state(node, state)
            return []
        if node.node_type == "MaterialBinding":
            state.material_binding = str(node.fields.get("value", "OVERALL"))
            return []
        if node.node_type == "NormalBinding":
            state.normal_binding = str(node.fields.get("value", "PER_VERTEX_INDEXED"))
            return []
        if node.node_type == "ShapeHints":
            state.shape_hints = dict(node.fields)
            return []
        if node.node_type == "Coordinate3":
            state.coordinate = self._register_definition(node, self._simple_out_node(node, "Coordinate"), state)
            return []
        if node.node_type == "Normal":
            state.normal = self._register_definition(node, self._simple_out_node(node, "Normal"), state)
            return []
        if node.node_type == "TextureCoordinate2":
            state.tex_coord = self._register_definition(
                node,
                self._simple_out_node(node, "TextureCoordinate"),
                state,
            )
            return []
        if node.node_type == "Texture2":
            state.texture = self._register_definition(node, self._convert_texture(node), state)
            return []
        if node.node_type in {"Texture2Transform", "Texture2Transformation"}:
            state.texture_transform = self._register_definition(
                node,
                self._convert_texture_transform(node),
                state,
            )
            return []
        if node.node_type == "FontStyle":
            state.font_style = self._register_definition(node, self._convert_font_style(node), state)
            return []
        if node.node_type in {"Translation", "Rotation", "Scale", "MatrixTransform", "Transform"}:
            self._apply_transform(node, state)
            return []
        if node.node_type in {"Separator", "TransformSeparator", "Group"}:
            return self._convert_group_like(node, state)
        if node.node_type == "Switch":
            return [self._wrap_transforms(self._convert_switch(node, state), state.transforms)]
        if node.node_type == "WWWAnchor":
            return [self._wrap_transforms(self._convert_anchor(node, state), state.transforms)]
        if node.node_type == "WWWInline":
            return [self._wrap_transforms(self._convert_inline(node), state.transforms)]
        if node.node_type == "LOD":
            return [self._wrap_transforms(self._convert_lod(node, state), state.transforms)]
        if node.node_type in {"IndexedFaceSet", "IndexedLineSet", "PointSet", "Cube", "Cone", "Cylinder", "Sphere", "AsciiText"}:
            return [self._wrap_transforms(self._convert_shape(node, state), state.transforms)]
        if node.node_type in {"DirectionalLight", "PointLight", "SpotLight"}:
            return [self._store_emitted_definition(self._wrap_transforms(self._convert_light(node), state.transforms), state)]
        if node.node_type in {"PerspectiveCamera", "OrthographicCamera"}:
            return [self._store_emitted_definition(self._wrap_transforms(self._convert_camera(node), state.transforms), state)]
        LOGGER.warning("Skipping unsupported node type %s", node.node_type)
        return []

    def _convert_group_like(self, node: AstNode, state: ConversionState) -> list[OutNode]:
        """Convert a grouping node while keeping its inherited state local."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        if not children:
            return []
        if node.def_name or len(children) > 1:
            group = OutNode("Group", fields=[("children", children)], def_name=node.def_name)
            return [self._store_emitted_definition(self._wrap_transforms(group, state.transforms), state)]
        if node.def_name:
            children[0].def_name = node.def_name
        return [self._store_emitted_definition(self._wrap_transforms(children[0], state.transforms), state)]

    def _convert_switch(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 Switch node."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        which_child = int(node.fields.get("whichChild", -1))
        return OutNode(
            "Switch",
            fields=[("whichChoice", which_child), ("choice", children)],
            def_name=node.def_name,
        )

    def _convert_anchor(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 WWWAnchor node into a VRML 2.0 Anchor."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        url_values = self._to_string_list(node.fields.get("name", []))
        description = node.fields.get("description")
        fields: list[tuple[str, Any]] = [("url", url_values)]
        if description:
            fields.append(("description", str(description)))
        fields.append(("children", children))
        return OutNode("Anchor", fields=fields, def_name=node.def_name)

    def _convert_inline(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 WWWInline node into an Inline node."""

        url_values = self._to_string_list(node.fields.get("name", []))
        fields: list[tuple[str, Any]] = [("url", url_values)]
        if "bboxCenter" in node.fields:
            fields.append(("bboxCenter", node.fields["bboxCenter"]))
        if "bboxSize" in node.fields:
            fields.append(("bboxSize", node.fields["bboxSize"]))
        return OutNode("Inline", fields=fields, def_name=node.def_name)

    def _convert_lod(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 LOD node into a VRML 2.0 LOD."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        fields: list[tuple[str, Any]] = [("level", children)]
        if "range" in node.fields:
            fields.append(("range", node.fields["range"]))
        return OutNode("LOD", fields=fields, def_name=node.def_name)

    def _convert_shape(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert one VRML 1.0 geometry node into a VRML 2.0 Shape."""

        geometry = self._convert_geometry(node, state)
        appearance = self._build_appearance(state, node)
        fields: list[tuple[str, Any]] = []
        if appearance is not None:
            fields.append(("appearance", appearance))
        fields.append(("geometry", geometry))
        return OutNode("Shape", fields=fields, def_name=node.def_name)

    def _convert_geometry(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert the geometry-specific portion of a shape node."""

        if node.node_type == "IndexedFaceSet":
            return self._convert_indexed_face_set(node, state)
        if node.node_type == "IndexedLineSet":
            return self._convert_indexed_line_set(node, state)
        if node.node_type == "PointSet":
            return self._convert_point_set(node, state)
        if node.node_type == "Cube":
            size = (
                float(node.fields.get("width", 2.0)),
                float(node.fields.get("height", 2.0)),
                float(node.fields.get("depth", 2.0)),
            )
            return OutNode("Box", fields=[("size", size)])
        if node.node_type == "Cone":
            parts = {part.upper() for part in self._to_string_list(node.fields.get("parts", ["ALL"]))}
            fields: list[tuple[str, Any]] = [
                ("bottomRadius", float(node.fields.get("bottomRadius", 1.0))),
                ("height", float(node.fields.get("height", 2.0))),
            ]
            if "BOTTOM" not in parts and "ALL" not in parts:
                fields.append(("bottom", False))
            if "SIDES" not in parts and "ALL" not in parts:
                fields.append(("side", False))
            return OutNode("Cone", fields=fields)
        if node.node_type == "Cylinder":
            parts = {part.upper() for part in self._to_string_list(node.fields.get("parts", ["ALL"]))}
            fields = [
                ("radius", float(node.fields.get("radius", 1.0))),
                ("height", float(node.fields.get("height", 2.0))),
            ]
            if "BOTTOM" not in parts and "ALL" not in parts:
                fields.append(("bottom", False))
            if "TOP" not in parts and "ALL" not in parts:
                fields.append(("top", False))
            if "SIDES" not in parts and "ALL" not in parts:
                fields.append(("side", False))
            return OutNode("Cylinder", fields=fields)
        if node.node_type == "Sphere":
            return OutNode("Sphere", fields=[("radius", float(node.fields.get("radius", 1.0)))])
        if node.node_type == "AsciiText":
            return self._convert_ascii_text(node, state)
        raise VrmlError(f"Unsupported geometry node {node.node_type}")

    def _convert_indexed_face_set(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 IndexedFaceSet into VRML 2.0 IndexedFaceSet."""

        fields: list[tuple[str, Any]] = []
        if state.coordinate is not None:
            fields.append(("coord", self._materialize_reference(state.coordinate)))
        if state.normal is not None:
            fields.append(("normal", self._materialize_reference(state.normal)))
        if state.tex_coord is not None:
            fields.append(("texCoord", self._materialize_reference(state.tex_coord)))
        color_node, color_index = self._build_color_node(state, node)
        if color_node is not None:
            fields.append(("color", color_node))
        fields.append(("coordIndex", list(node.fields.get("coordIndex", []))))
        if "normalIndex" in node.fields:
            fields.append(("normalIndex", list(node.fields["normalIndex"])))
        if "textureCoordIndex" in node.fields:
            fields.append(("texCoordIndex", list(node.fields["textureCoordIndex"])))
        if color_index:
            fields.append(("colorIndex", color_index))
        material_binding = (state.material_binding or "OVERALL").upper()
        normal_binding = (state.normal_binding or "PER_VERTEX_INDEXED").upper()
        if material_binding in {"PER_FACE", "PER_FACE_INDEXED", "PER_PART", "PER_PART_INDEXED"}:
            fields.append(("colorPerVertex", False))
        if normal_binding in {"PER_FACE", "PER_FACE_INDEXED", "PER_PART", "PER_PART_INDEXED"}:
            fields.append(("normalPerVertex", False))
        shape_hints = state.shape_hints
        if shape_hints.get("vertexOrdering", "").upper() == "CLOCKWISE":
            fields.append(("ccw", False))
        # The original converter emits these defaults aggressively.
        if shape_hints.get("shapeType", "").upper() != "SOLID":
            fields.append(("solid", False))
        if shape_hints.get("faceType") and shape_hints.get("faceType", "").upper() != "CONVEX":
            fields.append(("convex", False))
        fields.append(("creaseAngle", float(shape_hints.get("creaseAngle", 0.5))))
        return OutNode("IndexedFaceSet", fields=fields)

    def _convert_indexed_line_set(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 IndexedLineSet into VRML 2.0 IndexedLineSet."""

        fields: list[tuple[str, Any]] = []
        if state.coordinate is not None:
            fields.append(("coord", self._materialize_reference(state.coordinate)))
        color_node, color_index = self._build_color_node(state, node)
        if color_node is not None:
            fields.append(("color", color_node))
        fields.append(("coordIndex", list(node.fields.get("coordIndex", []))))
        if color_index:
            fields.append(("colorIndex", color_index))
        material_binding = (state.material_binding or "OVERALL").upper()
        if material_binding in {"PER_FACE", "PER_FACE_INDEXED", "PER_PART", "PER_PART_INDEXED"}:
            fields.append(("colorPerVertex", False))
        return OutNode("IndexedLineSet", fields=fields)

    def _convert_point_set(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 PointSet while honoring startIndex and numPoints."""

        fields: list[tuple[str, Any]] = []
        coordinate = self._slice_coordinate_node(state.coordinate, node.fields)
        if coordinate is not None:
            fields.append(("coord", coordinate))
        color_node, _ = self._build_color_node(state, node)
        if color_node is not None:
            fields.append(("color", color_node))
        return OutNode("PointSet", fields=fields)

    def _convert_ascii_text(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert VRML 1.0 AsciiText into a VRML 2.0 Text node."""

        string_values = self._to_string_list(node.fields.get("string", []))
        font_style = self._merge_font_style(state.font_style, node.fields)
        text_fields: list[tuple[str, Any]] = [("string", string_values)]
        width = node.fields.get("width")
        if width is not None:
            text_fields.append(("maxExtent", float(width)))
        if font_style is not None:
            text_fields.append(("fontStyle", font_style))
        return OutNode("Text", fields=text_fields)

    def _convert_light(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 light node to its VRML 2.0 equivalent."""

        field_names = {
            "DirectionalLight": ["on", "intensity", "color", "direction"],
            "PointLight": ["on", "intensity", "color", "location"],
            "SpotLight": ["on", "intensity", "color", "location", "direction", "dropOffRate", "cutOffAngle"],
        }[node.node_type]
        fields = [(name, node.fields[name]) for name in field_names if name in node.fields]
        return OutNode(node.node_type, fields=fields, def_name=node.def_name)

    def _convert_camera(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 camera node to a VRML 2.0 Viewpoint."""

        fields: list[tuple[str, Any]] = []
        if "position" in node.fields:
            fields.append(("position", node.fields["position"]))
        if "orientation" in node.fields:
            fields.append(("orientation", node.fields["orientation"]))
        if node.node_type == "PerspectiveCamera" and "heightAngle" in node.fields:
            fields.append(("fieldOfView", float(node.fields["heightAngle"])))
        return OutNode("Viewpoint", fields=fields, def_name=node.def_name)

    def _apply_transform(self, node: AstNode, state: ConversionState) -> None:
        """Update the persistent transform state after a transform node."""

        if node.node_type == "Translation":
            state.transforms.append(TransformSpec("translation", node.fields.get("translation", (0.0, 0.0, 0.0))))
            return
        if node.node_type == "Rotation":
            state.transforms.append(TransformSpec("rotation", node.fields.get("rotation", (0.0, 0.0, 1.0, 0.0))))
            return
        if node.node_type == "Scale":
            state.transforms.append(TransformSpec("scale", node.fields.get("scaleFactor", (1.0, 1.0, 1.0))))
            return
        if node.node_type == "MatrixTransform":
            matrix = node.fields.get("matrix")
            if matrix:
                state.transforms.append(TransformSpec("matrix", matrix))
            return
        if node.node_type == "Transform":
            if "translation" in node.fields:
                state.transforms.append(TransformSpec("translation", node.fields["translation"]))
            if "rotation" in node.fields:
                state.transforms.append(TransformSpec("rotation", node.fields["rotation"]))
            if "scaleFactor" in node.fields:
                state.transforms.append(TransformSpec("scale", node.fields["scaleFactor"]))

    def _build_appearance(self, state: ConversionState, geometry_node: AstNode) -> OutNode | None:
        """Build a VRML 2.0 Appearance node from the current render state."""

        appearance_fields: list[tuple[str, Any]] = []
        material = self._resolve_material(state.material, state)
        appearance_fields.append(("material", self._material_reference_to_output(state.material, material)))
        if state.texture is not None:
            appearance_fields.append(("texture", self._materialize_reference(state.texture)))
        if state.texture_transform is not None:
            appearance_fields.append(("textureTransform", self._materialize_reference(state.texture_transform)))
        return OutNode("Appearance", fields=appearance_fields)

    def _build_color_node(self, state: ConversionState, geometry_node: AstNode) -> tuple[OutNode | None, list[int] | None]:
        """Extract diffuse colors from the current material when they drive geometry color."""

        material = self._resolve_material(state.material, state)
        if material is None or not material.diffuse_colors:
            return None, None
        if len(material.diffuse_colors) == 1 and geometry_node.node_type not in {"IndexedLineSet", "PointSet"}:
            return None, None
        color_node = OutNode("Color", fields=[("color", list(material.diffuse_colors))])
        color_index = None
        if "materialIndex" in geometry_node.fields:
            color_index = list(geometry_node.fields["materialIndex"])
        return color_node, color_index

    def _convert_material_state(self, node: AstNode, state: ConversionState) -> MaterialState | UseRef:
        """Convert a VRML 1.0 Material node into reusable structured material data."""

        material_state = MaterialState(
            ambient_colors=self._ensure_color_list(node.fields.get("ambientColor", [(0.2, 0.2, 0.2)])),
            diffuse_colors=self._ensure_color_list(node.fields.get("diffuseColor", [(0.8, 0.8, 0.8)])),
            specular_colors=self._ensure_color_list(node.fields.get("specularColor", [(0.0, 0.0, 0.0)])),
            emissive_colors=self._ensure_color_list(node.fields.get("emissiveColor", [(0.0, 0.0, 0.0)])),
            shininess=[float(value) for value in node.fields.get("shininess", [0.2])],
            transparency=[float(value) for value in node.fields.get("transparency", [0.0])],
            def_name=node.def_name,
        )
        if node.def_name:
            state.definitions[node.def_name] = material_state
        return material_state

    def _material_to_out_node(self, material: MaterialState) -> OutNode:
        """Collapse one MaterialState into a VRML 2.0 Material node."""

        diffuse = material.diffuse_colors[0]
        ambient = material.ambient_colors[0] if material.ambient_colors else (0.2, 0.2, 0.2)
        ambient_intensity = max(0.0, min(1.0, sum(ambient) / 3.0))
        fields: list[tuple[str, Any]] = []
        if abs(ambient_intensity - 0.2) > 1e-9:
            fields.append(("ambientIntensity", ambient_intensity))
        if diffuse != (0.8, 0.8, 0.8):
            fields.append(("diffuseColor", diffuse))
        if material.specular_colors and material.specular_colors[0] != (0.0, 0.0, 0.0):
            fields.append(("specularColor", material.specular_colors[0]))
        if material.emissive_colors and material.emissive_colors[0] != (0.0, 0.0, 0.0):
            fields.append(("emissiveColor", material.emissive_colors[0]))
        if material.shininess and abs(material.shininess[0] - 0.2) > 1e-9:
            fields.append(("shininess", material.shininess[0]))
        if material.transparency and abs(material.transparency[0] - 0.0) > 1e-9:
            fields.append(("transparency", material.transparency[0]))
        return OutNode("Material", fields=fields, def_name=material.def_name)

    def _material_reference_to_output(
        self,
        material_ref: MaterialState | UseRef | DefinitionRef | None,
        material: MaterialState | None,
    ) -> OutNode | UseRef:
        """Emit a shared Material definition once and return `USE` for later references."""

        if material is None:
            if "_DefMat" in self.emitted_defs:
                return UseRef("_DefMat")
            self.emitted_defs.add("_DefMat")
            return OutNode("Material", fields=[], def_name="_DefMat")
        if isinstance(material_ref, DefinitionRef):
            if material_ref.name in self.emitted_defs:
                return UseRef(material_ref.name)
            material_node = self._material_to_out_node(material)
            material_node.def_name = material_ref.name
            self.emitted_defs.add(material_ref.name)
            return material_node
        if isinstance(material_ref, UseRef):
            return UseRef(material_ref.name)
        signature = self._material_signature(material)
        if signature in self.generated_material_names:
            return UseRef(self.generated_material_names[signature])
        generated_name = f"_v2%{self.generated_material_counter}"
        self.generated_material_counter += 1
        self.generated_material_names[signature] = generated_name
        material_node = self._material_to_out_node(material)
        material_node.def_name = generated_name
        self.emitted_defs.add(generated_name)
        return material_node

    def _material_signature(self, material: MaterialState) -> tuple[Any, ...]:
        """Create a stable signature for implicit material reuse."""

        return (
            tuple(material.ambient_colors),
            tuple(material.diffuse_colors),
            tuple(material.specular_colors),
            tuple(material.emissive_colors),
            tuple(material.shininess),
            tuple(material.transparency),
        )

    def _simple_out_node(self, node: AstNode, new_type: str) -> OutNode:
        """Copy the source fields into a differently named output node."""

        return OutNode(new_type, fields=list(node.fields.items()), def_name=node.def_name)

    def _convert_texture(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 Texture2 node into ImageTexture or PixelTexture."""

        if "filename" in node.fields:
            fields: list[tuple[str, Any]] = [("url", self._to_string_list(node.fields["filename"]))]
            if str(node.fields.get("wrapS", "REPEAT")).upper() == "CLAMP":
                fields.append(("repeatS", False))
            if str(node.fields.get("wrapT", "REPEAT")).upper() == "CLAMP":
                fields.append(("repeatT", False))
            return OutNode("ImageTexture", fields=fields, def_name=node.def_name)
        if "image" in node.fields:
            fields = [("image", list(node.fields["image"]))]
            return OutNode("PixelTexture", fields=fields, def_name=node.def_name)
        return OutNode("ImageTexture", fields=[("url", [])], def_name=node.def_name)

    def _convert_texture_transform(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 texture transform node into TextureTransform."""

        ordered_fields = [
            ("translation", node.fields.get("translation")),
            ("rotation", node.fields.get("rotation")),
            ("scale", node.fields.get("scaleFactor")),
            ("center", node.fields.get("center")),
        ]
        return OutNode(
            "TextureTransform",
            fields=[(name, value) for name, value in ordered_fields if value is not None],
            def_name=node.def_name,
        )

    def _convert_font_style(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 FontStyle helper node into VRML 2.0 FontStyle."""

        fields: list[tuple[str, Any]] = []
        if "size" in node.fields:
            fields.append(("size", float(node.fields["size"])))
        if "family" in node.fields:
            family = str(node.fields["family"]).upper()
            mapped = {"SERIF": "SERIF", "TYPEWRITER": "TYPEWRITER"}.get(family, "SANS")
            fields.append(("family", [mapped]))
        if "style" in node.fields:
            style = str(node.fields["style"]).upper()
            fields.append(("style", style.replace("_", " ")))
        return OutNode("FontStyle", fields=fields, def_name=node.def_name)

    def _merge_font_style(
        self,
        base_font_style: OutNode | UseRef | DefinitionRef | None,
        text_fields: dict[str, Any],
    ) -> OutNode | UseRef | DefinitionRef | None:
        """Merge inherited FontStyle state with AsciiText-specific hints."""

        if isinstance(base_font_style, UseRef):
            return base_font_style
        source_font_style = self._materialize_reference(base_font_style)
        if isinstance(source_font_style, UseRef):
            return source_font_style
        merged_fields: OrderedDict[str, Any] = OrderedDict()
        if isinstance(source_font_style, OutNode):
            for name, value in source_font_style.fields:
                merged_fields[name] = copy.deepcopy(value)
        if "spacing" in text_fields:
            merged_fields["spacing"] = float(text_fields["spacing"])
        if "justification" in text_fields:
            justification = str(text_fields["justification"]).upper()
            mapped = {"LEFT": "BEGIN", "CENTER": "MIDDLE", "RIGHT": "END"}.get(justification, "BEGIN")
            merged_fields["justify"] = [mapped]
        if not merged_fields:
            return None
        return OutNode("FontStyle", fields=list(merged_fields.items()))

    def _slice_coordinate_node(
        self,
        coordinate: OutNode | UseRef | DefinitionRef | None,
        fields: dict[str, Any],
    ) -> OutNode | UseRef | None:
        """Slice Coordinate data for PointSet startIndex and numPoints when possible."""

        if coordinate is None or isinstance(coordinate, UseRef):
            return copy.deepcopy(coordinate)
        source_coordinate = self._materialize_reference(coordinate)
        if isinstance(source_coordinate, UseRef):
            return source_coordinate
        point_values = None
        for field_name, value in source_coordinate.fields:
            if field_name == "point":
                point_values = list(value)
                break
        if point_values is None:
            return source_coordinate
        start_index = int(fields.get("startIndex", 0))
        num_points = int(fields.get("numPoints", len(point_values) - start_index))
        sliced = point_values[start_index : start_index + num_points]
        return OutNode("Coordinate", fields=[("point", sliced)])

    def _wrap_transforms(self, node: OutNode, transforms: list[TransformSpec]) -> OutNode:
        """Wrap an emitted node in nested Transform nodes for persistent state."""

        if not transforms:
            return node
        def_name = node.def_name
        node.def_name = None
        wrapped = node
        for transform in reversed(transforms):
            if transform.kind == "matrix":
                matrix_transform = self._matrix_to_transform_fields(transform.value)
                wrapped = OutNode("Transform", fields=matrix_transform + [("children", [wrapped])])
                continue
            field_name = {"translation": "translation", "rotation": "rotation", "scale": "scale"}[transform.kind]
            wrapped = OutNode("Transform", fields=[(field_name, transform.value), ("children", [wrapped])])
        wrapped.def_name = def_name
        return wrapped

    def _matrix_to_transform_fields(self, matrix: list[float]) -> list[tuple[str, Any]]:
        """Approximate a MatrixTransform with the subset VRML 2.0 Transform supports."""

        # This approximation preserves translation and axis scale for common affine cases.
        scale = (
            (matrix[0] ** 2 + matrix[1] ** 2 + matrix[2] ** 2) ** 0.5,
            (matrix[4] ** 2 + matrix[5] ** 2 + matrix[6] ** 2) ** 0.5,
            (matrix[8] ** 2 + matrix[9] ** 2 + matrix[10] ** 2) ** 0.5,
        )
        fields: list[tuple[str, Any]] = [("translation", (matrix[12], matrix[13], matrix[14]))]
        if scale != (1.0, 1.0, 1.0):
            fields.append(("scale", scale))
        return fields

    def _register_definition(self, node: AstNode, value: Any, state: ConversionState) -> Any:
        """Store converted definitions so later USE references can be resolved."""

        if node.def_name:
            definition_ref = DefinitionRef(node.def_name, value)
            state.definitions[node.def_name] = definition_ref
            return definition_ref
        return value

    def _store_emitted_definition(self, node: OutNode, state: ConversionState) -> OutNode:
        """Store emitted node definitions so later USE statements can reference them."""

        if node.def_name:
            state.definitions[node.def_name] = DefinitionRef(node.def_name, node)
        return node

    def _apply_use_reference(self, use_ref: UseRef, state: ConversionState) -> list[OutNode]:
        """Apply a USE reference either as inherited state or as an emitted child node."""

        resolved = state.definitions.get(use_ref.name)
        if resolved is None:
            LOGGER.warning("Skipping unknown USE reference %s", use_ref.name)
            return []
        if isinstance(resolved, DefinitionRef):
            value = resolved.value
        else:
            value = resolved
        if isinstance(value, MaterialState):
            state.material = DefinitionRef(use_ref.name, value)
            return []
        if isinstance(value, OutNode):
            if value.node_type == "Coordinate":
                state.coordinate = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "Normal":
                state.normal = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "TextureCoordinate":
                state.tex_coord = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type in {"ImageTexture", "PixelTexture"}:
                state.texture = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "TextureTransform":
                state.texture_transform = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "FontStyle":
                state.font_style = DefinitionRef(use_ref.name, value)
                return []
            return [UseRef(use_ref.name)]
        LOGGER.warning("Skipping unsupported USE reference %s", use_ref.name)
        return []

    def _resolve_material(
        self,
        material: MaterialState | UseRef | DefinitionRef | None,
        state: ConversionState,
    ) -> MaterialState | None:
        """Resolve material USE references into material data when available."""

        if material is None:
            return None
        if isinstance(material, DefinitionRef):
            if isinstance(material.value, MaterialState):
                return material.value
            return None
        if isinstance(material, UseRef):
            resolved = state.definitions.get(material.name)
            if isinstance(resolved, DefinitionRef) and isinstance(resolved.value, MaterialState):
                return resolved.value
            if isinstance(resolved, MaterialState):
                return resolved
            return None
        return material

    def _materialize_reference(self, value: Any) -> Any:
        """Emit a stored definition once and return `USE` on later references."""

        if isinstance(value, DefinitionRef):
            if value.name in self.emitted_defs:
                return UseRef(value.name)
            materialized = copy.deepcopy(value.value)
            if isinstance(materialized, (OutNode, MaterialState)):
                materialized.def_name = value.name
            self.emitted_defs.add(value.name)
            return materialized
        return copy.deepcopy(value)

    def _ensure_color_list(self, value: Any) -> list[tuple[float, float, float]]:
        """Normalize a color field to a list of RGB tuples."""

        if isinstance(value, list):
            return [tuple(map(float, item)) for item in value]
        return [tuple(map(float, value))]

    def _to_string_list(self, value: Any) -> list[str]:
        """Normalize a string-or-list field to a list of strings."""

        if isinstance(value, list):
            return [str(item) for item in value]
        if value is None:
            return []
        return [str(value)]


class VrmlWriter:
    """Serialize VRML 2.0 node trees into human-readable text."""

    def write(self, nodes: list[OutNode]) -> str:
        """Serialize the provided nodes into a full VRML 2.0 document."""

        LOGGER.info("Serializing VRML 2.0 output")
        blocks = [VRML2_HEADER, ""]
        blocks.extend(self._render_node(node, 0) for node in nodes)
        return "\n\n".join(blocks).rstrip() + "\n"

    def _render_node(self, node: OutNode | UseRef, indent: int) -> str:
        """Render one node reference or full node definition."""

        prefix = " " * indent
        if isinstance(node, UseRef):
            return f"{prefix}USE {node.name}"
        header = node.node_type
        if node.def_name:
            header = f"DEF {node.def_name} {header}"
        if not node.fields:
            return f"{prefix}{header} {{ }}"
        lines = [f"{prefix}{header} {{"]
        for field_name, value in node.fields:
            lines.extend(self._render_field(field_name, value, indent + 2))
        lines.append(f"{prefix}}}")
        return "\n".join(lines)

    def _render_field(self, field_name: str, value: Any, indent: int) -> list[str]:
        """Render one field assignment with appropriate multiline formatting."""

        prefix = " " * indent
        if isinstance(value, (OutNode, UseRef)):
            node_lines = self._render_node(value, indent + 2).splitlines()
            return [f"{prefix}{field_name}"] + node_lines
        if isinstance(value, list) and value and all(isinstance(item, (OutNode, UseRef)) for item in value):
            lines = [f"{prefix}{field_name} ["]
            for item in value:
                lines.append(self._render_node(item, indent + 2))
            lines.append(f"{prefix}]")
            return lines
        if isinstance(value, list):
            rendered = self._render_list(value, indent + 2)
            return [f"{prefix}{field_name} ["] + rendered + [f"{prefix}]"]
        return [f"{prefix}{field_name} {self._render_scalar(value)}"]

    def _render_list(self, values: list[Any], indent: int) -> list[str]:
        """Render one scalar or vector list body."""

        prefix = " " * indent
        lines: list[str] = []
        for value in values:
            if isinstance(value, tuple):
                lines.append(f"{prefix}{' '.join(self._format_number(number) for number in value)}")
            else:
                lines.append(f"{prefix}{self._render_scalar(value)}")
        return lines

    def _render_scalar(self, value: Any) -> str:
        """Render one scalar or fixed-size tuple value."""

        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, str):
            return f'"{value}"'
        if isinstance(value, tuple):
            return " ".join(self._format_number(number) for number in value)
        if isinstance(value, float):
            return self._format_number(value)
        return str(value)

    def _format_number(self, value: float) -> str:
        """Format one number without noisy trailing zeros."""

        return f"{value:.9g}"


def convert_vrml1_text(text: str) -> str:
    """Convert VRML 1.0 source text into VRML 2.0 source text."""

    parser = VrmlParser(text)
    ast = parser.parse()
    converter = VrmlConverter()
    out_nodes = converter.convert(ast)
    writer = VrmlWriter()
    return writer.write(out_nodes)


def configure_logging(verbose: bool) -> None:
    """Configure the process-wide logger used by the converter."""

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the Linux-native converter."""

    parser = argparse.ArgumentParser(
        description="Convert VRML 1.0 scene files into VRML 2.0 text on Linux.",
    )
    parser.add_argument("input", help="Path to the VRML 1.0 input file")
    parser.add_argument("output", nargs="?", help="Optional output file path; defaults to stdout")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""

    args = parse_args(argv)
    configure_logging(args.verbose)
    input_path = Path(args.input)
    if not input_path.is_file():
        LOGGER.error("Invalid input file name specified: %s", input_path)
        return 1
    LOGGER.info("Reading input file %s", input_path)
    vrml2_text = convert_vrml1_text(input_path.read_text(encoding="utf-8"))
    if args.output:
        output_path = Path(args.output)
        LOGGER.info("Writing output file %s", output_path)
        output_path.write_text(vrml2_text, encoding="utf-8")
    else:
        sys.stdout.write(vrml2_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
