# beto-blender-mcp

Blender 5.1 MCP addon for the **blenderface** pipeline — fully specified and materialized using the [BETO v4.5 framework](https://github.com/aramirez-maza/beto-framework).

This repository is a **canonical example of a BETO skill output**: a real, working software artifact produced by running the complete BETO v4.5 protocol (11 steps, 3 human gates) from a raw idea to deployed code.

---

## What it does

`blenderface-mcp` is a Blender addon that exposes a **TCP MCP (Model Context Protocol) server** on port 7878, enabling an external Python pipeline to control Blender programmatically.

It was built specifically for the **blenderface pipeline** — which reconstructs a 3D FLAME head mesh from a frontal face photo and generates 3D hair on top of it.

### Handlers (13 total)

| Domain | Handler | Description |
|--------|---------|-------------|
| Scene | `get_scene_info` | List all objects in the scene |
| Scene | `get_viewport_screenshot` | Capture viewport as PNG (base64) |
| Scene | `get_server_status` | Server state + registered handlers |
| Scene | `execute_code` | Run arbitrary Python (requires explicit flag) |
| FLAME Mesh | `import_flame_mesh` | Import OBJ/GLB mesh into Blender |
| FLAME Mesh | `get_object_info` | Vertex count, faces, material, location |
| FLAME Mesh | `list_objects` | List scene objects by type |
| Hair | `create_hair_curves` | Create native Hair Curves from scalp vertices |
| Hair | `create_particle_hair` | Fallback: Particle System HAIR |
| Hair | `set_hair_guide_curves` | Update guide curves from new vertex indices |
| Material | `assign_hair_material` | Assign Principled Hair BSDF material |
| Material | `set_hair_color` | Set hair color (RGB) |
| Material | `set_hair_properties` | Set roughness and melanin |

---

## Why it's better than ahujasid/blender-mcp

This addon was designed to fix specific problems found in the reference addon:

| Problem | Reference addon | This addon |
|---------|----------------|------------|
| Blender 5.1 compatibility | `bl_info` (deprecated since 4.2) | `blender_manifest.toml` |
| TCP protocol | Raw JSON, `recv(8192)` breaks on large payloads | 4-byte big-endian length prefix framing |
| Architecture | 2,635-line god-class | Modular handlers per domain |
| API keys | Hardcoded in source | None — no external services |
| Threading | Thread-per-handler on `bpy` | 1 net thread + queue + dispatcher in main thread |
| Pipeline-specific tools | None | FLAME mesh, Hair Curves, hair materials |

---

## Architecture

```
Pipeline (WSL2/Linux)          Windows Firewall + portproxy        Blender 5.1
─────────────────────          ────────────────────────────        ──────────────────────────────────────
send_framed(cmd) ──────────────── 172.31.128.1:7878 ──────────── 127.0.0.1:7878
                                                                       │
                                                                  networking thread
                                                                  (NO bpy access)
                                                                       │
                                                                  queue.Queue
                                                                  (thread-safe)
                                                                       │
                                                                  bpy.app.timers
                                                                  dispatcher
                                                                  (main thread only)
                                                                       │
                                                              handlers/scene.py
                                                              handlers/flame.py
                                                              handlers/hair.py
                                                              handlers/material.py
```

**Concurrency model (operator-declared):**
- 1 dedicated networking thread — TCP accept + recv, zero `bpy` access
- 1 `queue.Queue` — thread-safe job buffer
- 1 dispatcher in Blender main thread — `bpy.app.timers.register()`, deterministic execution

---

## Installation (Blender 5.1)

### 1. Download the addon

Clone this repo or download `blenderface_mcp/` directory.

### 2. Create the zip

```bash
cd blenderface_mcp
zip -r ../blenderface_mcp.zip . -x "*.pyc" -x "__pycache__/*"
```

### 3. Install in Blender 5.1

Drag and drop `blenderface_mcp.zip` into the Blender window.
Blender detects the `blender_manifest.toml` and installs it as an extension.

### 4. Start the server

Open the **N-panel** (`N` key in viewport) → **BlenderFace MCP** tab → **Start MCP Server**.

### 5. Windows + WSL2 setup

If running the pipeline from WSL2, run once in **PowerShell (Admin)**:

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=7878 connectaddress=127.0.0.1 connectport=7878
netsh advfirewall firewall add rule name="BlenderFace MCP 7878" protocol=TCP dir=in localport=7878 action=allow
```

---

## TCP Protocol

Every message (client → server and server → client) uses **4-byte big-endian length prefix framing**:

```
[4 bytes: payload length, big-endian uint32][N bytes: UTF-8 JSON payload]
```

### Command format
```json
{"type": "get_scene_info", "params": {}, "request_id": "optional-correlation-id"}
```

### Response format
```json
{"status": "success", "result": {...}, "request_id": "optional-correlation-id"}
```

### Python client example

```python
import socket, struct, json

def send_framed(sock, data: bytes):
    sock.sendall(struct.pack('>I', len(data)) + data)

def recv_framed(sock) -> bytes:
    header = b''
    while len(header) < 4:
        header += sock.recv(4 - len(header))
    n = struct.unpack('>I', header)[0]
    buf = b''
    while len(buf) < n:
        buf += sock.recv(n - len(buf))
    return buf

sock = socket.socket()
sock.connect(('172.31.128.1', 7878))

cmd = json.dumps({'type': 'get_scene_info', 'params': {}}).encode()
send_framed(sock, cmd)
resp = json.loads(recv_framed(sock))
print(resp)
sock.close()
```

---

## BETO v4.5 — How this was built

This addon is a canonical output of the **BETO v4.5 framework** — an epistemic governance protocol for LLM-assisted software specification and materialization.

### The BETO cycle that produced this addon

| Step | Output |
|------|--------|
| Step 0 | Semantic eligibility: `GO` |
| Step 1 | `BETO_CORE_DRAFT` — 54 declared elements, 5 BETO_ASSISTED resolutions, 2 OPERATOR declarations |
| **G-1** | Operator approved + declared concurrency model (threading constraints) |
| Step 2 | Structural interview — 0 conflicts, 7 PARALLEL candidates identified |
| Step 3 | Structural classification — 7 PARALLEL nodes, 0 Sub-BETOs |
| Step 4 | System graph — 8 nodes, 13 edges, 10/10 topology validations PASS |
| **G-2** | Operator approved + refined `TCP_SERVER_CORE` execution constraints |
| Step 5 | 7 child BETO_COREs generated (one per PARALLEL node) |
| Step 6 | OSC: `APPROVED_EXECUTABLE` — all 0 critical OQs declared executable |
| Step 6B | Product Fit: `FIT_APPROVED` — 5/5 criteria PASS |
| Step 7 | Phase documents for all 8 nodes |
| Step 8 | `TRACE_REGISTRY` — 48 authorized IDs |
| **G-3** | Operator approved → materialization |
| Step 10 | 9 files generated, 69 BETO-TRACE annotations, `TRACE_VERIFIED` |

### BETO traceability

Every element in the generated code carries a `BETO-TRACE` annotation linking it to a declared element in the specification:

```python
# BETO-TRACE: TCPSRV.SEC1.INTENT.NETWORKING_THREAD
# BETO-TRACE: BFMCP.SEC8.TECH.CONCURRENCY_MODEL
class BlenderMCPServer:
    ...
```

Format: `BETO_<SYSTEM>.SEC<N>.<TYPE>.<ELEMENT>`

---

## Project structure

```
blenderface_mcp/
├── blender_manifest.toml   # Blender 4.2+/5.1 extension manifest
├── __init__.py             # register/unregister + UI Panel + Operators
├── server.py               # BlenderMCPServer (net thread + queue + dispatcher)
└── handlers/
    ├── scene.py            # get_scene_info, screenshot, execute_code, status
    ├── flame.py            # import_flame_mesh, get_object_info, list_objects
    ├── hair.py             # create_hair_curves, particle_hair, guide_curves
    └── material.py         # assign_material, set_color, set_properties

blender_materializer/
└── materializer.py         # Pipeline client — migrated to framed protocol

blender_hair_materializer/
└── hair_materializer.py    # Pipeline client — migrated to framed protocol
```

---

## License

GPL-3.0-or-later — consistent with Blender's licensing requirements.

---

*Generated with [BETO v4.5](https://github.com/aramirez-maza/beto-framework) + [Claude Code](https://claude.ai/code)*
