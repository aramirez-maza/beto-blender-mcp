# BETO-TRACE: HFLAME.SEC1.INTENT.FLAME_HANDLERS
# BETO-TRACE: BFMCP.SEC8.TECH.MODULAR_ARCH
"""
FLAME Mesh Handlers

Handlers declared in BETO_CORE_HANDLERS_FLAME:
  import_flame_mesh — HFLAME.SEC3.HANDLER.IMPORT_FLAME_MESH
  get_object_info   — HFLAME.SEC3.HANDLER.GET_OBJECT_INFO
  list_objects      — HFLAME.SEC3.HANDLER.LIST_OBJECTS

Invariant: if an object with the same name already exists, it is removed
before reimporting (declared in BETO_CORE_HANDLERS_FLAME SEC5).
"""
import logging
import os

import bpy

log = logging.getLogger(__name__)


def import_flame_mesh(filepath: str, object_name: str = "flame_head") -> dict:
    """
    BETO-TRACE: HFLAME.SEC3.HANDLER.IMPORT_FLAME_MESH

    Imports an OBJ or GLB/GLTF file into the current Blender scene.
    Format detected by file extension.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()

    # Invariant: remove existing object with same name before reimporting
    existing = bpy.data.objects.get(object_name)
    if existing is not None:
        log.debug(f"Removing existing object '{object_name}' before reimport")
        bpy.data.objects.remove(existing, do_unlink=True)

    # Import by format
    # BETO-TRACE: HFLAME.SEC8.TECH.IMPORT_OBJ_OP
    # BETO-TRACE: HFLAME.SEC8.TECH.IMPORT_GLTF_OP
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=filepath)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=filepath)
    else:
        raise ValueError(f"Unsupported format: '{ext}'. Declare .obj or .glb/.gltf.")

    # Rename the imported object
    imported = bpy.context.selected_objects
    if not imported:
        raise RuntimeError("Import succeeded but no object was selected in context.")

    obj = imported[0]
    obj.name = object_name
    if obj.data:
        obj.data.name = object_name

    return {
        "object_name":  obj.name,
        "vertex_count": len(obj.data.vertices) if obj.type == "MESH" else 0,
        "face_count":   len(obj.data.polygons) if obj.type == "MESH" else 0,
    }


def get_object_info(name: str) -> dict:
    """BETO-TRACE: HFLAME.SEC3.HANDLER.GET_OBJECT_INFO"""
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(f"Object '{name}' not found in scene.")

    material_name = None
    if obj.material_slots and obj.material_slots[0].material:
        material_name = obj.material_slots[0].material.name

    return {
        "name":         obj.name,
        "type":         obj.type,
        "vertex_count": len(obj.data.vertices) if obj.type == "MESH" else None,
        "face_count":   len(obj.data.polygons) if obj.type == "MESH" else None,
        "location":     [round(v, 4) for v in obj.location],
        "dimensions":   [round(v, 4) for v in obj.dimensions],
        "material":     material_name,
    }


def list_objects(type_filter: str = None) -> dict:
    """BETO-TRACE: HFLAME.SEC3.HANDLER.LIST_OBJECTS"""
    objects = []
    for obj in bpy.data.objects:
        if type_filter is None or obj.type == type_filter.upper():
            objects.append({"name": obj.name, "type": obj.type})
    return {"objects": objects, "count": len(objects)}
