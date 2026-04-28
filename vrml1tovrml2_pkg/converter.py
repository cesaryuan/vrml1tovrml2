"""VRML 1.0 AST to VRML 2.0 node conversion."""

from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any

from .common import (
    AstNode,
    ConversionState,
    DefinitionRef,
    LOGGER,
    MaterialState,
    OutNode,
    SpoolSequence,
    TransformSpec,
    UseRef,
    VrmlError,
)


class VrmlConverter:
    """Convert parsed VRML 1.0 nodes into VRML 2.0 nodes."""

    def convert(self, statements: list[Any]) -> list[OutNode]:
        """Convert the root statement list into VRML 2.0 output nodes."""

        LOGGER.info("Converting parsed scene graph to VRML 2.0")
        self.emitted_defs: set[str] = set()
        self.generated_material_names: dict[tuple[Any, ...], str] = {}
        self.generated_material_counter = 0
        state = ConversionState()
        nodes = self._convert_sequence(statements, state)
        if nodes:
            nodes = [self._wrap_root(nodes)]
        LOGGER.info("Generated %d VRML 2.0 top-level nodes", len(nodes))
        return nodes

    def _wrap_root(self, nodes: list[OutNode]) -> OutNode:
        """Wrap the scene in the same root structure used by the original converter."""

        group_children = nodes
        if len(nodes) == 1 and nodes[0].node_type == "Group" and nodes[0].def_name is None:
            first_fields = dict(nodes[0].fields)
            children_value = first_fields.get("children")
            if isinstance(children_value, list):
                group_children = children_value
        group = OutNode("Group", fields=[("children", group_children)])
        return OutNode("Collision", fields=[("collide", False), ("children", [group])])

    def _convert_sequence(self, statements: list[Any], state: ConversionState) -> list[OutNode]:
        """Convert one ordered list of statements under the same traversal state."""

        emitted: list[OutNode] = []
        for statement in statements:
            emitted.extend(self._convert_statement(statement, state))
        return emitted

    def _convert_statement(self, statement: Any, state: ConversionState) -> list[OutNode]:
        """Convert one statement and return any emitted VRML 2.0 nodes."""

        if isinstance(statement, UseRef):
            return self._apply_use_reference(statement, state)
        node: AstNode = statement
        LOGGER.debug("Visiting node %s", node.node_type)
        if node.node_type == "Material":
            state.material = self._convert_material_state(node, state)
            return []
        if node.node_type == "MaterialBinding":
            state.material_binding = str(node.fields.get("value", "OVERALL"))
            return []
        if node.node_type == "NormalBinding":
            state.normal_binding = str(node.fields.get("value", "PER_VERTEX_INDEXED"))
            return []
        if node.node_type == "ShapeHints":
            state.shape_hints = dict(node.fields)
            return []
        if node.node_type == "Coordinate3":
            state.coordinate = self._register_definition(node, self._simple_out_node(node, "Coordinate"), state)
            return []
        if node.node_type == "Normal":
            state.normal = self._register_definition(node, self._simple_out_node(node, "Normal"), state)
            return []
        if node.node_type == "TextureCoordinate2":
            state.tex_coord = self._register_definition(node, self._simple_out_node(node, "TextureCoordinate"), state)
            return []
        if node.node_type == "Texture2":
            state.texture = self._register_definition(node, self._convert_texture(node), state)
            return []
        if node.node_type in {"Texture2Transform", "Texture2Transformation"}:
            state.texture_transform = self._register_definition(node, self._convert_texture_transform(node), state)
            return []
        if node.node_type == "FontStyle":
            state.font_style = self._register_definition(node, self._convert_font_style(node), state)
            return []
        if node.node_type in {"Translation", "Rotation", "Scale", "MatrixTransform", "Transform"}:
            self._apply_transform(node, state)
            return []
        if node.node_type in {"Separator", "TransformSeparator", "Group"}:
            return self._convert_group_like(node, state)
        if node.node_type == "Switch":
            return [self._wrap_transforms(self._convert_switch(node, state), state.transforms)]
        if node.node_type == "WWWAnchor":
            return [self._wrap_transforms(self._convert_anchor(node, state), state.transforms)]
        if node.node_type == "WWWInline":
            return [self._wrap_transforms(self._convert_inline(node), state.transforms)]
        if node.node_type == "LOD":
            return [self._wrap_transforms(self._convert_lod(node, state), state.transforms)]
        if node.node_type in {"IndexedFaceSet", "IndexedLineSet", "PointSet", "Cube", "Cone", "Cylinder", "Sphere", "AsciiText"}:
            return [self._wrap_transforms(self._convert_shape(node, state), state.transforms)]
        if node.node_type in {"DirectionalLight", "PointLight", "SpotLight"}:
            return [self._store_emitted_definition(self._wrap_transforms(self._convert_light(node), state.transforms), state)]
        if node.node_type in {"PerspectiveCamera", "OrthographicCamera"}:
            return [self._store_emitted_definition(self._wrap_transforms(self._convert_camera(node), state.transforms), state)]
        LOGGER.warning("Skipping unsupported node type %s", node.node_type)
        return []

    def _convert_group_like(self, node: AstNode, state: ConversionState) -> list[OutNode]:
        """Convert a grouping node while keeping its inherited state local."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        if not children:
            return []
        if node.def_name or len(children) > 1:
            group = OutNode("Group", fields=[("children", children)], def_name=node.def_name)
            return [self._store_emitted_definition(self._wrap_transforms(group, state.transforms), state)]
        if node.def_name:
            children[0].def_name = node.def_name
        return [self._store_emitted_definition(self._wrap_transforms(children[0], state.transforms), state)]

    def _convert_switch(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 Switch node."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        which_child = int(node.fields.get("whichChild", -1))
        return OutNode("Switch", fields=[("whichChoice", which_child), ("choice", children)], def_name=node.def_name)

    def _convert_anchor(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 WWWAnchor node into a VRML 2.0 Anchor."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        url_values = self._to_string_list(node.fields.get("name", []))
        description = node.fields.get("description")
        fields: list[tuple[str, Any]] = [("url", url_values)]
        if description:
            fields.append(("description", str(description)))
        fields.append(("children", children))
        return OutNode("Anchor", fields=fields, def_name=node.def_name)

    def _convert_inline(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 WWWInline node into an Inline node."""

        url_values = self._to_string_list(node.fields.get("name", []))
        fields: list[tuple[str, Any]] = [("url", url_values)]
        if "bboxCenter" in node.fields:
            fields.append(("bboxCenter", node.fields["bboxCenter"]))
        if "bboxSize" in node.fields:
            fields.append(("bboxSize", node.fields["bboxSize"]))
        return OutNode("Inline", fields=fields, def_name=node.def_name)

    def _convert_lod(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 LOD node into a VRML 2.0 LOD."""

        child_state = state.clone()
        child_state.transforms = []
        children = self._convert_sequence(node.children, child_state)
        fields: list[tuple[str, Any]] = [("level", children)]
        if "range" in node.fields:
            fields.append(("range", node.fields["range"]))
        return OutNode("LOD", fields=fields, def_name=node.def_name)

    def _convert_shape(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert one VRML 1.0 geometry node into a VRML 2.0 Shape."""

        geometry = self._convert_geometry(node, state)
        appearance = self._build_appearance(state, node)
        fields: list[tuple[str, Any]] = []
        if appearance is not None:
            fields.append(("appearance", appearance))
        fields.append(("geometry", geometry))
        return OutNode("Shape", fields=fields, def_name=node.def_name)

    def _convert_geometry(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert the geometry-specific portion of a shape node."""

        if node.node_type == "IndexedFaceSet":
            return self._convert_indexed_face_set(node, state)
        if node.node_type == "IndexedLineSet":
            return self._convert_indexed_line_set(node, state)
        if node.node_type == "PointSet":
            return self._convert_point_set(node, state)
        if node.node_type == "Cube":
            size = (
                float(node.fields.get("width", 2.0)),
                float(node.fields.get("height", 2.0)),
                float(node.fields.get("depth", 2.0)),
            )
            return OutNode("Box", fields=[("size", size)])
        if node.node_type == "Cone":
            parts = {part.upper() for part in self._to_string_list(node.fields.get("parts", ["ALL"]))}
            fields: list[tuple[str, Any]] = [
                ("bottomRadius", float(node.fields.get("bottomRadius", 1.0))),
                ("height", float(node.fields.get("height", 2.0))),
            ]
            if "BOTTOM" not in parts and "ALL" not in parts:
                fields.append(("bottom", False))
            if "SIDES" not in parts and "ALL" not in parts:
                fields.append(("side", False))
            return OutNode("Cone", fields=fields)
        if node.node_type == "Cylinder":
            parts = {part.upper() for part in self._to_string_list(node.fields.get("parts", ["ALL"]))}
            fields = [("radius", float(node.fields.get("radius", 1.0))), ("height", float(node.fields.get("height", 2.0)))]
            if "BOTTOM" not in parts and "ALL" not in parts:
                fields.append(("bottom", False))
            if "TOP" not in parts and "ALL" not in parts:
                fields.append(("top", False))
            if "SIDES" not in parts and "ALL" not in parts:
                fields.append(("side", False))
            return OutNode("Cylinder", fields=fields)
        if node.node_type == "Sphere":
            return OutNode("Sphere", fields=[("radius", float(node.fields.get("radius", 1.0)))])
        if node.node_type == "AsciiText":
            return self._convert_ascii_text(node, state)
        raise VrmlError(f"Unsupported geometry node {node.node_type}")

    def _convert_indexed_face_set(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 IndexedFaceSet into VRML 2.0 IndexedFaceSet."""

        fields: list[tuple[str, Any]] = []
        if state.coordinate is not None:
            fields.append(("coord", self._materialize_reference(state.coordinate)))
        if state.normal is not None:
            fields.append(("normal", self._materialize_reference(state.normal)))
        if state.tex_coord is not None:
            fields.append(("texCoord", self._materialize_reference(state.tex_coord)))
        color_node, color_index = self._build_color_node(state, node)
        if color_node is not None:
            fields.append(("color", color_node))
        trailing_fields: list[tuple[str, Any]] = []
        material_binding = (state.material_binding or "OVERALL").upper()
        normal_binding = (state.normal_binding or "PER_VERTEX_INDEXED").upper()
        if material_binding in {"PER_FACE", "PER_FACE_INDEXED", "PER_PART", "PER_PART_INDEXED"}:
            trailing_fields.append(("colorPerVertex", False))
        if normal_binding in {"PER_FACE", "PER_FACE_INDEXED", "PER_PART", "PER_PART_INDEXED"}:
            trailing_fields.append(("normalPerVertex", False))
        shape_hints = state.shape_hints
        if shape_hints.get("vertexOrdering", "").upper() == "CLOCKWISE":
            trailing_fields.append(("ccw", False))
        if shape_hints.get("shapeType", "").upper() != "SOLID":
            trailing_fields.append(("solid", False))
        if shape_hints.get("faceType") and shape_hints.get("faceType", "").upper() != "CONVEX":
            trailing_fields.append(("convex", False))
        trailing_fields.append(("creaseAngle", float(shape_hints.get("creaseAngle", 0.5))))
        trailing_fields.append(("coordIndex", self._clone_sequence(node.fields.get("coordIndex", []))))
        if color_index:
            trailing_fields.append(("colorIndex", color_index))
        if "normalIndex" in node.fields:
            trailing_fields.append(("normalIndex", self._clone_sequence(node.fields["normalIndex"])))
        if "textureCoordIndex" in node.fields:
            trailing_fields.append(("texCoordIndex", self._clone_sequence(node.fields["textureCoordIndex"])))
        fields.extend(trailing_fields)
        return OutNode("IndexedFaceSet", fields=fields)

    def _convert_indexed_line_set(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 IndexedLineSet into VRML 2.0 IndexedLineSet."""

        fields: list[tuple[str, Any]] = []
        if state.coordinate is not None:
            fields.append(("coord", self._materialize_reference(state.coordinate)))
        color_node, color_index = self._build_color_node(state, node)
        if color_node is not None:
            fields.append(("color", color_node))
        material_binding = (state.material_binding or "OVERALL").upper()
        if material_binding in {"PER_FACE", "PER_FACE_INDEXED", "PER_PART", "PER_PART_INDEXED"}:
            fields.append(("colorPerVertex", False))
        fields.append(("coordIndex", self._clone_sequence(node.fields.get("coordIndex", []))))
        if color_index:
            fields.append(("colorIndex", color_index))
        return OutNode("IndexedLineSet", fields=fields)

    def _convert_point_set(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert a VRML 1.0 PointSet while honoring startIndex and numPoints."""

        fields: list[tuple[str, Any]] = []
        coordinate = self._slice_coordinate_node(state.coordinate, node.fields)
        if coordinate is not None:
            fields.append(("coord", coordinate))
        color_node, _ = self._build_color_node(state, node)
        if color_node is not None:
            fields.append(("color", color_node))
        return OutNode("PointSet", fields=fields)

    def _convert_ascii_text(self, node: AstNode, state: ConversionState) -> OutNode:
        """Convert VRML 1.0 AsciiText into a VRML 2.0 Text node."""

        string_values = self._to_string_list(node.fields.get("string", []))
        font_style = self._merge_font_style(state.font_style, node.fields)
        text_fields: list[tuple[str, Any]] = [("string", string_values)]
        width = node.fields.get("width")
        if width is not None:
            text_fields.append(("maxExtent", float(width)))
        if font_style is not None:
            text_fields.append(("fontStyle", font_style))
        return OutNode("Text", fields=text_fields)

    def _convert_light(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 light node to its VRML 2.0 equivalent."""

        field_names = {
            "DirectionalLight": ["on", "intensity", "color", "direction"],
            "PointLight": ["on", "intensity", "color", "location"],
            "SpotLight": ["on", "intensity", "color", "location", "direction", "dropOffRate", "cutOffAngle"],
        }[node.node_type]
        fields = [(name, node.fields[name]) for name in field_names if name in node.fields]
        return OutNode(node.node_type, fields=fields, def_name=node.def_name)

    def _convert_camera(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 camera node to a VRML 2.0 Viewpoint."""

        fields: list[tuple[str, Any]] = []
        if "position" in node.fields:
            fields.append(("position", node.fields["position"]))
        if "orientation" in node.fields:
            fields.append(("orientation", node.fields["orientation"]))
        if node.node_type == "PerspectiveCamera" and "heightAngle" in node.fields:
            fields.append(("fieldOfView", float(node.fields["heightAngle"])))
        return OutNode("Viewpoint", fields=fields, def_name=node.def_name)

    def _apply_transform(self, node: AstNode, state: ConversionState) -> None:
        """Update the persistent transform state after a transform node."""

        if node.node_type == "Translation":
            state.transforms.append(TransformSpec("translation", node.fields.get("translation", (0.0, 0.0, 0.0))))
            return
        if node.node_type == "Rotation":
            state.transforms.append(TransformSpec("rotation", node.fields.get("rotation", (0.0, 0.0, 1.0, 0.0))))
            return
        if node.node_type == "Scale":
            state.transforms.append(TransformSpec("scale", node.fields.get("scaleFactor", (1.0, 1.0, 1.0))))
            return
        if node.node_type == "MatrixTransform":
            matrix = node.fields.get("matrix")
            if matrix:
                state.transforms.append(TransformSpec("matrix", matrix))
            return
        if node.node_type == "Transform":
            if "translation" in node.fields:
                state.transforms.append(TransformSpec("translation", node.fields["translation"]))
            if "rotation" in node.fields:
                state.transforms.append(TransformSpec("rotation", node.fields["rotation"]))
            if "scaleFactor" in node.fields:
                state.transforms.append(TransformSpec("scale", node.fields["scaleFactor"]))

    def _build_appearance(self, state: ConversionState, geometry_node: AstNode) -> OutNode | None:
        """Build a VRML 2.0 Appearance node from the current render state."""

        appearance_fields: list[tuple[str, Any]] = []
        material = self._resolve_material(state.material, state)
        appearance_fields.append(("material", self._material_reference_to_output(state.material, material)))
        if state.texture is not None:
            appearance_fields.append(("texture", self._materialize_reference(state.texture)))
        if state.texture_transform is not None:
            appearance_fields.append(("textureTransform", self._materialize_reference(state.texture_transform)))
        return OutNode("Appearance", fields=appearance_fields)

    def _build_color_node(self, state: ConversionState, geometry_node: AstNode) -> tuple[OutNode | None, list[int] | None]:
        """Extract diffuse colors from the current material when they drive geometry color."""

        material = self._resolve_material(state.material, state)
        if material is None or self._sequence_length(material.diffuse_colors) == 0:
            return None, None
        if self._sequence_length(material.diffuse_colors) == 1 and geometry_node.node_type not in {"IndexedLineSet", "PointSet"}:
            return None, None
        color_node = OutNode("Color", fields=[("color", self._clone_sequence(material.diffuse_colors))])
        color_index = None
        if "materialIndex" in geometry_node.fields:
            color_index = self._materialize_list(geometry_node.fields["materialIndex"])
        return color_node, color_index

    def _convert_material_state(self, node: AstNode, state: ConversionState) -> MaterialState | UseRef:
        """Convert a VRML 1.0 Material node into reusable structured material data."""

        material_state = MaterialState(
            ambient_colors=self._ensure_color_list(node.fields.get("ambientColor", [(0.2, 0.2, 0.2)])),
            diffuse_colors=self._ensure_color_list(node.fields.get("diffuseColor", [(0.8, 0.8, 0.8)])),
            specular_colors=self._ensure_color_list(node.fields.get("specularColor", [(0.0, 0.0, 0.0)])),
            emissive_colors=self._ensure_color_list(node.fields.get("emissiveColor", [(0.0, 0.0, 0.0)])),
            shininess=self._ensure_scalar_sequence(node.fields.get("shininess", [0.2]), float),
            transparency=self._ensure_scalar_sequence(node.fields.get("transparency", [0.0]), float),
            def_name=node.def_name,
        )
        if node.def_name:
            state.definitions[node.def_name] = material_state
        return material_state

    def _material_to_out_node(self, material: MaterialState) -> OutNode:
        """Collapse one MaterialState into a VRML 2.0 Material node."""

        diffuse = self._first_item(material.diffuse_colors)
        ambient = self._first_item(material.ambient_colors) if self._sequence_length(material.ambient_colors) else (0.2, 0.2, 0.2)
        ambient_intensity = max(0.0, min(1.0, sum(ambient) / 3.0))
        fields: list[tuple[str, Any]] = []
        if abs(ambient_intensity - 0.2) > 1e-9:
            fields.append(("ambientIntensity", ambient_intensity))
        if diffuse != (0.8, 0.8, 0.8):
            fields.append(("diffuseColor", diffuse))
        if self._sequence_length(material.specular_colors):
            specular = self._first_item(material.specular_colors)
            if specular != (0.0, 0.0, 0.0):
                fields.append(("specularColor", specular))
        if self._sequence_length(material.emissive_colors):
            emissive = self._first_item(material.emissive_colors)
            if emissive != (0.0, 0.0, 0.0):
                fields.append(("emissiveColor", emissive))
        if self._sequence_length(material.shininess):
            shininess = self._first_item(material.shininess)
            if abs(shininess - 0.2) > 1e-9:
                fields.append(("shininess", shininess))
        if self._sequence_length(material.transparency):
            transparency = self._first_item(material.transparency)
            if abs(transparency - 0.0) > 1e-9:
                fields.append(("transparency", transparency))
        return OutNode("Material", fields=fields, def_name=material.def_name)

    def _material_reference_to_output(
        self,
        material_ref: MaterialState | UseRef | DefinitionRef | None,
        material: MaterialState | None,
    ) -> OutNode | UseRef:
        """Emit a shared Material definition once and return `USE` for later references."""

        if material is None:
            if "_DefMat" in self.emitted_defs:
                return UseRef("_DefMat")
            self.emitted_defs.add("_DefMat")
            return OutNode("Material", fields=[], def_name="_DefMat")
        if isinstance(material_ref, DefinitionRef):
            if material_ref.name in self.emitted_defs:
                return UseRef(material_ref.name)
            material_node = self._material_to_out_node(material)
            material_node.def_name = material_ref.name
            self.emitted_defs.add(material_ref.name)
            return material_node
        if isinstance(material_ref, UseRef):
            return UseRef(material_ref.name)
        signature = self._material_signature(material)
        if signature in self.generated_material_names:
            return UseRef(self.generated_material_names[signature])
        generated_name = f"_v2%{self.generated_material_counter}"
        self.generated_material_counter += 1
        self.generated_material_names[signature] = generated_name
        material_node = self._material_to_out_node(material)
        material_node.def_name = generated_name
        self.emitted_defs.add(generated_name)
        return material_node

    def _material_signature(self, material: MaterialState) -> tuple[Any, ...]:
        """Create a stable signature for implicit material reuse."""

        return (
            tuple(material.ambient_colors),
            tuple(material.diffuse_colors),
            tuple(material.specular_colors),
            tuple(material.emissive_colors),
            tuple(material.shininess),
            tuple(material.transparency),
        )

    def _simple_out_node(self, node: AstNode, new_type: str) -> OutNode:
        """Copy the source fields into a differently named output node."""

        return OutNode(new_type, fields=list(node.fields.items()), def_name=node.def_name)

    def _convert_texture(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 Texture2 node into ImageTexture or PixelTexture."""

        if "filename" in node.fields:
            fields: list[tuple[str, Any]] = [("url", self._to_string_list(node.fields["filename"]))]
            if str(node.fields.get("wrapS", "REPEAT")).upper() == "CLAMP":
                fields.append(("repeatS", False))
            if str(node.fields.get("wrapT", "REPEAT")).upper() == "CLAMP":
                fields.append(("repeatT", False))
            return OutNode("ImageTexture", fields=fields, def_name=node.def_name)
        if "image" in node.fields:
            fields = [("image", list(node.fields["image"]))]
            return OutNode("PixelTexture", fields=fields, def_name=node.def_name)
        return OutNode("ImageTexture", fields=[("url", [])], def_name=node.def_name)

    def _convert_texture_transform(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 texture transform node into TextureTransform."""

        ordered_fields = [
            ("translation", node.fields.get("translation")),
            ("rotation", node.fields.get("rotation")),
            ("scale", node.fields.get("scaleFactor")),
            ("center", node.fields.get("center")),
        ]
        return OutNode("TextureTransform", fields=[(name, value) for name, value in ordered_fields if value is not None], def_name=node.def_name)

    def _convert_font_style(self, node: AstNode) -> OutNode:
        """Convert a VRML 1.0 FontStyle helper node into VRML 2.0 FontStyle."""

        fields: list[tuple[str, Any]] = []
        if "size" in node.fields:
            fields.append(("size", float(node.fields["size"])))
        if "family" in node.fields:
            family = str(node.fields["family"]).upper()
            mapped = {"SERIF": "SERIF", "TYPEWRITER": "TYPEWRITER"}.get(family, "SANS")
            fields.append(("family", [mapped]))
        if "style" in node.fields:
            style = str(node.fields["style"]).upper()
            fields.append(("style", style.replace("_", " ")))
        return OutNode("FontStyle", fields=fields, def_name=node.def_name)

    def _merge_font_style(self, base_font_style: OutNode | UseRef | DefinitionRef | None, text_fields: dict[str, Any]) -> OutNode | UseRef | DefinitionRef | None:
        """Merge inherited FontStyle state with AsciiText-specific hints."""

        if isinstance(base_font_style, UseRef):
            return base_font_style
        source_font_style = self._materialize_reference(base_font_style)
        if isinstance(source_font_style, UseRef):
            return source_font_style
        merged_fields: OrderedDict[str, Any] = OrderedDict()
        if isinstance(source_font_style, OutNode):
            for name, value in source_font_style.fields:
                merged_fields[name] = copy.deepcopy(value)
        if "spacing" in text_fields:
            merged_fields["spacing"] = float(text_fields["spacing"])
        if "justification" in text_fields:
            justification = str(text_fields["justification"]).upper()
            mapped = {"LEFT": "BEGIN", "CENTER": "MIDDLE", "RIGHT": "END"}.get(justification, "BEGIN")
            merged_fields["justify"] = [mapped]
        if not merged_fields:
            return None
        return OutNode("FontStyle", fields=list(merged_fields.items()))

    def _slice_coordinate_node(self, coordinate: OutNode | UseRef | DefinitionRef | None, fields: dict[str, Any]) -> OutNode | UseRef | None:
        """Slice Coordinate data for PointSet startIndex and numPoints when possible."""

        if coordinate is None or isinstance(coordinate, UseRef):
            return copy.deepcopy(coordinate)
        source_coordinate = self._materialize_reference(coordinate)
        if isinstance(source_coordinate, UseRef):
            return source_coordinate
        point_values = None
        for field_name, value in source_coordinate.fields:
            if field_name == "point":
                point_values = list(value)
                break
        if point_values is None:
            return source_coordinate
        start_index = int(fields.get("startIndex", 0))
        num_points = int(fields.get("numPoints", len(point_values) - start_index))
        sliced = point_values[start_index : start_index + num_points]
        return OutNode("Coordinate", fields=[("point", sliced)])

    def _wrap_transforms(self, node: OutNode, transforms: list[TransformSpec]) -> OutNode:
        """Wrap an emitted node in nested Transform nodes for persistent state."""

        if not transforms:
            return node
        def_name = node.def_name
        node.def_name = None
        wrapped = node
        for transform in reversed(transforms):
            if transform.kind == "matrix":
                matrix_transform = self._matrix_to_transform_fields(transform.value)
                wrapped = OutNode("Transform", fields=matrix_transform + [("children", [wrapped])])
                continue
            field_name = {"translation": "translation", "rotation": "rotation", "scale": "scale"}[transform.kind]
            wrapped = OutNode("Transform", fields=[(field_name, transform.value), ("children", [wrapped])])
        wrapped.def_name = def_name
        return wrapped

    def _matrix_to_transform_fields(self, matrix: list[float]) -> list[tuple[str, Any]]:
        """Approximate a MatrixTransform with the subset VRML 2.0 Transform supports."""

        scale = (
            (matrix[0] ** 2 + matrix[1] ** 2 + matrix[2] ** 2) ** 0.5,
            (matrix[4] ** 2 + matrix[5] ** 2 + matrix[6] ** 2) ** 0.5,
            (matrix[8] ** 2 + matrix[9] ** 2 + matrix[10] ** 2) ** 0.5,
        )
        fields: list[tuple[str, Any]] = [("translation", (matrix[12], matrix[13], matrix[14]))]
        if scale != (1.0, 1.0, 1.0):
            fields.append(("scale", scale))
        return fields

    def _register_definition(self, node: AstNode, value: Any, state: ConversionState) -> Any:
        """Store converted definitions so later USE references can be resolved."""

        if node.def_name:
            definition_ref = DefinitionRef(node.def_name, value)
            state.definitions[node.def_name] = definition_ref
            return definition_ref
        return value

    def _store_emitted_definition(self, node: OutNode, state: ConversionState) -> OutNode:
        """Store emitted node definitions so later USE statements can reference them."""

        if node.def_name:
            state.definitions[node.def_name] = DefinitionRef(node.def_name, node)
        return node

    def _apply_use_reference(self, use_ref: UseRef, state: ConversionState) -> list[OutNode]:
        """Apply a USE reference either as inherited state or as an emitted child node."""

        resolved = state.definitions.get(use_ref.name)
        if resolved is None:
            LOGGER.warning("Skipping unknown USE reference %s", use_ref.name)
            return []
        value = resolved.value if isinstance(resolved, DefinitionRef) else resolved
        if isinstance(value, MaterialState):
            state.material = DefinitionRef(use_ref.name, value)
            return []
        if isinstance(value, OutNode):
            if value.node_type == "Coordinate":
                state.coordinate = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "Normal":
                state.normal = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "TextureCoordinate":
                state.tex_coord = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type in {"ImageTexture", "PixelTexture"}:
                state.texture = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "TextureTransform":
                state.texture_transform = DefinitionRef(use_ref.name, value)
                return []
            if value.node_type == "FontStyle":
                state.font_style = DefinitionRef(use_ref.name, value)
                return []
            return [UseRef(use_ref.name)]
        LOGGER.warning("Skipping unsupported USE reference %s", use_ref.name)
        return []

    def _resolve_material(self, material: MaterialState | UseRef | DefinitionRef | None, state: ConversionState) -> MaterialState | None:
        """Resolve material USE references into material data when available."""

        if material is None:
            return None
        if isinstance(material, DefinitionRef):
            if isinstance(material.value, MaterialState):
                return material.value
            return None
        if isinstance(material, UseRef):
            resolved = state.definitions.get(material.name)
            if isinstance(resolved, DefinitionRef) and isinstance(resolved.value, MaterialState):
                return resolved.value
            if isinstance(resolved, MaterialState):
                return resolved
            return None
        return material

    def _materialize_reference(self, value: Any) -> Any:
        """Emit a stored definition once and return `USE` on later references."""

        if isinstance(value, DefinitionRef):
            if value.name in self.emitted_defs:
                return UseRef(value.name)
            materialized = copy.deepcopy(value.value)
            if isinstance(materialized, (OutNode, MaterialState)):
                materialized.def_name = value.name
            self.emitted_defs.add(value.name)
            return materialized
        return copy.deepcopy(value)

    def _ensure_color_list(self, value: Any) -> Any:
        """Normalize a color field to a list of RGB tuples."""

        if isinstance(value, SpoolSequence):
            return value
        if isinstance(value, list):
            return [tuple(map(float, item)) for item in value]
        return [tuple(map(float, value))]

    def _ensure_scalar_sequence(self, value: Any, cast: Any) -> Any:
        """Normalize scalar multi-fields while preserving spool-backed sequences."""

        if isinstance(value, SpoolSequence):
            return value
        if isinstance(value, list):
            return [cast(item) for item in value]
        return [cast(value)]

    def _clone_sequence(self, value: Any) -> Any:
        """Clone a regular list but reuse immutable spool-backed sequences."""

        if isinstance(value, SpoolSequence):
            return value
        return copy.deepcopy(value)

    def _sequence_length(self, value: Any) -> int:
        """Return the length of a normal or spool-backed logical sequence."""

        if value is None:
            return 0
        return len(value)

    def _first_item(self, value: Any) -> Any:
        """Return the first item from a normal or spool-backed logical sequence."""

        if isinstance(value, SpoolSequence):
            return value.first()
        return value[0]

    def _materialize_list(self, value: Any) -> list[Any]:
        """Materialize a logical sequence to a plain list when random access is needed."""

        return list(value)

    def _to_string_list(self, value: Any) -> list[str]:
        """Normalize a string-or-list field to a list of strings."""

        if isinstance(value, list):
            return [str(item) for item in value]
        if value is None:
            return []
        return [str(value)]
