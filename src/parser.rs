//! Streaming tokenizer and parser for a Rust VRML 1.0 implementation.

use std::collections::{BTreeMap, VecDeque};
use std::io::{Cursor, Read};

use crate::error::VrmlError;
use crate::model::{AstNode, FieldKind, OutNode, Statement, Token, TokenKind, UseRef, Value};

const VRML1_HEADER: &str = "#VRML V1.0 ascii";

/// Parse a VRML 1.0 document from an in-memory string.
#[allow(dead_code)]
pub fn parse_vrml(input: &str) -> Result<Vec<Statement>, VrmlError> {
    parse_vrml_reader(Cursor::new(input.as_bytes()))
}

/// Parse a VRML 1.0 document from any readable byte stream.
pub fn parse_vrml_reader<R: Read>(reader: R) -> Result<Vec<Statement>, VrmlError> {
    let mut char_reader = CharReader::new(reader);
    validate_vrml1_header(&mut char_reader)?;
    let tokenizer = VrmlTokenizer::new(char_reader);
    let token_buffer = TokenBuffer::new(tokenizer);
    let mut parser = Parser::new(token_buffer);
    parser.parse()
}

/// Read characters from an input stream with a bounded rolling buffer.
struct CharReader<R: Read> {
    /// Underlying byte source.
    reader: R,
    /// Number of bytes requested per chunk refill.
    chunk_size: usize,
    /// Rolling text buffer.
    buffer: String,
    /// Current byte offset inside the rolling buffer.
    index: usize,
    /// Whether the source has been fully consumed.
    eof: bool,
    /// Current 1-based source line.
    line: usize,
    /// Current 1-based source column.
    column: usize,
}

impl<R: Read> CharReader<R> {
    /// Create a character reader over any readable source.
    fn new(reader: R) -> Self {
        Self {
            reader,
            chunk_size: 64 * 1024,
            buffer: String::new(),
            index: 0,
            eof: false,
            line: 1,
            column: 1,
        }
    }

    /// Return the next character without consuming it.
    fn peek(&mut self, offset: usize) -> Result<Option<char>, VrmlError> {
        self.ensure(offset + 1)?;
        let position = self.index + offset;
        if position >= self.buffer.len() {
            return Ok(None);
        }
        Ok(self.buffer.as_bytes().get(position).map(|byte| *byte as char))
    }

    /// Consume and return the next character.
    fn advance(&mut self) -> Result<Option<char>, VrmlError> {
        self.ensure(1)?;
        if self.index >= self.buffer.len() {
            return Ok(None);
        }

        let character = self.buffer.as_bytes()[self.index] as char;
        self.index += 1;
        if character == '\n' {
            self.line += 1;
            self.column = 1;
        } else {
            self.column += 1;
        }
        self.trim();
        Ok(Some(character))
    }

    /// Consume characters until the current line ends.
    fn skip_line(&mut self) -> Result<(), VrmlError> {
        while let Some(character) = self.peek(0)? {
            if character == '\n' {
                break;
            }
            self.advance()?;
        }
        Ok(())
    }

    /// Refill the rolling buffer so at least `needed` characters are available.
    fn ensure(&mut self, needed: usize) -> Result<(), VrmlError> {
        while !self.eof && self.buffer.len().saturating_sub(self.index) < needed {
            let mut chunk = vec![0u8; self.chunk_size];
            let read = self.reader.read(&mut chunk)?;
            if read == 0 {
                self.eof = true;
                break;
            }
            chunk.truncate(read);
            let text = String::from_utf8(chunk).map_err(|error| {
                VrmlError::from(format!("Input is not valid UTF-8/ASCII VRML text: {error}"))
            })?;

            if self.index > 0 {
                let remainder = self.buffer.split_off(self.index);
                self.buffer = remainder;
                self.index = 0;
            }
            self.buffer.push_str(&text);
        }
        Ok(())
    }

    /// Drop already-consumed text so the rolling buffer stays bounded.
    fn trim(&mut self) {
        if self.index >= 32 * 1024 {
            let remainder = self.buffer.split_off(self.index);
            self.buffer = remainder;
            self.index = 0;
        }
    }
}

/// Verify that the source begins with the expected VRML 1.0 header.
fn validate_vrml1_header<R: Read>(reader: &mut CharReader<R>) -> Result<(), VrmlError> {
    while matches!(reader.peek(0)?, Some(character) if character.is_whitespace()) {
        reader.advance()?;
    }
    for expected in VRML1_HEADER.chars() {
        let actual = reader.advance()?;
        if actual != Some(expected) {
            return Err(VrmlError::from(
                "File does not have a valid VRML 1.0 header string",
            ));
        }
    }
    Ok(())
}

/// Turn VRML text into a token stream while reading incrementally.
struct VrmlTokenizer<R: Read> {
    /// Character reader consumed by the tokenizer.
    reader: CharReader<R>,
}

impl<R: Read> VrmlTokenizer<R> {
    /// Create a tokenizer over a character reader.
    fn new(reader: CharReader<R>) -> Self {
        Self { reader }
    }
}

impl<R: Read> Iterator for VrmlTokenizer<R> {
    type Item = Result<Token, VrmlError>;

    /// Yield the next lexical token from the stream.
    fn next(&mut self) -> Option<Self::Item> {
        loop {
            let character = match self.reader.peek(0) {
                Ok(value) => value,
                Err(error) => return Some(Err(error)),
            }?;

            if character == '\n' || matches!(character, ' ' | '\t' | '\r' | ',') {
                if let Err(error) = self.reader.advance() {
                    return Some(Err(error));
                }
                continue;
            }

            if character == '#' {
                if let Err(error) = self.reader.skip_line() {
                    return Some(Err(error));
                }
                continue;
            }

            let line = self.reader.line;
            let column = self.reader.column;

            if matches!(character, '{' | '}' | '[' | ']') {
                if let Err(error) = self.reader.advance() {
                    return Some(Err(error));
                }
                return Some(Ok(Token {
                    kind: TokenKind::Symbol,
                    value: character.to_string(),
                    line,
                    column,
                }));
            }

            if character == '"' {
                return Some(read_string(&mut self.reader, line, column));
            }

            return Some(read_identifier_or_number(&mut self.reader, line, column));
        }
    }
}

/// Read one quoted string token.
fn read_string<R: Read>(
    reader: &mut CharReader<R>,
    line: usize,
    column: usize,
) -> Result<Token, VrmlError> {
    reader.advance()?;
    let mut buffer = String::new();

    loop {
        let Some(character) = reader.peek(0)? else {
            return Err(VrmlError::from(format!(
                "Unterminated string at line {line}, column {column}"
            )));
        };

        if character == '\\' {
            reader.advance()?;
            let Some(escaped) = reader.peek(0)? else {
                return Err(VrmlError::from(format!(
                    "Unterminated string at line {line}, column {column}"
                )));
            };
            let mapped = match escaped {
                'n' => '\n',
                't' => '\t',
                '"' => '"',
                '\\' => '\\',
                other => other,
            };
            buffer.push(mapped);
            reader.advance()?;
            continue;
        }

        if character == '"' {
            reader.advance()?;
            return Ok(Token {
                kind: TokenKind::String,
                value: buffer,
                line,
                column,
            });
        }

        buffer.push(character);
        reader.advance()?;
    }
}

/// Read one identifier-like token and classify it as identifier or number.
fn read_identifier_or_number<R: Read>(
    reader: &mut CharReader<R>,
    line: usize,
    column: usize,
) -> Result<Token, VrmlError> {
    let mut buffer = String::new();
    while let Some(character) = reader.peek(0)? {
        if is_delimiter(character) {
            break;
        }
        buffer.push(character);
        reader.advance()?;
    }

    let kind = if buffer.parse::<f64>().is_ok() {
        TokenKind::Number
    } else {
        TokenKind::Identifier
    };

    Ok(Token {
        kind,
        value: buffer,
        line,
        column,
    })
}

/// Return whether a character ends the current token.
fn is_delimiter(character: char) -> bool {
    matches!(
        character,
        ' ' | '\t' | '\r' | '\n' | ',' | '{' | '}' | '[' | ']' | '"' | '#'
    )
}

/// Provide bounded lookahead over a streaming token iterator.
struct TokenBuffer<I: Iterator<Item = Result<Token, VrmlError>>> {
    /// Streaming source of tokens.
    tokens: I,
    /// Buffered lookahead window.
    buffer: VecDeque<Token>,
    /// Whether the underlying iterator is exhausted.
    exhausted: bool,
}

impl<I: Iterator<Item = Result<Token, VrmlError>>> TokenBuffer<I> {
    /// Create an empty lookahead buffer over a token iterator.
    fn new(tokens: I) -> Self {
        Self {
            tokens,
            buffer: VecDeque::new(),
            exhausted: false,
        }
    }

    /// Return the token at the current position plus an offset.
    fn peek(&mut self, offset: usize) -> Result<Token, VrmlError> {
        self.ensure(offset + 1)?;
        Ok(self.buffer.get(offset).cloned().unwrap_or(Token {
            kind: TokenKind::Eof,
            value: String::new(),
            line: 0,
            column: 0,
        }))
    }

    /// Consume and return the current token.
    fn advance(&mut self) -> Result<Token, VrmlError> {
        self.ensure(1)?;
        Ok(self.buffer.pop_front().unwrap_or(Token {
            kind: TokenKind::Eof,
            value: String::new(),
            line: 0,
            column: 0,
        }))
    }

    /// Fill the lookahead window up to the requested size.
    fn ensure(&mut self, count: usize) -> Result<(), VrmlError> {
        while !self.exhausted && self.buffer.len() < count {
            match self.tokens.next() {
                Some(Ok(token)) => self.buffer.push_back(token),
                Some(Err(error)) => return Err(error),
                None => {
                    self.exhausted = true;
                    break;
                }
            }
        }
        Ok(())
    }
}

/// Parse VRML tokens into a tree of statements.
struct Parser<I: Iterator<Item = Result<Token, VrmlError>>> {
    /// Token lookahead buffer used by the parser.
    tokens: TokenBuffer<I>,
}

impl<I: Iterator<Item = Result<Token, VrmlError>>> Parser<I> {
    /// Create a parser over a token buffer.
    fn new(tokens: TokenBuffer<I>) -> Self {
        Self { tokens }
    }

    /// Parse the full token stream into top-level statements.
    fn parse(&mut self) -> Result<Vec<Statement>, VrmlError> {
        let mut statements = Vec::new();
        while !self.at_end()? {
            statements.push(self.parse_statement()?);
        }
        Ok(statements)
    }

    /// Parse one statement, including `DEF` and `USE` forms.
    fn parse_statement(&mut self) -> Result<Statement, VrmlError> {
        if self.match_identifier("DEF")? {
            let def_name = self
                .consume(TokenKind::Identifier, "Expected name after DEF")?
                .value;
            let mut node = match self.parse_statement()? {
                Statement::Node(node) => node,
                Statement::Use(_) => {
                    return Err(VrmlError::from(format!(
                        "DEF {def_name} must target a node"
                    )))
                }
            };
            node.def_name = Some(def_name);
            return Ok(Statement::Node(node));
        }

        if self.match_identifier("USE")? {
            let name = self
                .consume(TokenKind::Identifier, "Expected name after USE")?
                .value;
            return Ok(Statement::Use(UseRef { name }));
        }

        self.parse_node().map(Statement::Node)
    }

    /// Parse one node with fields and child statements.
    fn parse_node(&mut self) -> Result<AstNode, VrmlError> {
        let node_type = self.consume(TokenKind::Identifier, "Expected node type")?.value;
        self.consume_symbol("{", &format!("Expected '{{' after node type {node_type}"))?;

        let mut fields = BTreeMap::new();
        let mut children = Vec::new();

        while !self.check_symbol("}")? && !self.at_end()? {
            if self.looks_like_statement()? {
                children.push(self.parse_statement()?);
                continue;
            }
            let field_name = self
                .consume(
                    TokenKind::Identifier,
                    &format!("Expected field name in {node_type}"),
                )?
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
            FieldKind::Vec2 => Ok(Value::Vec(
                self.parse_fixed_vector(2, "Expected numeric vector value")?,
            )),
            FieldKind::Vec3 => Ok(Value::Vec(
                self.parse_fixed_vector(3, "Expected numeric vector value")?,
            )),
            FieldKind::Rotation => Ok(Value::Vec(
                self.parse_fixed_vector(4, "Expected numeric rotation value")?,
            )),
            FieldKind::MfInt => self.parse_multi_numeric_values(1, false),
            FieldKind::MfFloat => self.parse_multi_numeric_values(1, true),
            FieldKind::MfVec3 => self.parse_multi_numeric_values(3, true),
            FieldKind::MfString => self.parse_multi_strings(),
            FieldKind::Enum => {
                let token = self.consume(
                    TokenKind::Identifier,
                    &format!("Expected enum value for {field_name}"),
                )?;
                Ok(Value::Identifier(token.value))
            }
            FieldKind::Auto => self.parse_auto_value(node_type, field_name),
        }
    }

    /// Parse a best-effort value form for less structured fields.
    fn parse_auto_value(
        &mut self,
        _node_type: &str,
        field_name: &str,
    ) -> Result<Value, VrmlError> {
        if self.match_identifier("DEF")? {
            let def_name = self
                .consume(TokenKind::Identifier, "Expected name after DEF")?
                .value;
            return match self.parse_statement()? {
                Statement::Node(mut node) => {
                    node.def_name = Some(def_name);
                    Ok(Value::Node(Box::new(convert_ast_node_to_out_node(node)?)))
                }
                Statement::Use(_) => {
                    Err(VrmlError::from(format!("DEF {def_name} must target a node")))
                }
            };
        }

        if self.match_identifier("USE")? {
            let name = self
                .consume(TokenKind::Identifier, "Expected name after USE")?
                .value;
            return Ok(Value::Use(UseRef { name }));
        }

        if self.check_symbol("[")? {
            return self.parse_list();
        }

        let token = self.peek()?;
        match token.kind {
            TokenKind::String => {
                self.advance()?;
                Ok(Value::String(token.value))
            }
            TokenKind::Number => {
                self.advance()?;
                let number = token.value.parse::<f64>().map_err(|error| {
                    VrmlError::from(format!("Invalid numeric value {}: {error}", token.value))
                })?;
                Ok(Value::Float(number))
            }
            TokenKind::Identifier => {
                if self.peek_next_symbol("{")? {
                    let nested = self.parse_statement()?;
                    return match nested {
                        Statement::Node(node) => {
                            Ok(Value::Node(Box::new(convert_ast_node_to_out_node(node)?)))
                        }
                        Statement::Use(use_ref) => Ok(Value::Use(use_ref)),
                    };
                }
                self.advance()?;
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
        while !self.check_symbol("]")? && !self.at_end()? {
            if self.looks_like_statement()? {
                match self.parse_statement()? {
                    Statement::Node(node) => {
                        values.push(Value::Node(Box::new(convert_ast_node_to_out_node(node)?)))
                    }
                    Statement::Use(use_ref) => values.push(Value::Use(use_ref)),
                }
                continue;
            }
            let token = self.peek()?;
            match token.kind {
                TokenKind::String => {
                    self.advance()?;
                    values.push(Value::String(token.value));
                }
                TokenKind::Number => {
                    self.advance()?;
                    values.push(Value::Float(token.value.parse::<f64>().map_err(
                        |error| {
                            VrmlError::from(format!(
                                "Invalid numeric value {}: {error}",
                                token.value
                            ))
                        },
                    )?));
                }
                TokenKind::Identifier => {
                    self.advance()?;
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
        token.value.parse::<i32>().map_err(|error| {
            VrmlError::from(format!("Invalid integer value {}: {error}", token.value))
        })
    }

    /// Parse one float token.
    fn parse_float(&mut self, message: &str) -> Result<f64, VrmlError> {
        let token = self.consume(TokenKind::Number, message)?;
        token.value.parse::<f64>().map_err(|error| {
            VrmlError::from(format!("Invalid float value {}: {error}", token.value))
        })
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
        if !self.check_symbol("[")? {
            return self.parse_multi_numeric_item(arity, floats);
        }

        self.consume_symbol("[", "Expected '['")?;
        let mut values = Vec::new();
        while !self.check_symbol("]")? && !self.at_end()? {
            values.push(self.parse_multi_numeric_item(arity, floats)?);
        }
        self.consume_symbol("]", "Expected ']'")?;
        Ok(Value::List(values))
    }

    /// Parse one numeric item from a multi-value field.
    fn parse_multi_numeric_item(
        &mut self,
        arity: usize,
        floats: bool,
    ) -> Result<Value, VrmlError> {
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
        if self.check_symbol("[")? {
            self.consume_symbol("[", "Expected '['")?;
            while !self.check_symbol("]")? && !self.at_end()? {
                let token = self.peek()?;
                if !matches!(token.kind, TokenKind::String | TokenKind::Identifier) {
                    return Err(VrmlError::from(format!(
                        "Expected string value at line {}, column {}",
                        token.line, token.column
                    )));
                }
                self.advance()?;
                values.push(Value::String(token.value));
            }
            self.consume_symbol("]", "Expected ']'")?;
            return Ok(Value::List(values));
        }

        let token = self.peek()?;
        if !matches!(token.kind, TokenKind::String | TokenKind::Identifier) {
            return Err(VrmlError::from(format!(
                "Expected string value at line {}, column {}",
                token.line, token.column
            )));
        }
        self.advance()?;
        Ok(Value::List(vec![Value::String(token.value)]))
    }

    /// Consume one token of the expected kind.
    fn consume(&mut self, expected: TokenKind, message: &str) -> Result<Token, VrmlError> {
        let token = self.peek()?;
        if token.kind == expected {
            return self.advance();
        }
        Err(VrmlError::from(format!(
            "{message} at line {}, column {}",
            token.line, token.column
        )))
    }

    /// Consume one symbol token with the expected text.
    fn consume_symbol(&mut self, symbol: &str, message: &str) -> Result<(), VrmlError> {
        let token = self.peek()?;
        if token.kind == TokenKind::Symbol && token.value == symbol {
            self.advance()?;
            return Ok(());
        }
        Err(VrmlError::from(format!(
            "{message} at line {}, column {}",
            token.line, token.column
        )))
    }

    /// Match one identifier token with the expected value.
    fn match_identifier(&mut self, value: &str) -> Result<bool, VrmlError> {
        let token = self.peek()?;
        if token.kind == TokenKind::Identifier && token.value == value {
            self.advance()?;
            return Ok(true);
        }
        Ok(false)
    }

    /// Return whether the next token closes a node or list.
    fn check_symbol(&mut self, symbol: &str) -> Result<bool, VrmlError> {
        let token = self.peek()?;
        Ok(token.kind == TokenKind::Symbol && token.value == symbol)
    }

    /// Return whether the current token sequence looks like a child statement.
    fn looks_like_statement(&mut self) -> Result<bool, VrmlError> {
        Ok(self.peek_value("DEF")?
            || self.peek_value("USE")?
            || (self.peek()?.kind == TokenKind::Identifier && self.peek_next_symbol("{")?))
    }

    /// Return whether the current token matches the provided identifier text.
    fn peek_value(&mut self, value: &str) -> Result<bool, VrmlError> {
        let token = self.peek()?;
        Ok(token.kind == TokenKind::Identifier && token.value == value)
    }

    /// Return whether the next token after the current one is the provided symbol.
    fn peek_next_symbol(&mut self, value: &str) -> Result<bool, VrmlError> {
        let token = self.tokens.peek(1)?;
        Ok(token.kind == TokenKind::Symbol && token.value == value)
    }

    /// Return the current token.
    fn peek(&mut self) -> Result<Token, VrmlError> {
        self.tokens.peek(0)
    }

    /// Advance and return the current token.
    fn advance(&mut self) -> Result<Token, VrmlError> {
        self.tokens.advance()
    }

    /// Return whether the parser has consumed the entire token stream.
    fn at_end(&mut self) -> Result<bool, VrmlError> {
        Ok(self.peek()?.kind == TokenKind::Eof)
    }
}

/// Return the parse specification for one known node field.
fn field_kind(node_type: &str, field_name: &str) -> FieldKind {
    match (node_type, field_name) {
        ("PerspectiveCamera", "position") => FieldKind::Vec3,
        ("PerspectiveCamera", "orientation") => FieldKind::Rotation,
        ("PerspectiveCamera", "heightAngle") => FieldKind::Float,
        ("DirectionalLight", "on") => FieldKind::Bool,
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
fn convert_ast_node_to_out_node(node: AstNode) -> Result<OutNode, VrmlError> {
    let mut out_node = OutNode::new(node.node_type);
    out_node.def_name = node.def_name;
    out_node.fields = node.fields.into_iter().collect();
    for child in node.children {
        let value = match child {
            Statement::Node(child_node) => {
                Value::Node(Box::new(convert_ast_node_to_out_node(child_node)?))
            }
            Statement::Use(use_ref) => Value::Use(use_ref),
        };
        out_node.fields.push(("__child__".to_owned(), value));
    }
    Ok(out_node)
}
