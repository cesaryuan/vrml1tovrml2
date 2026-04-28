//! VRML 2.0 text serialization for Rust output nodes.

use std::io::{self, Write};

use crate::model::{OutNode, Value};

const VRML2_HEADER: &str = "#VRML V2.0 utf8";

/// Serialize VRML 2.0 output nodes into textual `.wrl` content.
pub struct VrmlWriter;

impl VrmlWriter {
    /// Write a full VRML 2.0 document directly to a byte stream.
    pub fn write_to<W: Write>(nodes: &[OutNode], writer: &mut W) -> io::Result<()> {
        writer.write_all(VRML2_HEADER.as_bytes())?;
        writer.write_all(b"\n\n")?;

        for (index, node) in nodes.iter().enumerate() {
            if index > 0 {
                writer.write_all(b"\n\n")?;
            }
            writer.write_all(Self::render_node(node, 0).as_bytes())?;
        }

        writer.write_all(b"\n")?;
        Ok(())
    }

    /// Render a full VRML 2.0 document from output nodes.
    #[allow(dead_code)]
    pub fn write(nodes: &[OutNode]) -> String {
        let mut output = Vec::new();
        Self::write_to(nodes, &mut output).expect("writing to Vec<u8> cannot fail");
        String::from_utf8(output).expect("writer only emits valid UTF-8")
    }

    /// Render one node with indentation that matches the Python writer.
    fn render_node(node: &OutNode, indent: usize) -> String {
        let prefix = Self::indent(indent);
        let mut header = node.node_type.clone();
        if let Some(def_name) = &node.def_name {
            header = format!("DEF {def_name} {header}");
        }

        if node.fields.is_empty() {
            return format!("{prefix}{header} {{\n{prefix}}}");
        }

        let mut lines = vec![format!("{prefix}{header} {{")];
        for (field_name, value) in &node.fields {
            lines.extend(Self::render_field(field_name, value, indent + 2));
        }
        lines.push(format!("{prefix}}}"));
        lines.join("\n")
    }

    /// Render one field assignment and its nested structure when needed.
    fn render_field(field_name: &str, value: &Value, indent: usize) -> Vec<String> {
        let prefix = Self::indent(indent);
        match value {
            Value::Node(node) => {
                let node_lines = Self::render_node(node, indent + 2)
                    .lines()
                    .map(ToOwned::to_owned)
                    .collect::<Vec<_>>();
                let mut lines = vec![format!("{prefix}{field_name}")];
                lines.extend(node_lines);
                lines
            }
            Value::Use(use_ref) => {
                let mut lines = vec![format!("{prefix}{field_name}")];
                lines.push(format!("{}USE {}", Self::indent(indent + 2), use_ref.name));
                lines
            }
            Value::List(values) if values.iter().all(Self::is_node_like) => {
                let mut lines = vec![format!("{prefix}{field_name} [")];
                for (index, item) in values.iter().enumerate() {
                    let mut rendered_item = Self::render_node_like(item, indent + 2);
                    if index + 1 < values.len() {
                        if let Some(last) = rendered_item.last_mut() {
                            last.push(',');
                        }
                    }
                    lines.extend(rendered_item);
                }
                lines.push(format!("{prefix}]"));
                lines
            }
            Value::List(values) => {
                let mut lines = vec![format!("{prefix}{field_name} [")];
                for (index, item) in values.iter().enumerate() {
                    let mut line = format!("{}{}", Self::indent(indent + 2), Self::render_scalar(item));
                    if index + 1 < values.len() {
                        line.push(',');
                    }
                    lines.push(line);
                }
                lines.push(format!("{prefix}]"));
                lines
            }
            _ => vec![format!("{prefix}{field_name} {}", Self::render_scalar(value))],
        }
    }

    /// Render a node-like list item.
    fn render_node_like(value: &Value, indent: usize) -> Vec<String> {
        match value {
            Value::Node(node) => Self::render_node(node, indent)
                .lines()
                .map(ToOwned::to_owned)
                .collect(),
            Value::Use(use_ref) => vec![format!("{}USE {}", Self::indent(indent), use_ref.name)],
            _ => vec![format!("{}{}", Self::indent(indent), Self::render_scalar(value))],
        }
    }

    /// Render a scalar value or fixed-size vector.
    fn render_scalar(value: &Value) -> String {
        match value {
            Value::Bool(value) => {
                if *value {
                    "TRUE".to_owned()
                } else {
                    "FALSE".to_owned()
                }
            }
            Value::Int(value) => value.to_string(),
            Value::Float(value) => Self::format_number(*value),
            Value::String(value) => format!("\"{value}\""),
            Value::Identifier(value) => value.clone(),
            Value::Vec(values) => values
                .iter()
                .map(|value| Self::format_number(*value))
                .collect::<Vec<_>>()
                .join(" "),
            Value::List(_) => "[]".to_owned(),
            Value::Node(node) => Self::render_node(node, 0),
            Value::Use(use_ref) => format!("USE {}", use_ref.name),
        }
    }

    /// Format a float without noisy trailing zeros.
    fn format_number(value: f64) -> String {
        let text = format!("{value:.9}");
        let text = text.trim_end_matches('0').trim_end_matches('.');
        if text.is_empty() || text == "-0" {
            "0".to_owned()
        } else {
            text.to_owned()
        }
    }

    /// Return whether a value should render as a nested node block.
    fn is_node_like(value: &Value) -> bool {
        matches!(value, Value::Node(_) | Value::Use(_))
    }

    /// Create the tab indentation used by the existing writer.
    fn indent(indent: usize) -> String {
        "\t".repeat(indent / 2)
    }
}
