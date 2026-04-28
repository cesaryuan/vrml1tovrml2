"""VRML 2.0 text serialization."""

from __future__ import annotations

import io
from typing import Any, TextIO

from .common import LOGGER, OutNode, SpoolSequence, UseRef
from .common import VRML2_HEADER


class VrmlWriter:
    """Serialize VRML 2.0 node trees into human-readable text."""

    def write_to_stream(self, nodes: list[OutNode], stream: TextIO) -> None:
        """Serialize the provided nodes directly to a text stream."""

        LOGGER.info("Serializing VRML 2.0 output")
        stream.write(VRML2_HEADER)
        stream.write("\n\n")
        for index, node in enumerate(nodes):
            if index:
                stream.write("\n\n")
            stream.write(self._render_node(node, 0))
        stream.write("\n")

    def write(self, nodes: list[OutNode]) -> str:
        """Serialize the provided nodes into a full VRML 2.0 document."""

        output = io.StringIO()
        self.write_to_stream(nodes, output)
        return output.getvalue()

    def _render_node(self, node: OutNode | UseRef, indent: int) -> str:
        """Render one node reference or full node definition."""

        prefix = self._indent(indent)
        if isinstance(node, UseRef):
            return f"{prefix}USE {node.name}"
        header = node.node_type
        if node.def_name:
            header = f"DEF {node.def_name} {header}"
        if not node.fields:
            return f"{prefix}{header} {{\n{prefix}}}"
        lines = [f"{prefix}{header} {{"]
        for field_name, value in node.fields:
            lines.extend(self._render_field(field_name, value, indent + 2))
        lines.append(f"{prefix}}}")
        return "\n".join(lines)

    def _render_field(self, field_name: str, value: Any, indent: int) -> list[str]:
        """Render one field assignment with appropriate multiline formatting."""

        prefix = self._indent(indent)
        if isinstance(value, (OutNode, UseRef)):
            node_lines = self._render_node(value, indent + 2).splitlines()
            return [f"{prefix}{field_name}"] + node_lines
        if isinstance(value, list) and value and all(isinstance(item, (OutNode, UseRef)) for item in value):
            lines = [f"{prefix}{field_name} ["]
            for index, item in enumerate(value):
                rendered_item = self._render_node(item, indent + 2).splitlines()
                if index < len(value) - 1:
                    rendered_item[-1] = f"{rendered_item[-1]},"
                lines.extend(rendered_item)
            lines.append(f"{prefix}]")
            return lines
        if isinstance(value, SpoolSequence):
            rendered = self._render_spool_sequence(value, indent + 2)
            return [f"{prefix}{field_name} ["] + rendered + [f"{prefix}]"]
        if isinstance(value, list):
            rendered = self._render_list(value, indent + 2)
            return [f"{prefix}{field_name} ["] + rendered + [f"{prefix}]"]
        return [f"{prefix}{field_name} {self._render_scalar(value)}"]

    def _render_list(self, values: list[Any], indent: int) -> list[str]:
        """Render one scalar or vector list body."""

        prefix = self._indent(indent)
        lines: list[str] = []
        for index, value in enumerate(values):
            if isinstance(value, tuple):
                line = f"{prefix}{' '.join(self._format_number(number) for number in value)}"
            else:
                line = f"{prefix}{self._render_scalar(value)}"
            if index < len(values) - 1:
                line = f"{line},"
            lines.append(line)
        return lines

    def _render_spool_sequence(self, values: SpoolSequence, indent: int) -> list[str]:
        """Render a spool-backed large sequence without materializing it into a list."""

        prefix = self._indent(indent)
        lines: list[str] = []
        last_index = len(values) - 1
        for index, value in enumerate(values):
            if isinstance(value, tuple):
                line = f"{prefix}{' '.join(self._format_number(number) for number in value)}"
            else:
                line = f"{prefix}{self._render_scalar(value)}"
            if index < last_index:
                line = f"{line},"
            lines.append(line)
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

    def _indent(self, indent: int) -> str:
        """Translate the logical indent step count into tabs like the original tool."""

        return "\t" * (indent // 2)
