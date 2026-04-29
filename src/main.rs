//! Rust-native CLI entrypoint for VRML 1.0 to VRML 2.0 conversion.

use std::env;
use std::process::ExitCode;

use vrml1tovrml2_rs::cli;

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
