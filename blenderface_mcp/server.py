# BETO-TRACE: TCPSRV.SEC1.INTENT.NETWORKING_THREAD
# BETO-TRACE: BFMCP.SEC8.TECH.CONCURRENCY_MODEL
# BETO-TRACE: BFMCP.SEC8.TECH.TCP_FRAMING
"""
BlenderFace MCP — TCP Server Core

Concurrency model (DECLARED [OPERATOR]):
  1 networking thread  — accept + recv, NO bpy access
  1 queue.Queue        — thread-safe job buffer
  1 dispatcher         — runs in Blender main thread via bpy.app.timers

Framing protocol (DECLARED [BETO_ASSISTED] OQ-2):
  Every message: [4-byte big-endian length][UTF-8 JSON payload]

Anti-patterns discarded (DECLARED [OPERATOR]):
  - thread-per-handler on bpy
  - bpy access from secondary threads
  - asyncio as primary mechanism
  - multiprocess
"""
import json
import logging
import queue
import socket
import struct
import threading
import time
import traceback
from typing import Any, Callable, Optional

import bpy

# BETO-TRACE: TCPSRV.SEC8.TECH.TIMERS_INTERVAL
DISPATCHER_INTERVAL: float = 0.016  # ~60 fps — low overhead, low latency

log = logging.getLogger(__name__)


class BlenderMCPServer:
    """
    BETO-TRACE: TCPSRV.SEC1.INTENT.NETWORKING_THREAD

    Single instance per Blender session.  Lifecycle managed by
    BFMCP_OT_StartServer / BFMCP_OT_StopServer operators.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7878) -> None:
        # BETO-TRACE: BFMCP.SEC8.TECH.DEFAULT_PORT
        self.host = host
        self.port = port
        self.running = False
        # BETO-TRACE: BFMCP.SEC8.TECH.EXECUTE_CODE_FLAG
        self.allow_exec: bool = False

        self._socket: Optional[socket.socket] = None
        self._net_thread: Optional[threading.Thread] = None
        # BETO-TRACE: TCPSRV.SEC8.TECH.QUEUE_STDLIB
        self._job_queue: queue.Queue = queue.Queue()
        self._handlers: dict[str, Callable[..., Any]] = {}

    # ─── Public API ──────────────────────────────────────────────────────────

    def register_handler(self, name: str, func: Callable[..., Any]) -> None:
        """BETO-TRACE: TCPSRV.SEC3.OUTPUT.HANDLER_REGISTRY_API"""
        self._handlers[name] = func

    def start(self) -> None:
        if self.running:
            log.warning("BlenderFace MCP already running")
            return

        self.running = True

        # BETO-TRACE: TCPSRV.SEC8.TECH.FRAMING_STRUCT_PACK — socket setup
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind((self.host, self.port))
            self._socket.listen(5)
            # BETO-TRACE: TCPSRV.SEC8.TECH.SOCKET_TIMEOUT
            self._socket.settimeout(1.0)
        except OSError as e:
            log.error(f"BlenderFace MCP — socket bind failed: {e}")
            self.running = False
            return

        # BETO-TRACE: BFMCP.SEC8.TECH.CONCURRENCY_MODEL — dedicated net thread
        self._net_thread = threading.Thread(
            target=self._net_loop,
            daemon=True,
            name="bfmcp-net",
        )
        self._net_thread.start()

        # Dispatcher in Blender main thread
        bpy.app.timers.register(
            self._dispatcher_tick,
            first_interval=DISPATCHER_INTERVAL,
            persistent=True,
        )

        log.info(f"BlenderFace MCP started on {self.host}:{self.port}")

    def stop(self) -> None:
        self.running = False

        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        if self._net_thread and self._net_thread.is_alive():
            self._net_thread.join(timeout=2.0)
        self._net_thread = None

        # Timer self-unregisters on next tick when running=False
        log.info("BlenderFace MCP stopped")

    # ─── Networking thread — NO bpy ──────────────────────────────────────────
    # BETO-TRACE: BFMCP.SEC8.TECH.ANTI_PATTERNS

    def _net_loop(self) -> None:
        """Dedicated networking thread. Never touches bpy."""
        log.debug("bfmcp-net thread started")
        while self.running:
            try:
                try:
                    client_sock, addr = self._socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.running:
                        log.error("Accept error — socket closed unexpectedly")
                    break

                log.debug(f"Client connected: {addr}")
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True,
                    name=f"bfmcp-client-{addr[1]}",
                )
                t.start()

            except Exception as e:
                if self.running:
                    log.error(f"Net loop error: {e}")
                    time.sleep(0.2)

        log.debug("bfmcp-net thread stopped")

    def _handle_client(self, sock: socket.socket) -> None:
        """Per-client handler. Runs in networking thread. Never touches bpy."""
        sock.settimeout(120.0)
        try:
            while self.running:
                data = self._recv_framed(sock)
                if data is None:
                    break  # client disconnected

                try:
                    command = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError as e:
                    self._send_framed(sock, json.dumps({
                        "status": "error",
                        "message": f"Invalid JSON: {e}",
                        "request_id": None,
                    }).encode("utf-8"))
                    continue

                # BETO-TRACE: TCPSRV.SEC4.UNIT.JOB
                # BETO-TRACE: BFMCP.SEC8.TECH.REQUEST_ID
                job = {
                    "cmd_type":     command.get("type", ""),
                    "params":       command.get("params", {}),
                    "request_id":   command.get("request_id"),
                    "client_socket": sock,
                }
                self._job_queue.put(job)

        except (ConnectionResetError, BrokenPipeError):
            log.debug("Client disconnected")
        except Exception as e:
            log.error(f"Client handler error: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # ─── Main-thread dispatcher (bpy.app.timers) ─────────────────────────────
    # BETO-TRACE: BFMCP.SEC8.TECH.CONCURRENCY_MODEL

    def _dispatcher_tick(self) -> Optional[float]:
        """
        BETO-TRACE: TCPSRV.SEC1.INTENT.NETWORKING_THREAD — dispatcher
        Runs exclusively in Blender main thread.
        Dequeues jobs, executes handlers, sends responses.
        Returns None to unregister, float to reschedule.
        """
        if not self.running:
            return None  # Unregister timer

        while True:
            try:
                job = self._job_queue.get_nowait()
            except queue.Empty:
                break

            cmd_type     = job["cmd_type"]
            params       = job["params"]
            request_id   = job["request_id"]
            client_sock  = job["client_socket"]

            try:
                handler = self._handlers.get(cmd_type)
                if handler is None:
                    result = {
                        "status": "error",
                        "message": f"Unknown command: '{cmd_type}'",
                        "request_id": request_id,
                    }
                else:
                    log.debug(f"Executing: {cmd_type}")
                    handler_result = handler(**params)
                    result = {
                        "status": "success",
                        "result": handler_result,
                        "request_id": request_id,
                    }
            except Exception as e:
                traceback.print_exc()
                result = {
                    "status": "error",
                    "message": str(e),
                    "request_id": request_id,
                }

            try:
                self._send_framed(client_sock, json.dumps(result).encode("utf-8"))
            except Exception as e:
                log.error(f"Failed to send response for '{cmd_type}': {e}")

        return DISPATCHER_INTERVAL  # Reschedule

    # ─── Framing helpers ─────────────────────────────────────────────────────
    # BETO-TRACE: TCPSRV.SEC8.TECH.FRAMING_STRUCT_PACK

    @staticmethod
    def _send_framed(sock: socket.socket, data: bytes) -> None:
        """Send bytes with 4-byte big-endian length prefix."""
        sock.sendall(struct.pack(">I", len(data)) + data)

    @staticmethod
    def _recv_framed(sock: socket.socket) -> Optional[bytes]:
        """Receive bytes with 4-byte big-endian length prefix. None = disconnect."""
        header = BlenderMCPServer._recv_exact(sock, 4)
        if header is None:
            return None
        (payload_len,) = struct.unpack(">I", header)
        if payload_len == 0:
            return b""
        return BlenderMCPServer._recv_exact(sock, payload_len)

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
        """Read exactly n bytes. Returns None on disconnect."""
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf
