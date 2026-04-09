# BETO-TRACE: HSCENE.SEC1.INTENT.SCENE_HANDLERS
# BETO-TRACE: BFMCP.SEC8.TECH.MODULAR_ARCH
"""
Scene & System Handlers

Handlers declared in BETO_CORE_HANDLERS_SCENE:
  get_scene_info          — HSCENE.SEC3.HANDLER.GET_SCENE_INFO
  get_viewport_screenshot — HSCENE.SEC3.HANDLER.GET_VIEWPORT_SCREENSHOT
  execute_code            — HSCENE.SEC3.HANDLER.EXECUTE_CODE
  get_server_status       — HSCENE.SEC3.HANDLER.GET_SERVER_STATUS

All handlers are read-only on the scene.
execute_code requires allow_exec=True (passed via closure from __init__.py).
"""
import base64
import contextlib
import io
import logging
import os
import tempfile

import bpy

log = logging.getLogger(__name__)


def get_scene_info() -> dict:
    """BETO-TRACE: HSCENE.SEC3.HANDLER.GET_SCENE_INFO"""
    # BETO-TRACE: HSCENE.SEC8.TECH.BPY_DATA_OBJECTS
    objects = []
    for obj in bpy.data.objects:
        objects.append({
            "name":       obj.name,
            "type":       obj.type,
            "location":   [round(v, 4) for v in obj.location],
            "dimensions": [round(v, 4) for v in obj.dimensions],
            "visible":    obj.visible_get(),
        })
    return {"objects": objects, "count": len(objects)}


def get_viewport_screenshot(max_size: int = 800, format: str = "png") -> dict:
    """BETO-TRACE: HSCENE.SEC3.HANDLER.GET_VIEWPORT_SCREENSHOT"""
    # BETO-TRACE: HSCENE.SEC8.TECH.SCREENSHOT_OP
    tmp_path = tempfile.mktemp(suffix=f".{format}")
    try:
        # Find a VIEW_3D area to capture
        captured = False
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    with bpy.context.temp_override(window=window, area=area):
                        bpy.ops.screen.screenshot_area(filepath=tmp_path)
                    captured = True
                    break
            if captured:
                break

        if not captured:
            # Fallback: full screen screenshot
            bpy.ops.screen.screenshot(filepath=tmp_path)

        with open(tmp_path, "rb") as f:
            data = f.read()

        return {
            "image_base64": base64.b64encode(data).decode("utf-8"),
            "format":       format,
            "size_bytes":   len(data),
        }
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def execute_code(code: str, allow_exec: bool = False) -> dict:
    """
    BETO-TRACE: HSCENE.SEC3.HANDLER.EXECUTE_CODE
    BETO-TRACE: BFMCP.SEC8.TECH.EXECUTE_CODE_FLAG

    Security invariant: returns error if allow_exec=False.
    allow_exec is injected via closure in __init__.py — NOT from client params.
    """
    # BETO-TRACE: HSCENE.SEC8.TECH.EXEC_WITH_REDIRECT
    if not allow_exec:
        raise RuntimeError(
            "execute_code is disabled. "
            "Enable it in the BlenderFace MCP panel (View3D > N-panel > BlenderFace MCP)."
        )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, {"bpy": bpy})  # noqa: S102
    return {"output": out.getvalue()}


def get_server_status(server=None) -> dict:
    """BETO-TRACE: HSCENE.SEC3.HANDLER.GET_SERVER_STATUS"""
    if server is None:
        return {"running": False, "port": None, "handlers": []}
    return {
        "running":    server.running,
        "host":       server.host,
        "port":       server.port,
        "allow_exec": server.allow_exec,
        "handlers":   sorted(server._handlers.keys()),
    }
