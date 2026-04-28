//! Rust-native CLI entrypoint for VRML 1.0 to VRML 2.0 conversion.

mod cli;
mod converter;
mod error;
mod model;
mod parser;
mod writer;

use std::env;
use std::process::ExitCode;

/// Run the CLI and return the process exit code.
fn main() -> ExitCode {
    match cli::parse_args(env::args()) {
        Ok(args) => match cli::run(args) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("ERROR vrml1tovrml2: {error}");
                ExitCode::from(1)
            }
        },
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}
