//! VRML 2.0 text serialization for Rust output nodes.

use std::io::{self, Write};

use crate::model::{OutNode, Value};

const VRML2_HEADER: &str = "#VRML V2.0 utf8";
const COMPACT_COORD_INDEX_VALUES_PER_LINE: usize = 5;

/// Serialize VRML 2.0 output nodes into textual `.wrl` content.
pub struct VrmlWriter;

impl VrmlWriter {
    /// Write a full VRML 2.0 document directly to a byte stream.
    pub fn write_to<W: Write>(nodes: &[OutNode], writer: &mut W) -> io::Result<()> {
        Self::write_to_with_progress(nodes, writer, None)
    }

    /// Write a full VRML 2.0 document directly to a byte stream with progress callbacks.
    pub fn write_to_with_progress<'a, W: Write>(
        nodes: &[OutNode],
        writer: &'a mut W,
        on_progress: Option<&'a mut dyn FnMut()>,
    ) -> io::Result<()> {
        let mut state = WriterState::new(writer, on_progress);
        state.writer.write_all(VRML2_HEADER.as_bytes())?;
        state.writer.write_all(b"\n\n")?;

        for (index, node) in nodes.iter().enumerate() {
            if index > 0 {
                state.writer.write_all(b"\n\n")?;
            }
            state.write_node(node, 0)?;
        }

        state.writer.write_all(b"\n")?;
        Ok(())
    }

    /// Render a full VRML 2.0 document from output nodes.
    #[allow(dead_code)]
    pub fn write(nodes: &[OutNode]) -> String {
        let mut output = Vec::new();
        Self::write_to(nodes, &mut output).expect("writing to Vec<u8> cannot fail");
        String::from_utf8(output).expect("writer only emits valid UTF-8")
    }

    /// Count all output nodes recursively for write-progress sizing.
    pub fn count_nodes(nodes: &[OutNode]) -> usize {
        nodes.iter().map(Self::count_node).sum()
    }

    /// Count one output node and all nested child nodes.
    fn count_node(node: &OutNode) -> usize {
        1 + node
            .fields
            .iter()
            .map(|(_, value)| Self::count_value_nodes(value))
            .sum::<usize>()
    }

    /// Count nested nodes reachable from one field value.
    fn count_value_nodes(value: &Value) -> usize {
        match value {
            Value::Node(node) => Self::count_node(node),
            Value::List(values) => values.iter().map(Self::count_value_nodes).sum(),
            _ => 0,
        }
    }
}

/// Hold the mutable writer state used during recursive streaming output.
struct WriterState<'a, W: Write> {
    /// Final byte sink for VRML output.
    writer: &'a mut W,
    /// Optional callback used to update progress as nodes are written.
    on_progress: Option<&'a mut dyn FnMut()>,
}

impl<'a, W: Write> WriterState<'a, W> {
    /// Create a streaming writer state around a byte sink.
    fn new(writer: &'a mut W, on_progress: Option<&'a mut dyn FnMut()>) -> Self {
        Self {
            writer,
            on_progress,
        }
    }

    /// Write one node with indentation that matches the established output format.
    fn write_node(&mut self, node: &OutNode, indent: usize) -> io::Result<()> {
        self.write_indent(indent)?;
        if let Some(def_name) = &node.def_name {
            write!(self.writer, "DEF {def_name} {} ", node.node_type)?;
        } else {
            write!(self.writer, "{} ", node.node_type)?;
        }

        if node.fields.is_empty() {
            self.writer.write_all(b"{\n")?;
            self.write_indent(indent)?;
            self.writer.write_all(b"}")?;
            self.tick_progress();
            return Ok(());
        }

        self.writer.write_all(b"{\n")?;
        for (field_name, value) in &node.fields {
            self.write_field(field_name, value, indent + 2)?;
        }
        self.write_indent(indent)?;
        self.writer.write_all(b"}")?;
        self.tick_progress();
        Ok(())
    }

    /// Write one field assignment and its nested structure when needed.
    fn write_field(&mut self, field_name: &str, value: &Value, indent: usize) -> io::Result<()> {
        self.write_indent(indent)?;
        self.writer.write_all(field_name.as_bytes())?;

        match value {
            Value::Node(node) => {
                self.writer.write_all(b"\n")?;
                self.write_node(node, indent + 2)?;
            }
            Value::Use(use_ref) => {
                self.writer.write_all(b"\n")?;
                self.write_indent(indent + 2)?;
                write!(self.writer, "USE {}", use_ref.name)?;
            }
            Value::List(values) if values.iter().all(is_node_like) => {
                self.writer.write_all(b" [\n")?;
                for (index, item) in values.iter().enumerate() {
                    self.write_node_like(item, indent + 2)?;
                    if index + 1 < values.len() {
                        self.writer.write_all(b",")?;
                    }
                    self.writer.write_all(b"\n")?;
                }
                self.write_indent(indent)?;
                self.writer.write_all(b"]")?;
            }
            Value::List(values) if should_write_compact_coord_index(field_name, values) => {
                self.write_compact_scalar_list(values, indent)?;
            }
            Value::List(values) => {
                self.writer.write_all(b" [\n")?;
                for (index, item) in values.iter().enumerate() {
                    self.write_indent(indent + 2)?;
                    self.write_scalar(item)?;
                    if index + 1 < values.len() {
                        self.writer.write_all(b",")?;
                    }
                    self.writer.write_all(b"\n")?;
                }
                self.write_indent(indent)?;
                self.writer.write_all(b"]")?;
            }
            _ => {
                self.writer.write_all(b" ")?;
                self.write_scalar(value)?;
            }
        }

        self.writer.write_all(b"\n")?;
        Ok(())
    }

    /// Write a scalar list with multiple values packed onto each output line.
    fn write_compact_scalar_list(&mut self, values: &[Value], indent: usize) -> io::Result<()> {
        self.writer.write_all(b" [\n")?;

        for (index, item) in values.iter().enumerate() {
            if index % COMPACT_COORD_INDEX_VALUES_PER_LINE == 0 {
                self.write_indent(indent + 2)?;
            }

            self.write_scalar(item)?;

            if index + 1 < values.len() {
                self.writer.write_all(b",")?;
            }

            if (index + 1) % COMPACT_COORD_INDEX_VALUES_PER_LINE == 0 || index + 1 == values.len() {
                self.writer.write_all(b"\n")?;
            } else if index + 1 < values.len() {
                self.writer.write_all(b" ")?;
            }
        }

        self.write_indent(indent)?;
        self.writer.write_all(b"]")?;
        Ok(())
    }

    /// Write a node-like list item.
    fn write_node_like(&mut self, value: &Value, indent: usize) -> io::Result<()> {
        match value {
            Value::Node(node) => self.write_node(node, indent),
            Value::Use(use_ref) => {
                self.write_indent(indent)?;
                write!(self.writer, "USE {}", use_ref.name)
            }
            _ => {
                self.write_indent(indent)?;
                self.write_scalar(value)
            }
        }
    }

    /// Write one scalar value or fixed-size vector.
    fn write_scalar(&mut self, value: &Value) -> io::Result<()> {
        match value {
            Value::Bool(value) => {
                if *value {
                    self.writer.write_all(b"TRUE")
                } else {
                    self.writer.write_all(b"FALSE")
                }
            }
            Value::Int(value) => write!(self.writer, "{value}"),
            Value::Float(value) => write!(self.writer, "{}", format_number(*value)),
            Value::String(value) => write!(self.writer, "\"{value}\""),
            Value::Identifier(value) => self.writer.write_all(value.as_bytes()),
            Value::Vec(values) => {
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        self.writer.write_all(b" ")?;
                    }
                    write!(self.writer, "{}", format_number(*value))?;
                }
                Ok(())
            }
            Value::List(_) => self.writer.write_all(b"[]"),
            Value::Node(node) => self.write_node(node, 0),
            Value::Use(use_ref) => write!(self.writer, "USE {}", use_ref.name),
        }
    }

    /// Write one logical indentation level using two spaces per level.
    fn write_indent(&mut self, indent: usize) -> io::Result<()> {
        for _ in 0..(indent / 2) {
            self.writer.write_all(b"  ")?;
        }
        Ok(())
    }

    /// Advance the write-progress callback after a node has been serialized.
    fn tick_progress(&mut self) {
        if let Some(callback) = self.on_progress.as_mut() {
            callback();
        }
    }
}

/// Return whether a value should render as a nested node block.
fn is_node_like(value: &Value) -> bool {
    matches!(value, Value::Node(_) | Value::Use(_))
}

/// Return whether a field should use compact multi-value formatting.
fn should_write_compact_coord_index(field_name: &str, values: &[Value]) -> bool {
    field_name == "coordIndex" && values.iter().all(|value| matches!(value, Value::Int(_)))
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

#[cfg(test)]
mod tests {
    use super::VrmlWriter;
    use crate::model::{OutNode, Value};

    #[test]
    /// Keep `coordIndex` lists compact so large meshes stay readable.
    fn coord_index_uses_five_values_per_line() {
        let mut node = OutNode::new("IndexedFaceSet");
        node.fields.push((
            "coordIndex".to_owned(),
            Value::List((0..12).map(Value::Int).collect()),
        ));

        let output = VrmlWriter::write(&[node]);

        assert!(
            output.contains(
                "  coordIndex [\n    0, 1, 2, 3, 4,\n    5, 6, 7, 8, 9,\n    10, 11\n  ]\n"
            )
        );
    }
}
