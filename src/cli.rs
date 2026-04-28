//! Command-line interface for the Rust VRML converter.

use std::fs::File;
use std::io::{self, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

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
    let progress_bar = create_progress_bar(&args.input, file_size, args.progress)?;
    let reader = BufReader::new(input_file);
    let parse_result = if let Some(progress) = &progress_bar {
        parser::parse_vrml_reader(progress.wrap_read(reader))
    } else {
        parser::parse_vrml_reader(reader)
    };
    if let Some(progress) = &progress_bar {
        progress.finish_and_clear();
    }
    let statements = parse_result?;
    let nodes = converter::convert(&statements)?;

    if let Some(output_path) = args.output {
        if args.verbose {
            eprintln!("INFO vrml1tovrml2: Writing output file {}", output_path.display());
        }
        write_output_file(&output_path, &nodes)?;
    } else {
        write_stdout(&nodes)?;
    }

    Ok(())
}

/// Return the CLI usage text.
fn usage() -> &'static str {
    "Usage: vrml1tovrml2 [--verbose] [--progress] <input> [output]"
}

/// Write output text to a file path.
fn write_output_file(path: &Path, nodes: &[crate::model::OutNode]) -> Result<(), VrmlError> {
    let file = File::create(path)?;
    let mut writer = BufWriter::new(file);
    VrmlWriter::write_to(nodes, &mut writer)?;
    writer.flush()?;
    Ok(())
}

/// Write output text to stdout.
fn write_stdout(nodes: &[crate::model::OutNode]) -> Result<(), VrmlError> {
    let mut stdout = io::stdout().lock();
    VrmlWriter::write_to(nodes, &mut stdout)?;
    stdout.flush()?;
    Ok(())
}

/// Create a byte-oriented progress bar when the flag is enabled.
fn create_progress_bar(
    path: &Path,
    file_size: u64,
    enabled: bool,
) -> Result<Option<ProgressBar>, VrmlError> {
    if !enabled {
        return Ok(None);
    }

    let style = ProgressStyle::with_template(
        "{msg} [{wide_bar}] {bytes:>8}/{total_bytes:>8} ({percent:>3}%)",
    )
    .map_err(|error| VrmlError::from(format!("Invalid progress style: {error}")))?;

    let progress_bar = ProgressBar::new(file_size);
    progress_bar.set_style(style);
    progress_bar.set_message(path.display().to_string());
    Ok(Some(progress_bar))
}
