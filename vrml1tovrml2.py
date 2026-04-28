#!/usr/bin/env python3
"""Compatibility entrypoint for the modular vrml1tovrml2 implementation."""

from vrml1tovrml2_pkg import (
    CharReader,
    TokenBuffer,
    VrmlConverter,
    VrmlParser,
    VrmlTokenizer,
    VrmlWriter,
    convert_vrml1_stream,
    convert_vrml1_text,
    main,
    parse_args,
    validate_vrml1_header,
)

__all__ = [
    "CharReader",
    "TokenBuffer",
    "VrmlParser",
    "VrmlTokenizer",
    "VrmlConverter",
    "VrmlWriter",
    "convert_vrml1_stream",
    "convert_vrml1_text",
    "main",
    "parse_args",
    "validate_vrml1_header",
]


if __name__ == "__main__":
    raise SystemExit(main())
