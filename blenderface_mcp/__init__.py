# BETO-TRACE: BFMCP.SEC1.INTENT.MCP_ADDON_BLENDER51
# BETO-TRACE: BFMCP.SEC8.TECH.BLENDER_VERSION
# BETO-TRACE: UIPANEL.SEC8.TECH.PANEL_VIEW3D
# BETO-TRACE: UIPANEL.SEC8.TECH.OPERATOR_START_STOP
# BETO-TRACE: UIPANEL.SEC8.TECH.ADDON_PREFERENCES
"""
BlenderFace MCP — Blender 5.1 compatible addon

Entry point for the Blender extension system.
bl_info is intentionally omitted — replaced by blender_manifest.toml (Blender 4.2+).

Registers:
  - BlenderMCPServer instance (server.py)
  - All domain handlers via closures (handlers/)
  - View3D N-panel (BlenderFace MCP)
  - Start / Stop operators
  - AddonPreferences for host configuration
"""
import logging

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .server import BlenderMCPServer
from . import handlers

log = logging.getLogger(__name__)

# Global server instance — created at register(), destroyed at unregister()
_server: BlenderMCPServer = None


# ─── Addon Preferences ───────────────────────────────────────────────────────
# BETO-TRACE: UIPANEL.SEC8.TECH.ADDON_PREFERENCES

class BFMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    host: StringProperty(
        name="Host",
        description="Host address for the MCP TCP server",
        default="127.0.0.1",
    )

    def draw(self, context):
        self.layout.prop(self, "host")


# ─── Operators ───────────────────────────────────────────────────────────────
# BETO-TRACE: UIPANEL.SEC8.TECH.OPERATOR_START_STOP

class BFMCP_OT_StartServer(bpy.types.Operator):
    bl_idname  = "bfmcp.start_server"
    bl_label   = "Start MCP Server"
    bl_description = "Start the BlenderFace MCP TCP server"

    def execute(self, context):
        global _server
        if _server is not None and _server.running:
            self.report({"WARNING"}, "Server already running")
            return {"CANCELLED"}

        prefs = context.preferences.addons.get(__name__)
        host = prefs.preferences.host if prefs else "0.0.0.0"
        port = context.scene.bfmcp_port

        _server = BlenderMCPServer(host=host, port=port)
        _server.allow_exec = context.scene.bfmcp_allow_exec
        _register_handlers(_server, context)
        _server.start()

        context.scene.bfmcp_running = _server.running

        if _server.running:
            self.report({"INFO"}, f"BlenderFace MCP running on port {port}")
        else:
            self.report({"ERROR"}, f"Failed to start — port {port} may be in use. Check System Console.")

        # Force UI redraw
        for area in context.screen.areas:
            area.tag_redraw()

        return {"FINISHED"}


class BFMCP_OT_StopServer(bpy.types.Operator):
    bl_idname  = "bfmcp.stop_server"
    bl_label   = "Stop MCP Server"
    bl_description = "Stop the BlenderFace MCP TCP server"

    def execute(self, context):
        global _server
        if _server and _server.running:
            _server.stop()
        context.scene.bfmcp_running = False
        return {"FINISHED"}


# ─── UI Panel ────────────────────────────────────────────────────────────────
# BETO-TRACE: UIPANEL.SEC8.TECH.PANEL_VIEW3D

class BFMCP_PT_Panel(bpy.types.Panel):
    bl_label       = "BlenderFace MCP"
    bl_idname      = "BFMCP_PT_panel"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = "BlenderFace MCP"

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        # Status indicator
        running = scene.bfmcp_running
        status_icon = "CHECKMARK" if running else "X"
        status_text = f"Running on port {scene.bfmcp_port}" if running else "Stopped"
        layout.label(text=status_text, icon=status_icon)

        layout.separator()

        # Port config — only editable when stopped
        row = layout.row()
        row.enabled = not running
        row.prop(scene, "bfmcp_port")

        # execute_code flag
        layout.prop(scene, "bfmcp_allow_exec")

        layout.separator()

        # Start / Stop
        if not running:
            layout.operator("bfmcp.start_server", icon="PLAY")
        else:
            layout.operator("bfmcp.stop_server", icon="PAUSE")


# ─── Handler registration ─────────────────────────────────────────────────────
# BETO-TRACE: TCPSRV.SEC3.OUTPUT.HANDLER_REGISTRY_API

def _register_handlers(server: BlenderMCPServer, context) -> None:
    """
    Register all declared domain handlers on the server instance.
    execute_code uses a closure to inject server.allow_exec securely
    (client cannot override the flag via params).
    get_server_status uses a closure to inject the server instance.
    """
    # Scene handlers — HSCENE
    server.register_handler("get_scene_info",          handlers.scene.get_scene_info)
    server.register_handler("get_viewport_screenshot", handlers.scene.get_viewport_screenshot)
    server.register_handler("get_server_status",
        lambda: handlers.scene.get_server_status(server))
    # Security closure: allow_exec comes from server state, NOT from client params
    server.register_handler("execute_code",
        lambda code: handlers.scene.execute_code(code, allow_exec=server.allow_exec))

    # FLAME mesh handlers — HFLAME
    server.register_handler("import_flame_mesh", handlers.flame.import_flame_mesh)
    server.register_handler("get_object_info",   handlers.flame.get_object_info)
    server.register_handler("list_objects",      handlers.flame.list_objects)

    # Hair handlers — HHAIR
    server.register_handler("create_hair_curves",    handlers.hair.create_hair_curves)
    server.register_handler("create_particle_hair",  handlers.hair.create_particle_hair)
    server.register_handler("set_hair_guide_curves", handlers.hair.set_hair_guide_curves)

    # Material handlers — HMAT
    server.register_handler("assign_hair_material", handlers.material.assign_hair_material)
    server.register_handler("set_hair_color",        handlers.material.set_hair_color)
    server.register_handler("set_hair_properties",   handlers.material.set_hair_properties)


# ─── Register / Unregister ───────────────────────────────────────────────────

_classes = [
    BFMCP_AddonPreferences,
    BFMCP_OT_StartServer,
    BFMCP_OT_StopServer,
    BFMCP_PT_Panel,
]


def register():
    # BETO-TRACE: BFMCP.SEC8.TECH.BLENDER_VERSION — Scene properties
    bpy.types.Scene.bfmcp_port = IntProperty(
        name="Port",
        description="TCP port for the BlenderFace MCP server",
        default=7878,
        min=1024,
        max=65535,
    )
    bpy.types.Scene.bfmcp_running = BoolProperty(
        name="Server Running",
        default=False,
    )
    bpy.types.Scene.bfmcp_allow_exec = BoolProperty(
        name="Allow execute_code",
        description="Enable the execute_code handler (runs arbitrary Python in Blender)",
        default=False,
    )

    for cls in _classes:
        bpy.utils.register_class(cls)

    log.info("BlenderFace MCP addon registered")


def unregister():
    global _server
    if _server and _server.running:
        _server.stop()
    _server = None

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.bfmcp_port
    del bpy.types.Scene.bfmcp_running
    del bpy.types.Scene.bfmcp_allow_exec

    log.info("BlenderFace MCP addon unregistered")
