# BETO-TRACE: HMAT.SEC1.INTENT.MATERIAL_HANDLERS
# BETO-TRACE: BFMCP.SEC8.TECH.MODULAR_ARCH
"""
Material Handlers

Handlers declared in BETO_CORE_HANDLERS_MATERIAL:
  assign_hair_material — HMAT.SEC3.HANDLER.ASSIGN_HAIR_MATERIAL
  set_hair_color       — HMAT.SEC3.HANDLER.SET_HAIR_COLOR
  set_hair_properties  — HMAT.SEC3.HANDLER.SET_HAIR_PROPERTIES

Invariants (BETO_CORE_HANDLERS_MATERIAL SEC5):
  - If a material with material_name already exists in bpy.data.materials,
    it is reused — never duplicated.
  - use_nodes = True on every material created.

Shader precedence (DECLARED):
  - Principled Hair BSDF for Curve/Hair Curves objects (HMAT.SEC8.TECH.PRINCIPLED_HAIR_BSDF)
  - Principled BSDF fallback for Particle Hair / Mesh (HMAT.SEC8.TECH.PRINCIPLED_BSDF_FALLBACK)
"""
import logging

import bpy

log = logging.getLogger(__name__)


def assign_hair_material(object_name: str, material_name: str = "bfhair_material") -> dict:
    """
    BETO-TRACE: HMAT.SEC3.HANDLER.ASSIGN_HAIR_MATERIAL
    BETO-TRACE: HMAT.SEC8.TECH.PRINCIPLED_HAIR_BSDF
    BETO-TRACE: HMAT.SEC8.TECH.PRINCIPLED_BSDF_FALLBACK
    """
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise KeyError(f"Object '{object_name}' not found in scene.")

    # Invariant: reuse existing material, never duplicate
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(name=material_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        output_node = nodes.new("ShaderNodeOutputMaterial")

        # BETO-TRACE: HMAT.SEC8.TECH.PRINCIPLED_HAIR_BSDF
        try:
            shader = nodes.new("ShaderNodeBsdfHairPrincipled")
            shader.parametrization = "COLOR"
        except Exception:
            # BETO-TRACE: HMAT.SEC8.TECH.PRINCIPLED_BSDF_FALLBACK
            log.debug(f"Principled Hair BSDF not available — using Principled BSDF for '{object_name}'")
            shader = nodes.new("ShaderNodeBsdfPrincipled")

        links.new(shader.outputs["BSDF"], output_node.inputs["Surface"])

    # Assign to object
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return {"material_name": mat.name, "object_name": object_name}


def set_hair_color(object_name: str, color: list) -> dict:
    """
    BETO-TRACE: HMAT.SEC3.HANDLER.SET_HAIR_COLOR

    color: [r, g, b] floats in [0.0, 1.0]
    """
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise KeyError(f"Object '{object_name}' not found in scene.")
    if not obj.material_slots or obj.material_slots[0].material is None:
        raise RuntimeError(f"Object '{object_name}' has no material assigned. Call assign_hair_material first.")

    mat = obj.material_slots[0].material
    if not mat.use_nodes:
        mat.use_nodes = True

    r, g, b = float(color[0]), float(color[1]), float(color[2])
    rgba = (r, g, b, 1.0)

    applied = None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_HAIR_PRINCIPLED":
            node.inputs["Color"].default_value = rgba
            applied = [r, g, b]
            break
        if node.type == "BSDF_PRINCIPLED":
            node.inputs["Base Color"].default_value = rgba
            applied = [r, g, b]
            break

    if applied is None:
        raise RuntimeError(f"No supported shader node found in material '{mat.name}'.")

    return {"color_applied": applied, "material_name": mat.name}


def set_hair_properties(
    object_name: str,
    roughness: float = 0.6,
    melanin: float = 0.5,
) -> dict:
    """
    BETO-TRACE: HMAT.SEC3.HANDLER.SET_HAIR_PROPERTIES

    Sets roughness and melanin on the hair shader.
    melanin applies only to Principled Hair BSDF.
    """
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise KeyError(f"Object '{object_name}' not found in scene.")
    if not obj.material_slots or obj.material_slots[0].material is None:
        raise RuntimeError(f"Object '{object_name}' has no material. Call assign_hair_material first.")

    mat = obj.material_slots[0].material
    applied: dict = {}

    for node in mat.node_tree.nodes:
        if node.type == "BSDF_HAIR_PRINCIPLED":
            if "Roughness" in node.inputs:
                node.inputs["Roughness"].default_value = float(roughness)
                applied["roughness"] = roughness
            if "Melanin" in node.inputs:
                node.inputs["Melanin"].default_value = float(melanin)
                applied["melanin"] = melanin
            break
        if node.type == "BSDF_PRINCIPLED":
            if "Roughness" in node.inputs:
                node.inputs["Roughness"].default_value = float(roughness)
                applied["roughness"] = roughness
            break

    if not applied:
        raise RuntimeError(f"No supported shader node found in material '{mat.name}'.")

    return {"applied": applied, "material_name": mat.name}
