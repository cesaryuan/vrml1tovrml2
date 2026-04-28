//! Streaming tokenizer and parser for a Rust VRML 1.0 implementation.

use std::collections::BTreeMap;

use crate::error::VrmlError;
use crate::model::{AstNode, FieldKind, Statement, Token, TokenKind, UseRef, Value};

const VRML1_HEADER: &str = "#VRML V1.0 ascii";

/// Parse a VRML 1.0 document into statements.
pub fn parse_vrml(input: &str) -> Result<Vec<Statement>, VrmlError> {
    validate_vrml1_header(input)?;
    let tokens = tokenize(input)?;
    let mut parser = Parser::new(tokens);
    parser.parse()
}

/// Verify that the source begins with the expected VRML 1.0 header.
fn validate_vrml1_header(input: &str) -> Result<(), VrmlError> {
    let trimmed = input.trim_start();
    if trimmed.starts_with(VRML1_HEADER) {
        return Ok(());
    }
    Err(VrmlError::from(
        "File does not have a valid VRML 1.0 header string",
    ))
}

/// Tokenize the VRML 1.0 source into parser-ready tokens.
fn tokenize(input: &str) -> Result<Vec<Token>, VrmlError> {
    let mut tokens = Vec::new();
    let bytes = input.as_bytes();
    let mut index = 0usize;
    let mut line = 1usize;
    let mut column = 1usize;

    while index < bytes.len() {
        let char = bytes[index] as char;
        if char == '\n' {
            index += 1;
            line += 1;
            column = 1;
            continue;
        }
        if matches!(char, ' ' | '\t' | '\r' | ',') {
            index += 1;
            column += 1;
            continue;
        }
        if char == '#' {
            while index < bytes.len() && bytes[index] as char != '\n' {
                index += 1;
                column += 1;
            }
            continue;
        }
        if matches!(char, '{' | '}' | '[' | ']') {
            tokens.push(Token {
                kind: TokenKind::Symbol,
                value: char.to_string(),
                line,
                column,
            });
            index += 1;
            column += 1;
            continue;
        }
        if char == '"' {
            let start_line = line;
            let start_column = column;
            index += 1;
            column += 1;
            let mut value = String::new();
            while index < bytes.len() {
                let current = bytes[index] as char;
                if current == '"' {
                    index += 1;
                    column += 1;
                    break;
                }
                if current == '\\' {
                    index += 1;
                    column += 1;
                    if index >= bytes.len() {
                        return Err(VrmlError::from(format!(
                            "Unterminated string at line {start_line}, column {start_column}"
                        )));
                    }
                    let escaped = bytes[index] as char;
                    let mapped = match escaped {
                        'n' => '\n',
                        't' => '\t',
                        '"' => '"',
                        '\\' => '\\',
                        other => other,
                    };
                    value.push(mapped);
                    index += 1;
                    column += 1;
                    continue;
                }
                value.push(current);
                index += 1;
                column += 1;
            }
            tokens.push(Token {
                kind: TokenKind::String,
                value,
                line: start_line,
                column: start_column,
            });
            continue;
        }

        let start = index;
        let start_line = line;
        let start_column = column;
        while index < bytes.len() {
            let current = bytes[index] as char;
            if matches!(
                current,
                ' ' | '\t' | '\r' | '\n' | ',' | '{' | '}' | '[' | ']' | '"' | '#'
            ) {
                break;
            }
            index += 1;
            column += 1;
        }
        let value = &input[start..index];
        let kind = if looks_like_number(value) {
            TokenKind::Number
        } else {
            TokenKind::Identifier
        };
        tokens.push(Token {
            kind,
            value: value.to_owned(),
            line: start_line,
            column: start_column,
        });
    }

    tokens.push(Token {
        kind: TokenKind::Eof,
        value: String::new(),
        line,
        column,
    });
    Ok(tokens)
}

/// Return whether a token text should be treated as numeric.
fn looks_like_number(value: &str) -> bool {
    value.parse::<f64>().is_ok()
}

/// Parse VRML tokens into a tree of statements.
struct Parser {
    /// Hold the token stream in memory for simple lookahead.
    tokens: Vec<Token>,
    /// Track the current read position.
    index: usize,
}

impl Parser {
    /// Create a parser over an owned token vector.
    fn new(tokens: Vec<Token>) -> Self {
        Self { tokens, index: 0 }
    }

    /// Parse the full token stream into top-level statements.
    fn parse(&mut self) -> Result<Vec<Statement>, VrmlError> {
        let mut statements = Vec::new();
        while !self.at_end() {
            statements.push(self.parse_statement()?);
        }
        Ok(statements)
    }

    /// Parse one statement, including `DEF` and `USE` forms.
    fn parse_statement(&mut self) -> Result<Statement, VrmlError> {
        if self.match_identifier("DEF") {
            let def_name = self.consume(TokenKind::Identifier, "Expected name after DEF")?.value;
            let mut node = match self.parse_statement()? {
                Statement::Node(node) => node,
                Statement::Use(_) => {
                    return Err(VrmlError::from(format!("DEF {def_name} must target a node")))
                }
            };
            node.def_name = Some(def_name);
            return Ok(Statement::Node(node));
        }
        if self.match_identifier("USE") {
            let name = self
                .consume(TokenKind::Identifier, "Expected name after USE")?
                .value;
            return Ok(Statement::Use(UseRef { name }));
        }
        self.parse_node().map(Statement::Node)
    }

    /// Parse one node with fields and child statements.
    fn parse_node(&mut self) -> Result<AstNode, VrmlError> {
        let node_type = self
            .consume(TokenKind::Identifier, "Expected node type")?
            .value;
        self.consume_symbol("{", &format!("Expected '{{' after node type {node_type}"))?;

        let mut fields = BTreeMap::new();
        let mut children = Vec::new();

        while !self.check_symbol("}") && !self.at_end() {
            if self.looks_like_statement() {
                children.push(self.parse_statement()?);
                continue;
            }
            let field_name = self
                .consume(TokenKind::Identifier, &format!("Expected field name in {node_type}"))?
                .value;
            let field_kind = field_kind(&node_type, &field_name);
            let value = self.parse_field_value(&node_type, &field_name, field_kind)?;
            fields.insert(field_name, value);
        }

        self.consume_symbol("}", &format!("Expected '}}' after node {node_type}"))?;
        Ok(AstNode {
            node_type,
            fields,
            children,
            def_name: None,
        })
    }

    /// Parse one field value according to its node-specific field kind.
    fn parse_field_value(
        &mut self,
        node_type: &str,
        field_name: &str,
        field_kind: FieldKind,
    ) -> Result<Value, VrmlError> {
        match field_kind {
            FieldKind::Bool => self.parse_bool(),
            FieldKind::Int => Ok(Value::Int(self.parse_int("Expected integer")?)),
            FieldKind::Float => Ok(Value::Float(self.parse_float("Expected float")?)),
            FieldKind::Vec2 => Ok(Value::Vec(self.parse_fixed_vector(2, "Expected numeric vector value")?)),
            FieldKind::Vec3 => Ok(Value::Vec(self.parse_fixed_vector(3, "Expected numeric vector value")?)),
            FieldKind::Rotation => Ok(Value::Vec(
                self.parse_fixed_vector(4, "Expected numeric rotation value")?,
            )),
            FieldKind::MfInt => self.parse_multi_numeric_values(1, false),
            FieldKind::MfFloat => self.parse_multi_numeric_values(1, true),
            FieldKind::MfVec3 => self.parse_multi_numeric_values(3, true),
            FieldKind::MfString => self.parse_multi_strings(),
            FieldKind::Enum => {
                let token = self.consume(TokenKind::Identifier, &format!("Expected enum value for {field_name}"))?;
                Ok(Value::Identifier(token.value))
            }
            FieldKind::Auto => self.parse_auto_value(node_type, field_name),
        }
    }

    /// Parse a best-effort value form for less structured fields.
    fn parse_auto_value(&mut self, _node_type: &str, field_name: &str) -> Result<Value, VrmlError> {
        if self.match_identifier("DEF") {
            let def_name = self.consume(TokenKind::Identifier, "Expected name after DEF")?.value;
            return match self.parse_statement()? {
                Statement::Node(mut node) => {
                    node.def_name = Some(def_name);
                    Ok(Value::Node(Box::new(convert_ast_node_to_out_node(node)?)))
                }
                Statement::Use(_) => {
                    return Err(VrmlError::from(format!("DEF {def_name} must target a node")))
                }
            };
        }
        if self.match_identifier("USE") {
            let name = self
                .consume(TokenKind::Identifier, "Expected name after USE")?
                .value;
            return Ok(Value::Use(UseRef { name }));
        }
        if self.check_symbol("[") {
            return self.parse_list();
        }
        let token = self.peek().clone();
        match token.kind {
            TokenKind::String => {
                self.advance();
                Ok(Value::String(token.value))
            }
            TokenKind::Number => {
                self.advance();
                let number = token.value.parse::<f64>().map_err(|error| {
                    VrmlError::from(format!("Invalid numeric value {}: {error}", token.value))
                })?;
                Ok(Value::Float(number))
            }
            TokenKind::Identifier => {
                if self.peek_next_symbol("{") {
                    let nested = self.parse_statement()?;
                    return match nested {
                        Statement::Node(node) => Ok(Value::Node(Box::new(convert_ast_node_to_out_node(node)?))),
                        Statement::Use(use_ref) => Ok(Value::Use(use_ref)),
                    };
                }
                self.advance();
                Ok(Value::Identifier(token.value))
            }
            _ => Err(VrmlError::from(format!(
                "Unsupported value while reading field {field_name} at line {}, column {}",
                token.line, token.column
            ))),
        }
    }

    /// Parse a generic bracketed value list.
    fn parse_list(&mut self) -> Result<Value, VrmlError> {
        self.consume_symbol("[", "Expected '['")?;
        let mut values = Vec::new();
        while !self.check_symbol("]") && !self.at_end() {
            if self.looks_like_statement() {
                match self.parse_statement()? {
                    Statement::Node(node) => values.push(Value::Node(Box::new(convert_ast_node_to_out_node(node)?))),
                    Statement::Use(use_ref) => values.push(Value::Use(use_ref)),
                }
                continue;
            }
            let token = self.peek().clone();
            match token.kind {
                TokenKind::String => {
                    self.advance();
                    values.push(Value::String(token.value));
                }
                TokenKind::Number => {
                    self.advance();
                    values.push(Value::Float(token.value.parse::<f64>().map_err(|error| {
                        VrmlError::from(format!("Invalid numeric value {}: {error}", token.value))
                    })?));
                }
                TokenKind::Identifier => {
                    self.advance();
                    values.push(Value::Identifier(token.value));
                }
                _ => return Err(VrmlError::from("Unsupported list value")),
            }
        }
        self.consume_symbol("]", "Expected ']'")?;
        Ok(Value::List(values))
    }

    /// Parse a VRML boolean literal.
    fn parse_bool(&mut self) -> Result<Value, VrmlError> {
        let token = self.consume(TokenKind::Identifier, "Expected TRUE or FALSE")?;
        match token.value.to_ascii_uppercase().as_str() {
            "TRUE" => Ok(Value::Bool(true)),
            "FALSE" => Ok(Value::Bool(false)),
            other => Err(VrmlError::from(format!("Illegal value for QvSFBool: {other}"))),
        }
    }

    /// Parse one integer token.
    fn parse_int(&mut self, message: &str) -> Result<i32, VrmlError> {
        let token = self.consume(TokenKind::Number, message)?;
        token
            .value
            .parse::<i32>()
            .map_err(|error| VrmlError::from(format!("Invalid integer value {}: {error}", token.value)))
    }

    /// Parse one float token.
    fn parse_float(&mut self, message: &str) -> Result<f64, VrmlError> {
        let token = self.consume(TokenKind::Number, message)?;
        token
            .value
            .parse::<f64>()
            .map_err(|error| VrmlError::from(format!("Invalid float value {}: {error}", token.value)))
    }

    /// Parse a fixed-width numeric vector.
    fn parse_fixed_vector(&mut self, arity: usize, message: &str) -> Result<Vec<f64>, VrmlError> {
        let mut values = Vec::with_capacity(arity);
        for _ in 0..arity {
            values.push(self.parse_float(message)?);
        }
        Ok(values)
    }

    /// Parse one scalar or bracketed numeric multi-value field.
    fn parse_multi_numeric_values(
        &mut self,
        arity: usize,
        floats: bool,
    ) -> Result<Value, VrmlError> {
        if !self.check_symbol("[") {
            return self.parse_multi_numeric_item(arity, floats);
        }

        self.consume_symbol("[", "Expected '['")?;
        let mut values = Vec::new();
        while !self.check_symbol("]") && !self.at_end() {
            values.push(self.parse_multi_numeric_item(arity, floats)?);
        }
        self.consume_symbol("]", "Expected ']'")?;
        Ok(Value::List(values))
    }

    /// Parse one numeric item from a multi-value field.
    fn parse_multi_numeric_item(&mut self, arity: usize, floats: bool) -> Result<Value, VrmlError> {
        if arity == 1 {
            return if floats {
                Ok(Value::Float(self.parse_float("Expected numeric value")?))
            } else {
                Ok(Value::Int(self.parse_int("Expected numeric value")?))
            };
        }
        Ok(Value::Vec(self.parse_fixed_vector(
            arity,
            "Expected numeric vector value",
        )?))
    }

    /// Parse one or more string values, optionally bracketed.
    fn parse_multi_strings(&mut self) -> Result<Value, VrmlError> {
        let mut values = Vec::new();
        if self.check_symbol("[") {
            self.consume_symbol("[", "Expected '['")?;
            while !self.check_symbol("]") && !self.at_end() {
                let token = self.peek().clone();
                if !matches!(token.kind, TokenKind::String | TokenKind::Identifier) {
                    return Err(VrmlError::from(format!(
                        "Expected string value at line {}, column {}",
                        token.line, token.column
                    )));
                }
                self.advance();
                values.push(Value::String(token.value));
            }
            self.consume_symbol("]", "Expected ']'")?;
            return Ok(Value::List(values));
        }

        let token = self.peek().clone();
        if !matches!(token.kind, TokenKind::String | TokenKind::Identifier) {
            return Err(VrmlError::from(format!(
                "Expected string value at line {}, column {}",
                token.line, token.column
            )));
        }
        self.advance();
        Ok(Value::List(vec![Value::String(token.value)]))
    }

    /// Consume one token of the expected kind.
    fn consume(&mut self, expected: TokenKind, message: &str) -> Result<Token, VrmlError> {
        if self.peek().kind == expected {
            return Ok(self.advance());
        }
        let token = self.peek();
        Err(VrmlError::from(format!(
            "{message} at line {}, column {}",
            token.line, token.column
        )))
    }

    /// Consume one symbol token with the expected text.
    fn consume_symbol(&mut self, symbol: &str, message: &str) -> Result<(), VrmlError> {
        if self.check_symbol(symbol) {
            self.advance();
            return Ok(());
        }
        let token = self.peek();
        Err(VrmlError::from(format!(
            "{message} at line {}, column {}",
            token.line, token.column
        )))
    }

    /// Match one identifier token with the expected value.
    fn match_identifier(&mut self, value: &str) -> bool {
        if self.peek().kind == TokenKind::Identifier && self.peek().value == value {
            self.advance();
            return true;
        }
        false
    }

    /// Return whether the next token closes a node or list.
    fn check_symbol(&self, symbol: &str) -> bool {
        self.peek().kind == TokenKind::Symbol && self.peek().value == symbol
    }

    /// Return whether the current token sequence looks like a child statement.
    fn looks_like_statement(&self) -> bool {
        self.peek_value("DEF")
            || self.peek_value("USE")
            || (self.peek().kind == TokenKind::Identifier && self.peek_next_symbol("{"))
    }

    /// Return whether the current token matches the provided identifier text.
    fn peek_value(&self, value: &str) -> bool {
        self.peek().kind == TokenKind::Identifier && self.peek().value == value
    }

    /// Return whether the next token after the current one is the provided symbol.
    fn peek_next_symbol(&self, value: &str) -> bool {
        self.peek_n(1).kind == TokenKind::Symbol && self.peek_n(1).value == value
    }

    /// Return the current token.
    fn peek(&self) -> &Token {
        self.peek_n(0)
    }

    /// Return the token at the current position plus an offset.
    fn peek_n(&self, offset: usize) -> &Token {
        let index = self.index.saturating_add(offset);
        self.tokens.get(index).unwrap_or_else(|| {
            self.tokens
                .last()
                .expect("parser token stream always contains eof")
        })
    }

    /// Advance and return the current token.
    fn advance(&mut self) -> Token {
        let token = self.peek_n(0).clone();
        if token.kind != TokenKind::Eof {
            self.index += 1;
        }
        token
    }

    /// Return whether the parser has consumed the entire token stream.
    fn at_end(&self) -> bool {
        self.peek().kind == TokenKind::Eof
    }
}

/// Return the parse specification for one known node field.
fn field_kind(node_type: &str, field_name: &str) -> FieldKind {
    match (node_type, field_name) {
        ("PerspectiveCamera", "position") => FieldKind::Vec3,
        ("PerspectiveCamera", "orientation") => FieldKind::Rotation,
        ("PerspectiveCamera", "heightAngle") => FieldKind::Float,
        ("DirectionalLight", "direction") => FieldKind::Vec3,
        ("DirectionalLight", "color") => FieldKind::Vec3,
        ("DirectionalLight", "intensity") => FieldKind::Float,
        ("Material", "ambientColor") => FieldKind::MfVec3,
        ("Material", "diffuseColor") => FieldKind::MfVec3,
        ("Material", "specularColor") => FieldKind::MfVec3,
        ("Material", "emissiveColor") => FieldKind::MfVec3,
        ("Material", "shininess") => FieldKind::MfFloat,
        ("Material", "transparency") => FieldKind::MfFloat,
        ("MaterialBinding", "value") => FieldKind::Enum,
        ("NormalBinding", "value") => FieldKind::Enum,
        ("Coordinate3", "point") => FieldKind::MfVec3,
        ("Normal", "vector") => FieldKind::MfVec3,
        ("ShapeHints", "vertexOrdering") => FieldKind::Enum,
        ("ShapeHints", "shapeType") => FieldKind::Enum,
        ("ShapeHints", "faceType") => FieldKind::Enum,
        ("ShapeHints", "creaseAngle") => FieldKind::Float,
        ("IndexedFaceSet", "coordIndex") => FieldKind::MfInt,
        ("IndexedFaceSet", "materialIndex") => FieldKind::MfInt,
        ("IndexedFaceSet", "normalIndex") => FieldKind::MfInt,
        ("IndexedFaceSet", "textureCoordIndex") => FieldKind::MfInt,
        ("Translation", "translation") => FieldKind::Vec3,
        ("Cube", "width") => FieldKind::Float,
        ("Cube", "height") => FieldKind::Float,
        ("Cube", "depth") => FieldKind::Float,
        ("FontStyle", "size") => FieldKind::Float,
        ("FontStyle", "family") => FieldKind::Enum,
        ("FontStyle", "style") => FieldKind::Enum,
        ("AsciiText", "string") => FieldKind::MfString,
        ("AsciiText", "spacing") => FieldKind::Float,
        ("AsciiText", "justification") => FieldKind::Enum,
        ("AsciiText", "width") => FieldKind::Float,
        ("IndexedLineSet", "coordIndex") => FieldKind::MfInt,
        ("PointSet", "startIndex") => FieldKind::Int,
        ("PointSet", "numPoints") => FieldKind::Int,
        _ => FieldKind::Auto,
    }
}

/// Convert a parsed AST node into a structurally equivalent output node placeholder.
fn convert_ast_node_to_out_node(node: AstNode) -> Result<crate::model::OutNode, VrmlError> {
    let mut out_node = crate::model::OutNode::new(node.node_type);
    out_node.def_name = node.def_name;
    out_node.fields = node.fields.into_iter().collect();
    for child in node.children {
        let value = match child {
            Statement::Node(child_node) => Value::Node(Box::new(convert_ast_node_to_out_node(child_node)?)),
            Statement::Use(use_ref) => Value::Use(use_ref),
        };
        out_node.fields.push(("__child__".to_owned(), value));
    }
    Ok(out_node)
}
