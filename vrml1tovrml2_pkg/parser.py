"""Streaming reader, tokenizer, and parser for VRML 1.0."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterator, TextIO

from .common import (
    AstNode,
    LOGGER,
    SPOOL_TARGET_FIELDS,
    SpoolSequenceBuilder,
    Token,
    UseRef,
    VrmlError,
)
from .specs import NODE_FIELD_SPECS


class CharReader:
    """Read characters from a text stream incrementally with small lookahead."""

    def __init__(self, stream: TextIO, chunk_size: int = 65536, progress: Any | None = None) -> None:
        """Wrap a text stream that will be consumed incrementally."""

        self.stream = stream
        self.chunk_size = chunk_size
        self.progress = progress
        self.buffer = ""
        self.index = 0
        self.eof = False
        self.line = 1
        self.column = 1

    def peek(self, offset: int = 0) -> str | None:
        """Return the character at the current position plus offset."""

        self._ensure(offset + 1)
        position = self.index + offset
        if position >= len(self.buffer):
            return None
        return self.buffer[position]

    def advance(self) -> str | None:
        """Consume one character and update line and column counters."""

        self._ensure(1)
        if self.index >= len(self.buffer):
            return None
        char = self.buffer[self.index]
        self.index += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        self._trim()
        return char

    def skip_line(self) -> None:
        """Consume characters until the current line ends."""

        while True:
            char = self.peek()
            if char is None or char == "\n":
                return
            self.advance()

    def _ensure(self, needed: int) -> None:
        """Ensure at least `needed` characters are buffered from the current index."""

        while not self.eof and len(self.buffer) - self.index < needed:
            chunk = self.stream.read(self.chunk_size)
            if chunk == "":
                self.eof = True
                break
            if self.progress is not None:
                self.progress.update(len(chunk.encode("utf-8")))
            if self.index:
                self.buffer = self.buffer[self.index :] + chunk
                self.index = 0
            else:
                self.buffer += chunk

    def _trim(self) -> None:
        """Drop already-consumed prefix data so buffers do not grow unbounded."""

        if self.index >= 32768:
            self.buffer = self.buffer[self.index :]
            self.index = 0


class VrmlTokenizer:
    """Turn VRML 1.0 text into a stream of reusable tokens."""

    def __init__(self, reader: CharReader) -> None:
        """Store the character reader that will be tokenized."""

        self.reader = reader

    def tokenize(self) -> Iterator[Token]:
        """Yield tokens lazily while skipping whitespace and comments."""

        while True:
            char = self.reader.peek()
            if char is None:
                return
            if char == "\n":
                self.reader.advance()
                continue
            if char in " \t\r,":
                self.reader.advance()
                continue
            if char == "#":
                self.reader.skip_line()
                continue
            line = self.reader.line
            column = self.reader.column
            if char in "{}[]":
                self.reader.advance()
                yield Token("symbol", char, line, column)
                continue
            if char == '"':
                yield self._read_string(line, column)
                continue
            if char.isdigit() or char in "+-.":
                number_token = self._maybe_read_number(line, column)
                if number_token is not None:
                    yield number_token
                    continue
            yield self._read_identifier(line, column)

    def _read_string(self, line: int, column: int) -> Token:
        """Read one quoted VRML string token."""

        start_column = column
        self.reader.advance()
        buffer: list[str] = []
        while True:
            char = self.reader.peek()
            if char is None:
                raise VrmlError(f"Unterminated string at line {line}, column {start_column}")
            if char == "\\":
                self.reader.advance()
                escaped = self.reader.peek()
                if escaped is None:
                    raise VrmlError(f"Unterminated string at line {line}, column {start_column}")
                escape_table = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
                buffer.append(escape_table.get(escaped, escaped))
                self.reader.advance()
                continue
            if char == '"':
                self.reader.advance()
                return Token("string", "".join(buffer), line, start_column)
            buffer.append(char)
            self.reader.advance()

    def _maybe_read_number(self, line: int, column: int) -> Token | None:
        """Read a numeric token when the current bytes match a VRML number."""

        value_chars: list[str] = []
        if self.reader.peek() in "+-":
            value_chars.append(self.reader.advance() or "")
        has_digit = False
        while True:
            char = self.reader.peek()
            if char is None or not char.isdigit():
                break
            has_digit = True
            value_chars.append(self.reader.advance() or "")
        if self.reader.peek() == ".":
            value_chars.append(self.reader.advance() or "")
            while True:
                char = self.reader.peek()
                if char is None or not char.isdigit():
                    break
                has_digit = True
                value_chars.append(self.reader.advance() or "")
        if not has_digit:
            for char in reversed(value_chars):
                self.reader.index -= 1
                if char == "\n":
                    self.reader.line -= 1
            self.reader.column -= len(value_chars)
            return None
        if self.reader.peek() in {"e", "E"}:
            exponent_chars = [self.reader.advance() or ""]
            if self.reader.peek() in {"+", "-"}:
                exponent_chars.append(self.reader.advance() or "")
            exp_has_digit = False
            while True:
                char = self.reader.peek()
                if char is None or not char.isdigit():
                    break
                exp_has_digit = True
                exponent_chars.append(self.reader.advance() or "")
            if exp_has_digit:
                value_chars.extend(exponent_chars)
            else:
                for _ in exponent_chars:
                    self.reader.index -= 1
                self.reader.column -= len(exponent_chars)
        return Token("number", "".join(value_chars), line, column)

    def _read_identifier(self, line: int, column: int) -> Token:
        """Read one identifier-like token until the next delimiter."""

        value_chars: list[str] = []
        while True:
            char = self.reader.peek()
            if char is None or char in " \t\r\n,{}[]\"#":
                break
            value_chars.append(self.reader.advance() or "")
        return Token("identifier", "".join(value_chars), line, column)


class TokenBuffer:
    """Provide small lookahead over a streaming token iterator."""

    def __init__(self, tokens: Iterator[Token]) -> None:
        """Store the streaming token iterator and an empty lookahead buffer."""

        self.tokens = tokens
        self.buffer: list[Token] = []
        self.exhausted = False

    def peek(self, offset: int = 0) -> Token:
        """Return the token at the current position plus offset."""

        self._ensure(offset + 1)
        if offset >= len(self.buffer):
            return Token("eof", "", -1, -1)
        return self.buffer[offset]

    def advance(self) -> Token:
        """Consume and return the current token."""

        self._ensure(1)
        if not self.buffer:
            return Token("eof", "", -1, -1)
        return self.buffer.pop(0)

    def at_end(self) -> bool:
        """Return whether there are no more tokens available."""

        self._ensure(1)
        return not self.buffer

    def _ensure(self, count: int) -> None:
        """Fill the lookahead buffer up to the requested size when possible."""

        while not self.exhausted and len(self.buffer) < count:
            try:
                self.buffer.append(next(self.tokens))
            except StopIteration:
                self.exhausted = True
                break


def validate_vrml1_header(reader: CharReader, header: str) -> None:
    """Consume leading whitespace and verify the VRML 1.0 header."""

    while reader.peek() is not None and (reader.peek() or "").isspace():
        reader.advance()
    for expected in header:
        char = reader.advance()
        if char != expected:
            raise VrmlError("File does not have a valid VRML 1.0 header string")


class VrmlParser:
    """Parse VRML 1.0 tokens into an abstract syntax tree."""

    def __init__(self, token_buffer: TokenBuffer) -> None:
        """Store the streaming token buffer used by the parser."""

        self.tokens = token_buffer

    def parse(self) -> list[Any]:
        """Parse the full source file into a list of root statements."""

        LOGGER.info("Parsing VRML 1.0 source")
        statements: list[Any] = []
        while not self._at_end():
            statements.append(self._parse_statement())
        LOGGER.info("Parsed %d top-level statements", len(statements))
        return statements

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

        spool_large_field = (node_type, field_name) in SPOOL_TARGET_FIELDS
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
            return self._parse_multi_numeric_values(1, cast=int, spool=spool_large_field, scalar_type="int")
        if field_kind == "mffloat":
            return self._parse_multi_numeric_values(1, cast=float, spool=spool_large_field, scalar_type="float")
        if field_kind == "mfvec2":
            return self._parse_multi_numeric_values(2, cast=float, spool=spool_large_field, scalar_type="float")
        if field_kind == "mfvec3":
            return self._parse_multi_numeric_values(3, cast=float, spool=spool_large_field, scalar_type="float")
        if field_kind == "mfcolor":
            return self._parse_multi_numeric_values(3, cast=float, spool=spool_large_field, scalar_type="float")
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

    def _parse_multi_numeric_values(
        self,
        arity: int,
        cast: Any,
        spool: bool = False,
        scalar_type: str = "float",
    ) -> Any:
        """Parse a multi-value field either as one value or as a bracketed list."""

        if not self._check_symbol("["):
            single = self._parse_multi_value_item(arity, cast)
            return [single]
        values: list[Any] = []
        builder = SpoolSequenceBuilder(arity, scalar_type) if spool else None
        self._consume_symbol("[", "Expected '['")
        while not self._check_symbol("]") and not self._at_end():
            item = self._parse_multi_value_item(arity, cast)
            if builder is not None:
                builder.append(item)
            else:
                values.append(item)
        self._consume_symbol("]", "Expected ']'")
        if builder is not None:
            return builder.finalize()
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

        next_token = self.tokens.peek(1)
        return next_token.kind == "symbol" and next_token.value == value

    def _advance(self) -> Token:
        """Consume and return the current token."""

        return self.tokens.advance()

    def _peek(self) -> Token:
        """Return the current token or a synthetic EOF token."""

        return self.tokens.peek(0)

    def _at_end(self) -> bool:
        """Return whether all tokens have already been consumed."""

        return self.tokens.at_end()
