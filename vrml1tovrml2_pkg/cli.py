"""CLI entrypoint and high-level orchestration."""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TextIO

from .common import LOGGER, VRML1_HEADER
from .converter import VrmlConverter
from .parser import CharReader, TokenBuffer, VrmlParser, VrmlTokenizer, validate_vrml1_header
from .progress import create_byte_progress
from .writer import VrmlWriter


@dataclass(slots=True)
class ConversionTimings:
    """Store elapsed times for the major conversion pipeline stages."""

    read_parse_seconds: float
    convert_seconds: float
    write_seconds: float
    total_seconds: float


def convert_vrml1_stream(input_stream: TextIO, output_stream: TextIO, progress: object | None = None) -> None:
    """Convert VRML 1.0 source read from one stream into VRML 2.0 on another."""

    convert_vrml1_stream_with_timings(input_stream, output_stream, progress=progress)


def convert_vrml1_stream_with_timings(
    input_stream: TextIO,
    output_stream: TextIO,
    progress: object | None = None,
) -> ConversionTimings:
    """Convert one VRML 1.0 stream and return per-stage elapsed timings."""

    total_started_at = time.perf_counter()
    read_parse_started_at = total_started_at
    reader = CharReader(input_stream, progress=progress)
    validate_vrml1_header(reader, VRML1_HEADER)
    token_buffer = TokenBuffer(VrmlTokenizer(reader).tokenize())
    parser = VrmlParser(token_buffer)
    ast = parser.parse()
    read_parse_seconds = time.perf_counter() - read_parse_started_at

    convert_started_at = time.perf_counter()
    converter = VrmlConverter()
    out_nodes = converter.convert(ast)
    convert_seconds = time.perf_counter() - convert_started_at

    write_started_at = time.perf_counter()
    writer = VrmlWriter()
    writer.write_to_stream(out_nodes, output_stream)
    write_seconds = time.perf_counter() - write_started_at
    total_seconds = time.perf_counter() - total_started_at
    return ConversionTimings(
        read_parse_seconds=read_parse_seconds,
        convert_seconds=convert_seconds,
        write_seconds=write_seconds,
        total_seconds=total_seconds,
    )


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
    timings: ConversionTimings | None = None
    try:
        if args.output:
            output_path = Path(args.output)
            LOGGER.info("Writing output file %s", output_path)
            with input_path.open("r", encoding="utf-8", errors="strict") as input_stream:
                with output_path.open("w", encoding="utf-8") as output_stream:
                    timings = convert_vrml1_stream_with_timings(input_stream, output_stream, progress=progress)
        else:
            with input_path.open("r", encoding="utf-8", errors="strict") as input_stream:
                timings = convert_vrml1_stream_with_timings(input_stream, sys.stdout, progress=progress)
    finally:
        progress.close()
    if timings is not None:
        print_timing_summary(timings)
    return 0


def print_timing_summary(timings: ConversionTimings) -> None:
    """Print one compact timing summary for the completed conversion run."""

    print(
        "Timing: "
        f"reading/parsing={format_duration(timings.read_parse_seconds)} "
        f"converting={format_duration(timings.convert_seconds)} "
        f"writing={format_duration(timings.write_seconds)} "
        f"total={format_duration(timings.total_seconds)}",
        file=sys.stderr,
    )


def format_duration(seconds: float) -> str:
    """Format a duration in a compact human-readable form."""

    if seconds >= 1.0:
        return f"{seconds:.3f}s"
    if seconds >= 0.001:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds * 1_000_000:.0f}us"
