//! Library entrypoint for the VRML 1.0 to VRML 2.0 converter.

#[cfg(not(target_arch = "wasm32"))]
pub mod cli;
pub mod converter;
pub mod error;
pub mod model;
pub mod parser;
pub mod writer;

use error::VrmlError;

/// Convert a full VRML 1.0 document string into VRML 2.0 output text.
pub fn convert_vrml_text(input: &str) -> Result<String, VrmlError> {
    let statements = parser::parse_vrml(input)?;
    let nodes = converter::convert(&statements)?;
    Ok(writer::VrmlWriter::write(&nodes))
}

#[cfg(target_arch = "wasm32")]
mod web {
    use wasm_bindgen::prelude::*;

    /// Convert VRML 1.0 text into VRML 2.0 text for browser callers.
    #[wasm_bindgen(js_name = convertVrmlText)]
    pub fn convert_vrml_text_for_web(input: &str) -> Result<String, JsValue> {
        crate::convert_vrml_text(input).map_err(|error| JsValue::from_str(&error.to_string()))
    }
}
