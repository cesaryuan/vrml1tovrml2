//! Command-line interface for the Rust VRML converter.

use std::fs::File;
use std::io::{self, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use indicatif::{ProgressBar, ProgressStyle};

use crate::converter;
use crate::error::VrmlError;
use crate::parser;
use crate::writer::VrmlWriter;

/// Store parsed command-line arguments.
pub struct CliArgs {
    /// Path to the VRML 1.0 input file.
    pub input: PathBuf,
    /// Optional path for the VRML 2.0 output file.
    pub output: Option<PathBuf>,
    /// Whether verbose mode was requested.
    pub verbose: bool,
    /// Whether progress output was requested.
    pub progress: bool,
}

/// Parse CLI arguments into a typed structure.
pub fn parse_args(argv: impl IntoIterator<Item = String>) -> Result<CliArgs, VrmlError> {
    let mut args = argv.into_iter();
    let _program = args.next();

    let mut verbose = false;
    let mut progress = false;
    let mut positional = Vec::new();

    for arg in args {
        match arg.as_str() {
            "--verbose" => verbose = true,
            "--progress" => progress = true,
            "--help" | "-h" => return Err(VrmlError::from(usage())),
            _ if arg.starts_with('-') => {
                return Err(VrmlError::from(format!("Unknown option: {arg}\n\n{}", usage())))
            }
            _ => positional.push(arg),
        }
    }

    if positional.is_empty() {
        return Err(VrmlError::from(usage()));
    }

    let input = PathBuf::from(&positional[0]);
    let output = positional.get(1).map(PathBuf::from);
    Ok(CliArgs {
        input,
        output,
        verbose,
        progress,
    })
}

/// Run the full Rust CLI conversion flow.
pub fn run(args: CliArgs) -> Result<(), VrmlError> {
    let total_started_at = Instant::now();
    if !args.input.is_file() {
        return Err(VrmlError::from(format!(
            "Invalid input file name specified: {}",
            args.input.display()
        )));
    }

    if args.verbose {
        eprintln!("INFO vrml1tovrml2: Reading input file {}", args.input.display());
    }

    let input_file = File::open(&args.input)?;
    let file_size = input_file.metadata()?.len();
    let read_progress = create_read_progress_bar(&args.input, file_size, args.progress)?;
    let reader = BufReader::new(input_file);
    let read_started_at = Instant::now();
    let parse_result = if let Some(progress) = &read_progress {
        parser::parse_vrml_reader(progress.wrap_read(reader))
    } else {
        parser::parse_vrml_reader(reader)
    };
    if let Some(progress) = &read_progress {
        progress.finish();
    }
    let statements = parse_result?;
    let read_elapsed = read_started_at.elapsed();

    let convert_progress = create_count_progress_bar(
        "Converting",
        statement_count(&statements) as u64,
        args.progress,
    )?;
    let convert_started_at = Instant::now();
    let mut on_convert_progress = || {
        if let Some(progress) = &convert_progress {
            progress.inc(1);
        }
    };
    let convert_result = if args.progress {
        converter::convert_with_progress(&statements, &mut on_convert_progress)
    } else {
        converter::convert(&statements)
    };
    if let Some(progress) = &convert_progress {
        progress.finish();
    }
    let nodes = convert_result?;
    let convert_elapsed = convert_started_at.elapsed();

    let write_started_at = Instant::now();
    if let Some(output_path) = args.output {
        if args.verbose {
            eprintln!("INFO vrml1tovrml2: Writing output file {}", output_path.display());
        }
        write_output_file(&output_path, &nodes, args.progress)?;
    } else {
        write_stdout(&nodes, args.progress)?;
    }
    let write_elapsed = write_started_at.elapsed();
    let total_elapsed = total_started_at.elapsed();

    print_timing_summary(read_elapsed, convert_elapsed, write_elapsed, total_elapsed);

    Ok(())
}

/// Return the CLI usage text.
fn usage() -> &'static str {
    "Usage: vrml1tovrml2 [--verbose] [--progress] <input> [output]"
}

/// Write output text to a file path.
fn write_output_file(
    path: &Path,
    nodes: &[crate::model::OutNode],
    progress_enabled: bool,
) -> Result<(), VrmlError> {
    let file = File::create(path)?;
    let mut writer = BufWriter::new(file);
    let write_progress = create_count_progress_bar(
        "Writing",
        crate::writer::VrmlWriter::count_nodes(nodes) as u64,
        progress_enabled,
    )?;
    let mut on_write_progress = || {
        if let Some(progress) = &write_progress {
            progress.inc(1);
        }
    };
    VrmlWriter::write_to_with_progress(
        nodes,
        &mut writer,
        if progress_enabled {
            Some(&mut on_write_progress)
        } else {
            None
        },
    )?;
    writer.flush()?;
    if let Some(progress) = &write_progress {
        progress.finish();
    }
    Ok(())
}

/// Write output text to stdout.
fn write_stdout(
    nodes: &[crate::model::OutNode],
    progress_enabled: bool,
) -> Result<(), VrmlError> {
    let mut stdout = io::stdout().lock();
    let write_progress = create_count_progress_bar(
        "Writing",
        crate::writer::VrmlWriter::count_nodes(nodes) as u64,
        progress_enabled,
    )?;
    let mut on_write_progress = || {
        if let Some(progress) = &write_progress {
            progress.inc(1);
        }
    };
    VrmlWriter::write_to_with_progress(
        nodes,
        &mut stdout,
        if progress_enabled {
            Some(&mut on_write_progress)
        } else {
            None
        },
    )?;
    stdout.flush()?;
    if let Some(progress) = &write_progress {
        progress.finish();
    }
    Ok(())
}

/// Create a byte-oriented progress bar for the input read phase.
fn create_read_progress_bar(
    path: &Path,
    file_size: u64,
    enabled: bool,
) -> Result<Option<ProgressBar>, VrmlError> {
    if !enabled {
        return Ok(None);
    }

    let style = ProgressStyle::with_template(
        "{msg:<12} [{wide_bar}] {bytes:>8}/{total_bytes:>8} ({percent:>3}%)",
    )
    .map_err(|error| VrmlError::from(format!("Invalid progress style: {error}")))?;

    let progress_bar = ProgressBar::new(file_size);
    progress_bar.set_style(style);
    progress_bar.set_message(format!("Reading {}", path.display()));
    Ok(Some(progress_bar))
}

/// Create a unit-count progress bar for conversion or writing phases.
fn create_count_progress_bar(
    label: &str,
    total: u64,
    enabled: bool,
) -> Result<Option<ProgressBar>, VrmlError> {
    if !enabled {
        return Ok(None);
    }

    let safe_total = total.max(1);
    let style = ProgressStyle::with_template(
        "{msg:<12} [{wide_bar}] {pos:>6}/{len:<6} ({percent:>3}%)",
    )
    .map_err(|error| VrmlError::from(format!("Invalid progress style: {error}")))?;

    let progress_bar = ProgressBar::new(safe_total);
    progress_bar.set_style(style);
    progress_bar.set_message(label.to_owned());
    Ok(Some(progress_bar))
}

/// Count statements recursively so conversion progress reflects nested work too.
fn statement_count(statements: &[crate::model::Statement]) -> usize {
    statements.iter().map(count_statement).sum()
}

/// Count one statement and all nested child statements.
fn count_statement(statement: &crate::model::Statement) -> usize {
    match statement {
        crate::model::Statement::Use(_) => 1,
        crate::model::Statement::Node(node) => {
            1 + node.children.iter().map(count_statement).sum::<usize>()
        }
    }
}

/// Print one end-of-run timing summary for the major pipeline stages.
fn print_timing_summary(
    read_elapsed: Duration,
    convert_elapsed: Duration,
    write_elapsed: Duration,
    total_elapsed: Duration,
) {
    eprintln!(
        "Timing: reading/parsing={} converting={} writing={} total={}",
        format_duration(read_elapsed),
        format_duration(convert_elapsed),
        format_duration(write_elapsed),
        format_duration(total_elapsed),
    );
}

/// Format a duration for compact human-readable timing output.
fn format_duration(duration: Duration) -> String {
    if duration.as_secs() >= 1 {
        format!("{:.3}s", duration.as_secs_f64())
    } else if duration.as_millis() >= 1 {
        format!("{}ms", duration.as_millis())
    } else {
        format!("{}us", duration.as_micros())
    }
}
