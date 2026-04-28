"""CLI entrypoint and high-level orchestration."""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path
from typing import Iterable, TextIO

from .common import LOGGER, VRML1_HEADER
from .converter import VrmlConverter
from .parser import CharReader, TokenBuffer, VrmlParser, VrmlTokenizer, validate_vrml1_header
from .progress import create_byte_progress
from .writer import VrmlWriter


def convert_vrml1_stream(input_stream: TextIO, output_stream: TextIO, progress: object | None = None) -> None:
    """Convert VRML 1.0 source read from one stream into VRML 2.0 on another."""

    reader = CharReader(input_stream, progress=progress)
    validate_vrml1_header(reader, VRML1_HEADER)
    token_buffer = TokenBuffer(VrmlTokenizer(reader).tokenize())
    parser = VrmlParser(token_buffer)
    ast = parser.parse()
    converter = VrmlConverter()
    out_nodes = converter.convert(ast)
    writer = VrmlWriter()
    writer.write_to_stream(out_nodes, output_stream)


def convert_vrml1_text(text: str) -> str:
    """Convert VRML 1.0 source text into VRML 2.0 source text."""

    output = io.StringIO()
    convert_vrml1_stream(io.StringIO(text), output)
    return output.getvalue()


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
    parser.add_argument("--progress", action="store_true", help="Show input read progress when tqdm is available")
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
    progress = create_byte_progress(input_path, enabled=args.progress)
    try:
        if args.output:
            output_path = Path(args.output)
            LOGGER.info("Writing output file %s", output_path)
            with input_path.open("r", encoding="utf-8", errors="strict") as input_stream:
                with output_path.open("w", encoding="utf-8") as output_stream:
                    convert_vrml1_stream(input_stream, output_stream, progress=progress)
        else:
            with input_path.open("r", encoding="utf-8", errors="strict") as input_stream:
                convert_vrml1_stream(input_stream, sys.stdout, progress=progress)
    finally:
        progress.close()
    return 0
