//! Shared data structures used across parsing, conversion, and writing.

use std::collections::BTreeMap;

/// Store one lexical token emitted by the tokenizer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Token {
    /// Classify the token for the parser.
    pub kind: TokenKind,
    /// Preserve the original token text when relevant.
    pub value: String,
    /// Track the source line for diagnostics.
    pub line: usize,
    /// Track the source column for diagnostics.
    pub column: usize,
}

/// Enumerate the token classes understood by the parser.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TokenKind {
    /// Symbolic delimiters like `{`, `}`, `[` and `]`.
    Symbol,
    /// Numeric tokens, kept as text until parsed.
    Number,
    /// String literal tokens without the surrounding quotes.
    String,
    /// Identifier-like tokens.
    Identifier,
    /// Synthetic end-of-file marker.
    Eof,
}

/// Represent a parsed or emitted `USE` reference.
#[derive(Clone, Debug, PartialEq)]
pub struct UseRef {
    /// Name of the previously defined node or state object.
    pub name: String,
}

/// Represent one parsed VRML 1.0 node.
#[derive(Clone, Debug, PartialEq)]
pub struct AstNode {
    /// Source node type such as `Separator` or `Material`.
    pub node_type: String,
    /// Parsed named fields.
    pub fields: BTreeMap<String, Value>,
    /// Parsed child statements.
    pub children: Vec<Statement>,
    /// Optional `DEF` name attached to the node.
    pub def_name: Option<String>,
}

/// Represent one top-level or child statement in the parsed AST.
#[derive(Clone, Debug, PartialEq)]
pub enum Statement {
    /// A full node instance.
    Node(AstNode),
    /// A `USE` reference.
    Use(UseRef),
}

/// Represent a serialized VRML 2.0 node.
#[derive(Clone, Debug, PartialEq)]
pub struct OutNode {
    /// Output node type such as `Transform` or `Shape`.
    pub node_type: String,
    /// Output node fields, preserving order.
    pub fields: Vec<(String, Value)>,
    /// Optional `DEF` name attached to the output node.
    pub def_name: Option<String>,
}

/// Represent the value forms supported by the parser and writer.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    /// Boolean scalar.
    Bool(bool),
    /// Signed integer scalar.
    Int(i32),
    /// Floating-point scalar.
    Float(f64),
    /// String scalar.
    String(String),
    /// Identifier-like enum or symbolic value.
    Identifier(String),
    /// Fixed-size numeric vector.
    Vec(Vec<f64>),
    /// Bracketed value list.
    List(Vec<Value>),
    /// Nested node value.
    Node(Box<OutNode>),
    /// Nested use reference value.
    Use(UseRef),
}

/// Describe how a node field should be parsed from VRML 1.0 text.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FieldKind {
    /// Boolean literal.
    Bool,
    /// Integer scalar.
    Int,
    /// Float scalar.
    Float,
    /// Two-component vector.
    Vec2,
    /// Three-component vector.
    Vec3,
    /// Four-component rotation vector.
    Rotation,
    /// Multi-value integer list.
    MfInt,
    /// Multi-value float list.
    MfFloat,
    /// Multi-value 3D vector list.
    MfVec3,
    /// Multi-value string list.
    MfString,
    /// Identifier-like enum or symbol.
    Enum,
    /// Best-effort fallback parsing.
    Auto,
}

impl OutNode {
    /// Create a new output node with no fields or `DEF` name.
    pub fn new(node_type: impl Into<String>) -> Self {
        Self {
            node_type: node_type.into(),
            fields: Vec::new(),
            def_name: None,
        }
    }
}
