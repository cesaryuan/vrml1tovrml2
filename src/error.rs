//! Error types used by the Rust VRML converter.

use std::fmt::{Display, Formatter};
use std::io;

/// Represent a parse, conversion, or I/O failure in the Rust converter.
#[derive(Debug)]
pub enum VrmlError {
    /// Wrap filesystem and stream I/O failures.
    Io(io::Error),
    /// Describe malformed or unsupported VRML input.
    Message(String),
}

impl Display for VrmlError {
    /// Render the error as a human-readable message.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "{error}"),
            Self::Message(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for VrmlError {}

impl From<io::Error> for VrmlError {
    /// Convert a standard I/O error into the converter error type.
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<&str> for VrmlError {
    /// Convert a static message into the converter error type.
    fn from(value: &str) -> Self {
        Self::Message(value.to_owned())
    }
}

impl From<String> for VrmlError {
    /// Convert an owned message into the converter error type.
    fn from(value: String) -> Self {
        Self::Message(value)
    }
}
