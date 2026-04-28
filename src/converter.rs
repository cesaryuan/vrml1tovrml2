//! VRML 1.0 AST to VRML 2.0 conversion logic for the Rust migration.

use std::collections::HashMap;

use crate::error::VrmlError;
use crate::model::{AstNode, OutNode, Statement, UseRef, Value};

/// Convert parsed VRML 1.0 statements into VRML 2.0 output nodes.
pub fn convert(statements: &[Statement]) -> Result<Vec<OutNode>, VrmlError> {
    let mut converter = Converter::new();
    converter.convert(statements)
}

/// Hold reusable state while traversing a VRML 1.0 scene.
struct Converter {
    /// Track `DEF` names that have already been emitted.
    emitted_defs: HashMap<String, ()>,
    /// Reuse implicit materials across shapes with identical properties.
    generated_material_names: HashMap<String, String>,
    /// Allocate fresh implicit material names.
    generated_material_counter: usize,
}

/// Track one persistent transform operation from VRML 1.0 traversal.
#[derive(Clone, Debug)]
struct TransformSpec {
    /// Identify the transform field to emit.
    kind: TransformKind,
    /// Store the transform value.
    value: Value,
}

/// Enumerate supported persistent transform kinds.
#[derive(Clone, Debug)]
enum TransformKind {
    /// Translation transform.
    Translation,
}

/// Hold the current material state for geometry conversion.
#[derive(Clone, Debug)]
struct MaterialState {
    /// Ambient colors from the VRML 1.0 material.
    ambient_colors: Vec<Vec<f64>>,
    /// Diffuse colors from the VRML 1.0 material.
    diffuse_colors: Vec<Vec<f64>>,
    /// Specular colors from the VRML 1.0 material.
    specular_colors: Vec<Vec<f64>>,
    /// Emissive colors from the VRML 1.0 material.
    emissive_colors: Vec<Vec<f64>>,
    /// Shininess values from the VRML 1.0 material.
    shininess: Vec<f64>,
    /// Transparency values from the VRML 1.0 material.
    transparency: Vec<f64>,
}

/// Represent reusable definitions tracked during traversal.
#[derive(Clone, Debug)]
enum DefinitionValue {
    /// A material definition that affects future shapes.
    Material(MaterialState),
    /// A coordinate definition that affects future shapes.
    Coordinate(OutNode),
    /// A normal definition that affects future shapes.
    Normal(OutNode),
    /// A font style definition that affects future text.
    FontStyle(OutNode),
    /// A directly emitted output node name that can later be referenced by `USE`.
    Node,
}

/// Track inherited VRML 1.0 state while traversing statements.
#[derive(Clone, Debug, Default)]
struct ConversionState {
    /// Persistent transforms applied to future emitted nodes.
    transforms: Vec<TransformSpec>,
    /// Active material state.
    material: Option<MaterialRef>,
    /// Active material binding.
    material_binding: Option<String>,
    /// Active normal binding.
    normal_binding: Option<String>,
    /// Active shape hints.
    shape_hints: HashMap<String, Value>,
    /// Active coordinate node.
    coordinate: Option<NodeRef>,
    /// Active normal node.
    normal: Option<NodeRef>,
    /// Active font style node.
    font_style: Option<NodeRef>,
    /// Shared `DEF` definitions visible to later statements.
    definitions: HashMap<String, DefinitionValue>,
}

/// Refer to material data directly or through a named definition.
#[derive(Clone, Debug)]
enum MaterialRef {
    /// An inline material state.
    Inline(MaterialState),
    /// A named material definition.
    Defined(String, MaterialState),
}

/// Refer to reusable nodes directly or through a named definition.
#[derive(Clone, Debug)]
enum NodeRef {
    /// An inline node instance.
    Inline(OutNode),
    /// A named node definition.
    Defined(String, OutNode),
}

impl Converter {
    /// Create a converter with empty definition and material caches.
    fn new() -> Self {
        Self {
            emitted_defs: HashMap::new(),
            generated_material_names: HashMap::new(),
            generated_material_counter: 0,
        }
    }

    /// Convert root statements and wrap them like the existing Python implementation.
    fn convert(&mut self, statements: &[Statement]) -> Result<Vec<OutNode>, VrmlError> {
        let mut state = ConversionState::default();
        let mut nodes = self.convert_sequence(statements, &mut state)?;
        if !nodes.is_empty() {
            nodes = vec![self.wrap_root(nodes)];
        }
        Ok(nodes)
    }

    /// Wrap the scene in `Collision` and `Group` nodes like the Python converter.
    fn wrap_root(&self, nodes: Vec<OutNode>) -> OutNode {
        let group_children = if nodes.len() == 1 && nodes[0].node_type == "Group" && nodes[0].def_name.is_none() {
            nodes[0]
                .fields
                .iter()
                .find_map(|(name, value)| {
                    if name == "children" {
                        if let Value::List(values) = value {
                            return Some(
                                values
                                    .iter()
                                    .filter_map(|value| match value {
                                        Value::Node(node) => Some((**node).clone()),
                                        _ => None,
                                    })
                                    .collect::<Vec<_>>(),
                            );
                        }
                    }
                    None
                })
                .unwrap_or(nodes)
        } else {
            nodes
        };

        let mut group = OutNode::new("Group");
        group.fields.push(("children".to_owned(), Value::List(node_list(group_children))));

        let mut collision = OutNode::new("Collision");
        collision.fields.push(("collide".to_owned(), Value::Bool(false)));
        collision
            .fields
            .push(("children".to_owned(), Value::List(node_list(vec![group]))));
        collision
    }

    /// Convert a sequence of sibling statements under one inherited state.
    fn convert_sequence(
        &mut self,
        statements: &[Statement],
        state: &mut ConversionState,
    ) -> Result<Vec<OutNode>, VrmlError> {
        let mut emitted = Vec::new();
        for statement in statements {
            emitted.extend(self.convert_statement(statement, state)?);
        }
        Ok(emitted)
    }

    /// Convert one statement and return any emitted output nodes.
    fn convert_statement(
        &mut self,
        statement: &Statement,
        state: &mut ConversionState,
    ) -> Result<Vec<OutNode>, VrmlError> {
        match statement {
            Statement::Use(use_ref) => self.apply_use_reference(use_ref, state),
            Statement::Node(node) => self.convert_node(node, state),
        }
    }

    /// Convert one concrete VRML 1.0 node.
    fn convert_node(
        &mut self,
        node: &AstNode,
        state: &mut ConversionState,
    ) -> Result<Vec<OutNode>, VrmlError> {
        match node.node_type.as_str() {
            "Material" => {
                state.material = Some(self.convert_material_state(node, state)?);
                Ok(Vec::new())
            }
            "MaterialBinding" => {
                state.material_binding = self.enum_value(node, "value");
                Ok(Vec::new())
            }
            "NormalBinding" => {
                state.normal_binding = self.enum_value(node, "value");
                Ok(Vec::new())
            }
            "ShapeHints" => {
                state.shape_hints = node.fields.clone().into_iter().collect();
                Ok(Vec::new())
            }
            "Coordinate3" => {
                let out = self.simple_out_node(node, "Coordinate");
                state.coordinate = Some(self.register_node_definition(node, out, state));
                Ok(Vec::new())
            }
            "Normal" => {
                let out = self.simple_out_node(node, "Normal");
                state.normal = Some(self.register_node_definition(node, out, state));
                Ok(Vec::new())
            }
            "FontStyle" => {
                let out = self.convert_font_style(node);
                state.font_style = Some(self.register_node_definition(node, out, state));
                Ok(Vec::new())
            }
            "Translation" => {
                if let Some(value) = node.fields.get("translation") {
                    state.transforms.push(TransformSpec {
                        kind: TransformKind::Translation,
                        value: value.clone(),
                    });
                }
                Ok(Vec::new())
            }
            "Separator" | "Group" | "TransformSeparator" => self.convert_group_like(node, state),
            "PerspectiveCamera" => {
                let transforms = state.transforms.clone();
                let emitted = self.wrap_transforms(self.convert_camera(node), &transforms);
                Ok(vec![self.store_emitted_definition(emitted, state)])
            }
            "DirectionalLight" => {
                let transforms = state.transforms.clone();
                let emitted = self.wrap_transforms(self.convert_light(node), &transforms);
                Ok(vec![self.store_emitted_definition(emitted, state)])
            }
            "IndexedFaceSet" | "Cube" | "AsciiText" | "IndexedLineSet" | "PointSet" => {
                let transforms = state.transforms.clone();
                let shape = self.convert_shape(node, state)?;
                let emitted = self.wrap_transforms(shape, &transforms);
                Ok(vec![self.store_emitted_definition(emitted, state)])
            }
            other => Err(VrmlError::from(format!("Unsupported node type in Rust converter: {other}"))),
        }
    }

    /// Convert a grouping node while localizing inherited transforms inside the group.
    fn convert_group_like(
        &mut self,
        node: &AstNode,
        state: &mut ConversionState,
    ) -> Result<Vec<OutNode>, VrmlError> {
        let mut child_state = state.clone();
        child_state.transforms.clear();
        let children = self.convert_sequence(&node.children, &mut child_state)?;
        if children.is_empty() {
            return Ok(Vec::new());
        }

        if node.def_name.is_some() || children.len() > 1 {
            let mut group = OutNode::new("Group");
            group.def_name = node.def_name.clone();
            group
                .fields
                .push(("children".to_owned(), Value::List(node_list(children))));
            return Ok(vec![self.store_emitted_definition(
                self.wrap_transforms(group, &state.transforms),
                state,
            )]);
        }

        let child = children
            .into_iter()
            .next()
            .ok_or_else(|| VrmlError::from("Expected group child"))?;
        Ok(vec![self.store_emitted_definition(
            self.wrap_transforms(child, &state.transforms),
            state,
        )])
    }

    /// Convert a geometry node into a VRML 2.0 `Shape`.
    fn convert_shape(&mut self, node: &AstNode, state: &mut ConversionState) -> Result<OutNode, VrmlError> {
        let geometry = self.convert_geometry(node, state)?;
        let appearance = self.build_appearance(state);
        let mut shape = OutNode::new("Shape");
        shape.def_name = node.def_name.clone();
        shape
            .fields
            .push(("appearance".to_owned(), Value::Node(Box::new(appearance))));
        shape
            .fields
            .push(("geometry".to_owned(), Value::Node(Box::new(geometry))));
        Ok(shape)
    }

    /// Convert the geometry-specific part of one shape node.
    fn convert_geometry(&mut self, node: &AstNode, state: &mut ConversionState) -> Result<OutNode, VrmlError> {
        match node.node_type.as_str() {
            "IndexedFaceSet" => self.convert_indexed_face_set(node, state),
            "IndexedLineSet" => self.convert_indexed_line_set(node, state),
            "PointSet" => self.convert_point_set(node, state),
            "Cube" => {
                let mut out = OutNode::new("Box");
                out.fields.push((
                    "size".to_owned(),
                    Value::Vec(vec![
                        self.float_field(node, "width").unwrap_or(2.0),
                        self.float_field(node, "height").unwrap_or(2.0),
                        self.float_field(node, "depth").unwrap_or(2.0),
                    ]),
                ));
                Ok(out)
            }
            "AsciiText" => self.convert_ascii_text(node, state),
            other => Err(VrmlError::from(format!("Unsupported geometry node {other}"))),
        }
    }

    /// Convert a VRML 1.0 `IndexedFaceSet`.
    fn convert_indexed_face_set(
        &mut self,
        node: &AstNode,
        state: &mut ConversionState,
    ) -> Result<OutNode, VrmlError> {
        let mut out = OutNode::new("IndexedFaceSet");

        if let Some(coordinate) = &state.coordinate {
            out.fields
                .push(("coord".to_owned(), self.node_ref_to_value(coordinate)?));
        }
        if let Some(normal) = &state.normal {
            out.fields
                .push(("normal".to_owned(), self.node_ref_to_value(normal)?));
        }

        if let Some((color_node, color_index)) = self.build_color_node(state, node)? {
            out.fields
                .push(("color".to_owned(), Value::Node(Box::new(color_node))));
            if let Some(color_index) = color_index {
                out.fields
                    .push(("__pending_color_index__".to_owned(), Value::List(color_index)));
            }
        }

        if matches!(
            state.material_binding.as_deref().map(|value| value.to_ascii_uppercase()),
            Some(value) if matches!(value.as_str(), "PER_FACE" | "PER_FACE_INDEXED" | "PER_PART" | "PER_PART_INDEXED")
        ) {
            out.fields
                .push(("colorPerVertex".to_owned(), Value::Bool(false)));
        }
        if matches!(
            state.normal_binding.as_deref().map(|value| value.to_ascii_uppercase()),
            Some(value) if matches!(value.as_str(), "PER_FACE" | "PER_FACE_INDEXED" | "PER_PART" | "PER_PART_INDEXED")
        ) {
            out.fields
                .push(("normalPerVertex".to_owned(), Value::Bool(false)));
        }

        if self
            .enum_hint(&state.shape_hints, "shapeType")
            .map(|value| value.to_ascii_uppercase())
            .unwrap_or_default()
            != "SOLID"
        {
            out.fields.push(("solid".to_owned(), Value::Bool(false)));
        }
        if matches!(
            self.enum_hint(&state.shape_hints, "faceType")
                .map(|value| value.to_ascii_uppercase()),
            Some(value) if value != "CONVEX"
        ) {
            out.fields.push(("convex".to_owned(), Value::Bool(false)));
        }
        if matches!(
            self.enum_hint(&state.shape_hints, "vertexOrdering")
                .map(|value| value.to_ascii_uppercase()),
            Some(value) if value == "CLOCKWISE"
        ) {
            out.fields.push(("ccw".to_owned(), Value::Bool(false)));
        }

        let crease_angle = self
            .float_hint(&state.shape_hints, "creaseAngle")
            .unwrap_or(0.5);
        out.fields
            .push(("creaseAngle".to_owned(), Value::Float(crease_angle)));

        if let Some(value) = node.fields.get("coordIndex") {
            out.fields.push(("coordIndex".to_owned(), value.clone()));
        }
        if let Some(index) = take_pending_field(&mut out.fields, "__pending_color_index__") {
            out.fields.push(("colorIndex".to_owned(), index));
        }
        if let Some(value) = node.fields.get("normalIndex") {
            out.fields.push(("normalIndex".to_owned(), value.clone()));
        }
        if let Some(value) = node.fields.get("textureCoordIndex") {
            out.fields.push(("texCoordIndex".to_owned(), value.clone()));
        }

        Ok(out)
    }

    /// Convert a VRML 1.0 `IndexedLineSet`.
    fn convert_indexed_line_set(
        &mut self,
        node: &AstNode,
        state: &mut ConversionState,
    ) -> Result<OutNode, VrmlError> {
        let mut out = OutNode::new("IndexedLineSet");

        if let Some(coordinate) = &state.coordinate {
            out.fields
                .push(("coord".to_owned(), self.node_ref_to_value(coordinate)?));
        }

        if let Some((color_node, color_index)) = self.build_color_node(state, node)? {
            out.fields
                .push(("color".to_owned(), Value::Node(Box::new(color_node))));
            if let Some(color_index) = color_index {
                out.fields
                    .push(("__pending_color_index__".to_owned(), Value::List(color_index)));
            }
        }

        if matches!(
            state.material_binding.as_deref().map(|value| value.to_ascii_uppercase()),
            Some(value) if matches!(value.as_str(), "PER_FACE" | "PER_FACE_INDEXED" | "PER_PART" | "PER_PART_INDEXED")
        ) {
            out.fields
                .push(("colorPerVertex".to_owned(), Value::Bool(false)));
        }

        if let Some(value) = node.fields.get("coordIndex") {
            out.fields.push(("coordIndex".to_owned(), value.clone()));
        }
        if let Some(index) = take_pending_field(&mut out.fields, "__pending_color_index__") {
            out.fields.push(("colorIndex".to_owned(), index));
        }

        Ok(out)
    }

    /// Convert a VRML 1.0 `PointSet`.
    fn convert_point_set(&mut self, node: &AstNode, state: &mut ConversionState) -> Result<OutNode, VrmlError> {
        let mut out = OutNode::new("PointSet");

        if let Some(coordinate) = &state.coordinate {
            out.fields
                .push(("coord".to_owned(), self.slice_coordinate_value(coordinate, node)?));
        }

        if let Some((color_node, _)) = self.build_color_node(state, node)? {
            out.fields
                .push(("color".to_owned(), Value::Node(Box::new(color_node))));
        }

        Ok(out)
    }

    /// Convert a VRML 1.0 `AsciiText`.
    fn convert_ascii_text(&mut self, node: &AstNode, state: &mut ConversionState) -> Result<OutNode, VrmlError> {
        let mut out = OutNode::new("Text");
        out.fields.push((
            "string".to_owned(),
            self.value_to_string_list(node.fields.get("string")).unwrap_or(Value::List(Vec::new())),
        ));
        if let Some(width) = self.float_field(node, "width") {
            out.fields.push(("maxExtent".to_owned(), Value::Float(width)));
        }
        if let Some(font_style) = self.merge_font_style(state, node)? {
            out.fields
                .push(("fontStyle".to_owned(), Value::Node(Box::new(font_style))));
        }
        Ok(out)
    }

    /// Convert a VRML 1.0 camera node to a VRML 2.0 `Viewpoint`.
    fn convert_camera(&self, node: &AstNode) -> OutNode {
        let mut out = OutNode::new("Viewpoint");
        if let Some(value) = node.fields.get("position") {
            out.fields.push(("position".to_owned(), value.clone()));
        }
        if let Some(value) = node.fields.get("orientation") {
            out.fields.push(("orientation".to_owned(), value.clone()));
        }
        if let Some(value) = self.float_field(node, "heightAngle") {
            out.fields.push(("fieldOfView".to_owned(), Value::Float(value)));
        }
        out.def_name = node.def_name.clone();
        out
    }

    /// Convert a VRML 1.0 light node to its VRML 2.0 counterpart.
    fn convert_light(&self, node: &AstNode) -> OutNode {
        let mut out = OutNode::new("DirectionalLight");
        for field_name in ["on", "intensity", "color", "direction"] {
            if let Some(value) = node.fields.get(field_name) {
                out.fields.push((field_name.to_owned(), value.clone()));
            }
        }
        out.def_name = node.def_name.clone();
        out
    }

    /// Build a VRML 2.0 appearance from the currently active material state.
    fn build_appearance(&mut self, state: &ConversionState) -> OutNode {
        let mut out = OutNode::new("Appearance");
        let material = self.material_reference_to_output(state.material.as_ref());
        out.fields.push(("material".to_owned(), material));
        out
    }

    /// Convert the current material state into an emitted appearance field value.
    fn material_reference_to_output(&mut self, material_ref: Option<&MaterialRef>) -> Value {
        match material_ref {
            None => {
                if self.emitted_defs.contains_key("_DefMat") {
                    return Value::Use(UseRef {
                        name: "_DefMat".to_owned(),
                    });
                }
                self.emitted_defs.insert("_DefMat".to_owned(), ());
                let mut material = OutNode::new("Material");
                material.def_name = Some("_DefMat".to_owned());
                Value::Node(Box::new(material))
            }
            Some(MaterialRef::Defined(name, material)) => {
                if self.emitted_defs.contains_key(name) {
                    return Value::Use(UseRef { name: name.clone() });
                }
                self.emitted_defs.insert(name.clone(), ());
                let mut material_node = self.material_to_out_node(material);
                material_node.def_name = Some(name.clone());
                Value::Node(Box::new(material_node))
            }
            Some(MaterialRef::Inline(material)) => {
                let signature = self.material_signature(material);
                if let Some(existing) = self.generated_material_names.get(&signature) {
                    return Value::Use(UseRef {
                        name: existing.clone(),
                    });
                }
                let generated_name = format!("_v2%{}", self.generated_material_counter);
                self.generated_material_counter += 1;
                self.generated_material_names
                    .insert(signature, generated_name.clone());
                self.emitted_defs.insert(generated_name.clone(), ());
                let mut material_node = self.material_to_out_node(material);
                material_node.def_name = Some(generated_name);
                Value::Node(Box::new(material_node))
            }
        }
    }

    /// Convert one material state into a VRML 2.0 `Material` node.
    fn material_to_out_node(&self, material: &MaterialState) -> OutNode {
        let mut out = OutNode::new("Material");

        let ambient = material
            .ambient_colors
            .first()
            .cloned()
            .unwrap_or_else(|| vec![0.2, 0.2, 0.2]);
        let diffuse = material
            .diffuse_colors
            .first()
            .cloned()
            .unwrap_or_else(|| vec![0.8, 0.8, 0.8]);
        let ambient_intensity = (ambient.iter().sum::<f64>() / 3.0).clamp(0.0, 1.0);

        if (ambient_intensity - 0.2).abs() > 1e-9 {
            out.fields
                .push(("ambientIntensity".to_owned(), Value::Float(ambient_intensity)));
        }
        if diffuse != vec![0.8, 0.8, 0.8] {
            out.fields.push(("diffuseColor".to_owned(), Value::Vec(diffuse)));
        }
        if let Some(specular) = material.specular_colors.first() {
            if *specular != vec![0.0, 0.0, 0.0] {
                out.fields
                    .push(("specularColor".to_owned(), Value::Vec(specular.clone())));
            }
        }
        if let Some(emissive) = material.emissive_colors.first() {
            if *emissive != vec![0.0, 0.0, 0.0] {
                out.fields
                    .push(("emissiveColor".to_owned(), Value::Vec(emissive.clone())));
            }
        }
        if let Some(shininess) = material.shininess.first() {
            if (*shininess - 0.2).abs() > 1e-9 {
                out.fields
                    .push(("shininess".to_owned(), Value::Float(*shininess)));
            }
        }
        if let Some(transparency) = material.transparency.first() {
            if (*transparency - 0.0).abs() > 1e-9 {
                out.fields
                    .push(("transparency".to_owned(), Value::Float(*transparency)));
            }
        }

        out
    }

    /// Build a color node and optional color index from the active material.
    fn build_color_node(
        &self,
        state: &ConversionState,
        geometry_node: &AstNode,
    ) -> Result<Option<(OutNode, Option<Vec<Value>>)>, VrmlError> {
        let material = match state.material.as_ref() {
            Some(MaterialRef::Inline(material)) => material,
            Some(MaterialRef::Defined(_, material)) => material,
            None => return Ok(None),
        };

        if material.diffuse_colors.is_empty() {
            return Ok(None);
        }
        if material.diffuse_colors.len() == 1
            && !matches!(geometry_node.node_type.as_str(), "IndexedLineSet" | "PointSet")
        {
            return Ok(None);
        }

        let mut color = OutNode::new("Color");
        color.fields.push((
            "color".to_owned(),
            Value::List(
                material
                    .diffuse_colors
                    .iter()
                    .cloned()
                    .map(Value::Vec)
                    .collect(),
            ),
        ));

        let color_index = geometry_node
            .fields
            .get("materialIndex")
            .and_then(|value| match value {
                Value::List(values) => Some(values.clone()),
                _ => None,
            });

        Ok(Some((color, color_index)))
    }

    /// Convert a source material node into reusable material state.
    fn convert_material_state(
        &self,
        node: &AstNode,
        state: &mut ConversionState,
    ) -> Result<MaterialRef, VrmlError> {
        let material = MaterialState {
            ambient_colors: self.ensure_color_list(node.fields.get("ambientColor"), &[0.2, 0.2, 0.2])?,
            diffuse_colors: self.ensure_color_list(node.fields.get("diffuseColor"), &[0.8, 0.8, 0.8])?,
            specular_colors: self.ensure_color_list(node.fields.get("specularColor"), &[0.0, 0.0, 0.0])?,
            emissive_colors: self.ensure_color_list(node.fields.get("emissiveColor"), &[0.0, 0.0, 0.0])?,
            shininess: self.ensure_float_list(node.fields.get("shininess"), 0.2)?,
            transparency: self.ensure_float_list(node.fields.get("transparency"), 0.0)?,
        };

        if let Some(def_name) = &node.def_name {
            state
                .definitions
                .insert(def_name.clone(), DefinitionValue::Material(material.clone()));
            return Ok(MaterialRef::Defined(def_name.clone(), material));
        }

        Ok(MaterialRef::Inline(material))
    }

    /// Convert a VRML 1.0 `FontStyle` helper node.
    fn convert_font_style(&self, node: &AstNode) -> OutNode {
        let mut out = OutNode::new("FontStyle");
        if let Some(size) = self.float_field(node, "size") {
            out.fields.push(("size".to_owned(), Value::Float(size)));
        }
        if let Some(family) = self.enum_value(node, "family") {
            let mapped = match family.to_ascii_uppercase().as_str() {
                "SERIF" => "SERIF",
                "TYPEWRITER" => "TYPEWRITER",
                _ => "SANS",
            };
            out.fields.push((
                "family".to_owned(),
                Value::List(vec![Value::String(mapped.to_owned())]),
            ));
        }
        if let Some(style) = self.enum_value(node, "style") {
            out.fields.push((
                "style".to_owned(),
                Value::String(style.to_ascii_uppercase().replace('_', " ")),
            ));
        }
        out.def_name = node.def_name.clone();
        out
    }

    /// Merge inherited font style state with `AsciiText` overrides.
    fn merge_font_style(
        &mut self,
        state: &ConversionState,
        text_node: &AstNode,
    ) -> Result<Option<OutNode>, VrmlError> {
        let mut merged = OutNode::new("FontStyle");

        if let Some(font_style_ref) = &state.font_style {
            let source = self.materialize_node_reference(font_style_ref)?;
            merged.fields.extend(source.fields);
        }

        if let Some(spacing) = self.float_field(text_node, "spacing") {
            merged.fields.push(("spacing".to_owned(), Value::Float(spacing)));
        }
        if let Some(justification) = self.enum_value(text_node, "justification") {
            let mapped = match justification.to_ascii_uppercase().as_str() {
                "LEFT" => "BEGIN",
                "CENTER" => "MIDDLE",
                "RIGHT" => "END",
                _ => "BEGIN",
            };
            merged.fields.push((
                "justify".to_owned(),
                Value::List(vec![Value::String(mapped.to_owned())]),
            ));
        }

        if merged.fields.is_empty() {
            return Ok(None);
        }

        Ok(Some(merged))
    }

    /// Slice coordinates for `PointSet startIndex/numPoints`.
    fn slice_coordinate_value(&mut self, coordinate: &NodeRef, point_set: &AstNode) -> Result<Value, VrmlError> {
        if let NodeRef::Defined(name, _) = coordinate {
            return Ok(Value::Use(UseRef { name: name.clone() }));
        }
        let source = self.materialize_node_reference(coordinate)?;
        let point_values = source
            .fields
            .iter()
            .find_map(|(name, value)| if name == "point" { Some(value.clone()) } else { None })
            .unwrap_or(Value::List(Vec::new()));

        let points = match point_values {
            Value::List(values) => values,
            _ => Vec::new(),
        };
        let start_index = self.int_field(point_set, "startIndex").unwrap_or(0).max(0) as usize;
        let num_points = self
            .int_field(point_set, "numPoints")
            .unwrap_or((points.len().saturating_sub(start_index)) as i32)
            .max(0) as usize;
        let sliced = points
            .into_iter()
            .skip(start_index)
            .take(num_points)
            .collect::<Vec<_>>();

        let mut out = OutNode::new("Coordinate");
        out.fields.push(("point".to_owned(), Value::List(sliced)));
        Ok(Value::Node(Box::new(out)))
    }

    /// Wrap one emitted node in the currently active transforms.
    fn wrap_transforms(&self, mut node: OutNode, transforms: &[TransformSpec]) -> OutNode {
        if transforms.is_empty() {
            return node;
        }

        let def_name = node.def_name.take();
        let mut wrapped = node;
        for transform in transforms.iter().rev() {
            match transform.kind {
                TransformKind::Translation => {
                    let mut out = OutNode::new("Transform");
                    out.fields.push(("translation".to_owned(), transform.value.clone()));
                    out.fields.push((
                        "children".to_owned(),
                        Value::List(vec![Value::Node(Box::new(wrapped))]),
                    ));
                    wrapped = out;
                }
            }
        }
        wrapped.def_name = def_name;
        wrapped
    }

    /// Register a reusable node definition and return the corresponding node reference.
    fn register_node_definition(
        &self,
        node: &AstNode,
        value: OutNode,
        state: &mut ConversionState,
    ) -> NodeRef {
        if let Some(def_name) = &node.def_name {
            match value.node_type.as_str() {
                "Coordinate" => {
                    state
                        .definitions
                        .insert(def_name.clone(), DefinitionValue::Coordinate(value.clone()));
                }
                "Normal" => {
                    state
                        .definitions
                        .insert(def_name.clone(), DefinitionValue::Normal(value.clone()));
                }
                "FontStyle" => {
                    state
                        .definitions
                        .insert(def_name.clone(), DefinitionValue::FontStyle(value.clone()));
                }
                _ => {
                    state
                        .definitions
                        .insert(def_name.clone(), DefinitionValue::Node);
                }
            }
            return NodeRef::Defined(def_name.clone(), value);
        }
        NodeRef::Inline(value)
    }

    /// Store emitted node definitions so later `USE` statements can reference them.
    fn store_emitted_definition(&self, node: OutNode, state: &mut ConversionState) -> OutNode {
        if let Some(def_name) = &node.def_name {
            state.definitions.insert(def_name.clone(), DefinitionValue::Node);
        }
        node
    }

    /// Apply a `USE` reference either to inherited state or as an emitted output node.
    fn apply_use_reference(
        &mut self,
        use_ref: &UseRef,
        state: &mut ConversionState,
    ) -> Result<Vec<OutNode>, VrmlError> {
        let Some(resolved) = state.definitions.get(&use_ref.name).cloned() else {
            return Ok(Vec::new());
        };

        match resolved {
            DefinitionValue::Material(material) => {
                state.material = Some(MaterialRef::Defined(use_ref.name.clone(), material));
                Ok(Vec::new())
            }
            DefinitionValue::Coordinate(node) => {
                state.coordinate = Some(NodeRef::Defined(use_ref.name.clone(), node));
                Ok(Vec::new())
            }
            DefinitionValue::Normal(node) => {
                state.normal = Some(NodeRef::Defined(use_ref.name.clone(), node));
                Ok(Vec::new())
            }
            DefinitionValue::FontStyle(node) => {
                state.font_style = Some(NodeRef::Defined(use_ref.name.clone(), node));
                Ok(Vec::new())
            }
            DefinitionValue::Node => {
                let mut out = OutNode::new("Transform");
                out.fields.push((
                    "children".to_owned(),
                    Value::List(vec![Value::Use(UseRef {
                        name: use_ref.name.clone(),
                    })]),
                ));
                Ok(vec![out])
            }
        }
    }

    /// Materialize a reusable node reference and emit `USE` on later references.
    fn materialize_node_reference(&mut self, node_ref: &NodeRef) -> Result<OutNode, VrmlError> {
        match node_ref {
            NodeRef::Inline(node) => Ok(node.clone()),
            NodeRef::Defined(name, node) => {
                self.emitted_defs.insert(name.clone(), ());
                let mut node = node.clone();
                node.def_name = Some(name.clone());
                Ok(node)
            }
        }
    }

    /// Convert a reusable node reference into either an inline node or a field-level `USE`.
    fn node_ref_to_value(&mut self, node_ref: &NodeRef) -> Result<Value, VrmlError> {
        match node_ref {
            NodeRef::Inline(node) => Ok(Value::Node(Box::new(node.clone()))),
            NodeRef::Defined(name, node) => {
                if self.emitted_defs.contains_key(name) {
                    return Ok(Value::Use(UseRef { name: name.clone() }));
                }
                let mut node = node.clone();
                node.def_name = Some(name.clone());
                self.emitted_defs.insert(name.clone(), ());
                Ok(Value::Node(Box::new(node)))
            }
        }
    }

    /// Convert a parsed source node to a renamed output node with the same fields.
    fn simple_out_node(&self, node: &AstNode, new_type: &str) -> OutNode {
        let mut out = OutNode::new(new_type);
        out.fields = node
            .fields
            .iter()
            .map(|(name, value)| (name.clone(), value.clone()))
            .collect();
        out.def_name = node.def_name.clone();
        out
    }

    /// Build a stable signature string for implicit material reuse.
    fn material_signature(&self, material: &MaterialState) -> String {
        format!(
            "{:?}|{:?}|{:?}|{:?}|{:?}|{:?}",
            material.ambient_colors,
            material.diffuse_colors,
            material.specular_colors,
            material.emissive_colors,
            material.shininess,
            material.transparency
        )
    }

    /// Normalize a parsed color field into a list of RGB vectors.
    fn ensure_color_list(&self, value: Option<&Value>, default: &[f64]) -> Result<Vec<Vec<f64>>, VrmlError> {
        match value {
            None => Ok(vec![default.to_vec()]),
            Some(Value::Vec(values)) => Ok(vec![values.clone()]),
            Some(Value::List(values)) => values
                .iter()
                .map(|value| match value {
                    Value::Vec(values) => Ok(values.clone()),
                    _ => Err(VrmlError::from("Expected color vector list")),
                })
                .collect(),
            _ => Err(VrmlError::from("Expected color vector value")),
        }
    }

    /// Normalize a parsed scalar field into a list of floats.
    fn ensure_float_list(&self, value: Option<&Value>, default: f64) -> Result<Vec<f64>, VrmlError> {
        match value {
            None => Ok(vec![default]),
            Some(Value::Float(value)) => Ok(vec![*value]),
            Some(Value::Int(value)) => Ok(vec![*value as f64]),
            Some(Value::List(values)) => values
                .iter()
                .map(|value| match value {
                    Value::Float(value) => Ok(*value),
                    Value::Int(value) => Ok(*value as f64),
                    _ => Err(VrmlError::from("Expected float list")),
                })
                .collect(),
            _ => Err(VrmlError::from("Expected float value")),
        }
    }

    /// Convert a parsed string or list field into a writer-ready string list.
    fn value_to_string_list(&self, value: Option<&Value>) -> Option<Value> {
        match value {
            None => None,
            Some(Value::String(value)) => Some(Value::List(vec![Value::String(value.clone())])),
            Some(Value::Identifier(value)) => Some(Value::List(vec![Value::String(value.clone())])),
            Some(Value::List(values)) => Some(Value::List(
                values
                    .iter()
                    .filter_map(|value| match value {
                        Value::String(value) => Some(Value::String(value.clone())),
                        Value::Identifier(value) => Some(Value::String(value.clone())),
                        _ => None,
                    })
                    .collect(),
            )),
            _ => None,
        }
    }

    /// Read a float field from a parsed node when present.
    fn float_field(&self, node: &AstNode, field_name: &str) -> Option<f64> {
        self.float_value(node.fields.get(field_name))
    }

    /// Read an integer field from a parsed node when present.
    fn int_field(&self, node: &AstNode, field_name: &str) -> Option<i32> {
        match node.fields.get(field_name) {
            Some(Value::Int(value)) => Some(*value),
            Some(Value::Float(value)) => Some(*value as i32),
            _ => None,
        }
    }

    /// Read an enum-like identifier field from a parsed node when present.
    fn enum_value(&self, node: &AstNode, field_name: &str) -> Option<String> {
        match node.fields.get(field_name) {
            Some(Value::Identifier(value)) => Some(value.clone()),
            Some(Value::String(value)) => Some(value.clone()),
            _ => None,
        }
    }

    /// Read a float-like value from an arbitrary parsed value.
    fn float_value(&self, value: Option<&Value>) -> Option<f64> {
        match value {
            Some(Value::Float(value)) => Some(*value),
            Some(Value::Int(value)) => Some(*value as f64),
            Some(Value::List(values)) if values.len() == 1 => self.float_value(values.first()),
            _ => None,
        }
    }

    /// Read an enum hint from the active shape hints map.
    fn enum_hint(&self, hints: &HashMap<String, Value>, field_name: &str) -> Option<String> {
        match hints.get(field_name) {
            Some(Value::Identifier(value)) => Some(value.clone()),
            Some(Value::String(value)) => Some(value.clone()),
            _ => None,
        }
    }

    /// Read a float hint from the active shape hints map.
    fn float_hint(&self, hints: &HashMap<String, Value>, field_name: &str) -> Option<f64> {
        self.float_value(hints.get(field_name))
    }
}

/// Convert a vector of nodes into a `Value::List` payload.
fn node_list(nodes: Vec<OutNode>) -> Vec<Value> {
    nodes
        .into_iter()
        .map(|node| Value::Node(Box::new(node)))
        .collect()
}

/// Remove and return one temporary field value that should be emitted later.
fn take_pending_field(fields: &mut Vec<(String, Value)>, field_name: &str) -> Option<Value> {
    let index = fields.iter().position(|(name, _)| name == field_name)?;
    Some(fields.remove(index).1)
}
