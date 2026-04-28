//! Command-line interface for the Rust VRML converter.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

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
    if args.progress {
        eprintln!(
            "INFO vrml1tovrml2: Progress reporting is not implemented yet in the Rust-native path"
        );
    }

    let input_text = fs::read_to_string(&args.input)?;
    let statements = parser::parse_vrml(&input_text)?;
    let nodes = converter::convert(&statements)?;
    let output_text = VrmlWriter::write(&nodes);

    if let Some(output_path) = args.output {
        if args.verbose {
            eprintln!("INFO vrml1tovrml2: Writing output file {}", output_path.display());
        }
        write_output_file(&output_path, &output_text)?;
    } else {
        write_stdout(&output_text)?;
    }

    Ok(())
}

/// Return the CLI usage text.
fn usage() -> &'static str {
    "Usage: vrml1tovrml2 [--verbose] [--progress] <input> [output]"
}

/// Write output text to a file path.
fn write_output_file(path: &Path, content: &str) -> Result<(), VrmlError> {
    fs::write(path, content)?;
    Ok(())
}

/// Write output text to stdout.
fn write_stdout(content: &str) -> Result<(), VrmlError> {
    let mut stdout = io::stdout().lock();
    stdout.write_all(content.as_bytes())?;
    Ok(())
}
