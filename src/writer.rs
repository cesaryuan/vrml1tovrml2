//! VRML 2.0 text serialization for Rust output nodes.

use std::fmt::Write as FmtWrite;
use std::io::{self, Write};

use rayon::prelude::*;

use crate::model::{OutNode, Value};

const VRML2_HEADER: &str = "#VRML V2.0 utf8";
/// Avoid parallel setup overhead for short scalar lists.
const PARALLEL_SCALAR_LIST_THRESHOLD: usize = 4_096;
/// Avoid parallel setup overhead for short node-like lists.
const PARALLEL_NODE_LIST_THRESHOLD: usize = 64;
/// Keep scalar list chunk strings reasonably large for Rayon workers.
const PARALLEL_SCALAR_LIST_CHUNK_SIZE: usize = 2_048;

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

    /// Write one node with indentation that matches the Python writer.
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
                self.write_node_like_list(values, indent)?;
            }
            Value::List(values) => {
                self.write_scalar_list(values, indent)?;
            }
            _ => {
                self.writer.write_all(b" ")?;
                self.write_scalar(value)?;
            }
        }

        self.writer.write_all(b"\n")?;
        Ok(())
    }

    /// Write one node-like list, parallelizing large independent child renders when safe.
    fn write_node_like_list(&mut self, values: &[Value], indent: usize) -> io::Result<()> {
        if self.can_parallelize() && values.len() >= PARALLEL_NODE_LIST_THRESHOLD {
            return self.write_node_like_list_parallel(values, indent);
        }

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
        Ok(())
    }

    /// Write one scalar list, parallelizing large numeric payloads when progress is disabled.
    fn write_scalar_list(&mut self, values: &[Value], indent: usize) -> io::Result<()> {
        if self.can_parallelize() && values.len() >= PARALLEL_SCALAR_LIST_THRESHOLD {
            return self.write_scalar_list_parallel(values, indent);
        }

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

    /// Write one logical indentation level using tabs like the Python writer.
    fn write_indent(&mut self, indent: usize) -> io::Result<()> {
        for _ in 0..(indent / 2) {
            self.writer.write_all(b"\t")?;
        }
        Ok(())
    }

    /// Advance the write-progress callback after a node has been serialized.
    fn tick_progress(&mut self) {
        if let Some(callback) = self.on_progress.as_mut() {
            callback();
        }
    }

    /// Return whether the current writer state can safely use parallel rendering helpers.
    fn can_parallelize(&self) -> bool {
        self.on_progress.is_none()
    }

    /// Write one large node-like list by rendering each child subtree on a Rayon worker.
    fn write_node_like_list_parallel(&mut self, values: &[Value], indent: usize) -> io::Result<()> {
        let rendered_items = values
            .par_iter()
            .map(|value| render_node_like(value, indent + 2))
            .collect::<Vec<_>>();

        self.writer.write_all(b" [\n")?;
        for (index, rendered) in rendered_items.iter().enumerate() {
            self.writer.write_all(rendered.as_bytes())?;
            if index + 1 < rendered_items.len() {
                self.writer.write_all(b",")?;
            }
            self.writer.write_all(b"\n")?;
        }
        self.write_indent(indent)?;
        self.writer.write_all(b"]")?;
        Ok(())
    }

    /// Write one large scalar list by rendering chunks in parallel before streaming them out.
    fn write_scalar_list_parallel(&mut self, values: &[Value], indent: usize) -> io::Result<()> {
        let rendered_chunks = values
            .par_chunks(PARALLEL_SCALAR_LIST_CHUNK_SIZE)
            .enumerate()
            .map(|(chunk_index, chunk)| render_scalar_chunk(chunk, indent + 2, chunk_index > 0))
            .collect::<Vec<_>>();

        self.writer.write_all(b" [\n")?;
        for rendered in rendered_chunks {
            self.writer.write_all(rendered.as_bytes())?;
        }
        self.write_indent(indent)?;
        self.writer.write_all(b"]")?;
        Ok(())
    }
}

/// Return whether a value should render as a nested node block.
fn is_node_like(value: &Value) -> bool {
    matches!(value, Value::Node(_) | Value::Use(_))
}

/// Render one node-like list item into a standalone string for parallel write assembly.
fn render_node_like(value: &Value, indent: usize) -> String {
    match value {
        Value::Node(node) => render_node(node, indent),
        Value::Use(use_ref) => {
            let mut output = String::new();
            push_indent(&mut output, indent);
            let _ = write!(output, "USE {}", use_ref.name);
            output
        }
        _ => {
            let mut output = String::new();
            push_indent(&mut output, indent);
            output.push_str(&render_scalar(value));
            output
        }
    }
}

/// Render one full node subtree into a standalone string for parallel write assembly.
fn render_node(node: &OutNode, indent: usize) -> String {
    let mut output = String::new();
    push_indent(&mut output, indent);
    if let Some(def_name) = &node.def_name {
        let _ = write!(output, "DEF {def_name} {} ", node.node_type);
    } else {
        let _ = write!(output, "{} ", node.node_type);
    }

    if node.fields.is_empty() {
        output.push_str("{\n");
        push_indent(&mut output, indent);
        output.push('}');
        return output;
    }

    output.push_str("{\n");
    for (field_name, value) in &node.fields {
        output.push_str(&render_field(field_name, value, indent + 2));
        output.push('\n');
    }
    push_indent(&mut output, indent);
    output.push('}');
    output
}

/// Render one field assignment into a standalone string for parallel write assembly.
fn render_field(field_name: &str, value: &Value, indent: usize) -> String {
    let mut output = String::new();
    push_indent(&mut output, indent);
    output.push_str(field_name);

    match value {
        Value::Node(node) => {
            output.push('\n');
            output.push_str(&render_node(node, indent + 2));
        }
        Value::Use(use_ref) => {
            output.push('\n');
            push_indent(&mut output, indent + 2);
            let _ = write!(output, "USE {}", use_ref.name);
        }
        Value::List(values) if values.iter().all(is_node_like) => {
            output.push_str(" [\n");
            for (index, item) in values.iter().enumerate() {
                output.push_str(&render_node_like(item, indent + 2));
                if index + 1 < values.len() {
                    output.push(',');
                }
                output.push('\n');
            }
            push_indent(&mut output, indent);
            output.push(']');
        }
        Value::List(values) => {
            output.push_str(" [\n");
            for (index, item) in values.iter().enumerate() {
                push_indent(&mut output, indent + 2);
                output.push_str(&render_scalar(item));
                if index + 1 < values.len() {
                    output.push(',');
                }
                output.push('\n');
            }
            push_indent(&mut output, indent);
            output.push(']');
        }
        _ => {
            output.push(' ');
            output.push_str(&render_scalar(value));
        }
    }

    output
}

/// Render one chunk from a large scalar list, optionally prefixing a newline between chunks.
fn render_scalar_chunk(values: &[Value], indent: usize, prefix_newline: bool) -> String {
    let mut output = String::new();
    if prefix_newline {
        output.push('\n');
    }
    for (index, value) in values.iter().enumerate() {
        if index > 0 {
            output.push('\n');
        }
        push_indent(&mut output, indent);
        output.push_str(&render_scalar(value));
        output.push(',');
    }
    if output.ends_with(',') {
        output.pop();
    }
    output.push('\n');
    output
}

/// Render one scalar value or vector into a standalone string.
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
        Value::Float(value) => format_number(*value),
        Value::String(value) => format!("\"{value}\""),
        Value::Identifier(value) => value.clone(),
        Value::Vec(values) => values
            .iter()
            .map(|value| format_number(*value))
            .collect::<Vec<_>>()
            .join(" "),
        Value::List(_) => "[]".to_owned(),
        Value::Node(node) => render_node(node, 0),
        Value::Use(use_ref) => format!("USE {}", use_ref.name),
    }
}

/// Append one indentation level sequence using tabs like the streaming writer.
fn push_indent(output: &mut String, indent: usize) {
    for _ in 0..(indent / 2) {
        output.push('\t');
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
