#!/usr/bin/env python3
"""
LAN File Transfer Tool - 局域网文件传输工具

A simple web-based file transfer tool for LAN environments.
Run this on one machine, both sender and receiver open the web page.
Supports: files, images, text messages.

Usage:
    python transfer.py              # Default port 5000
    python transfer.py -p 8080      # Custom port
    python transfer.py --no-cleanup # Don't auto-clean old files
"""

import argparse
import json
import os
import queue
import shutil
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from flask import (
    Flask, Response, jsonify, make_response,
    render_template, request, send_file, stream_with_context
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DISCOVERY_PORT = 5001          # UDP port for LAN discovery
FILE_EXPIRE_MINUTES = 60       # Auto-clean files older than this
CHUNK_SIZE = 64 * 1024         # 64KB chunks for streaming

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB max

# SSE clients: each is a queue.Queue
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

# TLS redirect page (sent when browser tries https:// on the HTTP port)
_TLS_REDIRECT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>请使用 HTTP</title>
<style>
  body { font-family: -apple-system, sans-serif; display: flex;
         align-items: center; justify-content: center; min-height: 100vh;
         margin: 0; background: #f5f5f5; }
  .box { background: #fff; padding: 40px; border-radius: 12px;
         max-width: 500px; text-align: center; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
  h2 { color: #4f46e5; } p { color: #666; line-height: 1.7; }
  a { color: #4f46e5; font-weight: 600; font-size: 1.1rem; word-break: break-all; }
  code { background: #eef2ff; padding: 2px 8px; border-radius: 4px; }
</style></head>
<body>
<div class="box">
  <h2>⚠️ 请使用 HTTP 访问</h2>
  <p>你的浏览器自动将地址升级为了 <code>https://</code>，<br>
     但此服务仅支持 HTTP（局域网内部使用，无需加密）。</p>
  <p>请点击以下链接，或在地址栏手动输入：</p>
  <p><a href="__URL__">__URL__</a></p>
  <p style="font-size:0.85rem;color:#999;">提示：输入时确保地址以 <code>http://</code> 开头</p>
</div>
</body></html>"""


def _build_tls_redirect_page(host: str, port: int) -> bytes:
    """Build the TLS redirect HTML page for the given host/port."""
    url = f"http://{host}:{port}"
    return _TLS_REDIRECT_PAGE.replace("__URL__", url).encode("utf-8")




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_local_ip() -> str:
    """Detect the LAN IP address of this machine."""
    try:
        # Try connecting to an external address to determine the local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.1)
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        pass

    # Fallback: enumerate interfaces
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except OSError:
        return "127.0.0.1"


def get_all_local_ips() -> list[str]:
    """Return all plausible LAN IPs (192.168.x.x, 10.x.x.x, 172.16-31.x.x)."""
    ips = set()
    try:
        import netifaces
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            for addr in addrs.get(netifaces.AF_INET, []):
                ip = addr.get("addr", "")
                if ip and not ip.startswith("127."):
                    ips.add(ip)
    except ImportError:
        pass

    # Always try the primary method
    primary = get_local_ip()
    if primary:
        ips.add(primary)

    return sorted(ips)


def get_ip() -> str:
    """Get the primary LAN IP (without netifaces dependency)."""
    return get_local_ip()


def get_date_dir() -> Path:
    """Return today's upload subdirectory, creating it if needed."""
    today = datetime.now().strftime("%Y-%m-%d")
    d = UPLOAD_DIR / today
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_metadata(path: Path) -> dict:
    """Return metadata dict for a file. `path` is relative to UPLOAD_DIR."""
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(UPLOAD_DIR)),
        "date": path.parent.name if path.parent != UPLOAD_DIR else "",
        "size": stat.st_size,
        "size_human": format_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "type": guess_file_type(path),
    }


def format_size(size: int) -> str:
    """Format bytes to human readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def guess_file_type(path: Path) -> str:
    """Guess file type category."""
    ext = path.suffix.lower()
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico"}
    text_exts = {".txt", ".md", ".py", ".js", ".html", ".css", ".json", ".xml",
                 ".yaml", ".yml", ".ini", ".cfg", ".log", ".csv", ".sh", ".bat"}
    video_exts = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"}
    audio_exts = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma"}

    if ext in image_exts:
        return "image"
    elif ext in text_exts:
        return "text"
    elif ext in video_exts:
        return "video"
    elif ext in audio_exts:
        return "audio"
    elif ext in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}:
        return "archive"
    elif ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}:
        return "document"
    else:
        return "file"


def safe_filename(filename: str) -> str:
    """Sanitize filename, keep extension."""
    name, ext = os.path.splitext(filename)
    # Remove path separators and null bytes
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    ext = ext.replace("/", "_").replace("\\", "_").replace("\x00", "")
    name = name.strip() or "unnamed"
    ext = ext.strip()
    return f"{name}{ext}"


def safe_resolve_path(relative_path: str) -> Path | None:
    """Resolve a relative path under UPLOAD_DIR, rejecting path traversal."""
    # Normalize: strip leading/trailing slashes, prevent .. traversal
    cleaned = relative_path.lstrip("/").rstrip("/")
    if ".." in cleaned.split("/"):
        return None  # Path traversal attempt
    resolved = (UPLOAD_DIR / cleaned).resolve()
    if not str(resolved).startswith(str(UPLOAD_DIR.resolve())):
        return None
    return resolved


def broadcast_event(event: str, data: dict):
    """Push an SSE event to all connected clients."""
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait({"event": event, "data": data})
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_old_files():
    """Remove files older than FILE_EXPIRE_MINUTES. Clean empty date dirs."""
    now = time.time()
    cutoff = now - FILE_EXPIRE_MINUTES * 60
    removed = []
    for f in UPLOAD_DIR.rglob("*"):
        if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime < cutoff:
            # Skip chunk temp dirs
            if ".chunk_" in str(f):
                continue
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                pass
    # Remove empty date subdirectories
    for d in sorted(UPLOAD_DIR.iterdir(), reverse=True):
        if d.is_dir() and not d.name.startswith(".") and not d.name.startswith(".chunk_"):
            if not any(d.iterdir()):
                try:
                    d.rmdir()
                except OSError:
                    pass
    if removed:
        broadcast_event("files_deleted", {"files": removed})


def cleanup_loop():
    """Periodic cleanup thread."""
    while True:
        time.sleep(120)  # check every 2 minutes
        try:
            cleanup_old_files()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# LAN Discovery (UDP broadcast)
# ---------------------------------------------------------------------------

def discovery_listener(port: int):
    """Listen for UDP discovery probes and respond with server info."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(5)
    try:
        sock.bind(("", DISCOVERY_PORT))
    except OSError:
        # Port may already be in use (another instance)
        return

    local_ip = get_ip()
    resp = json.dumps({
        "type": "lan_transfer_server",
        "ip": local_ip,
        "port": port,
        "hostname": socket.gethostname(),
    }).encode()

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode(errors="ignore").strip()
            if msg == "LAN_TRANSFER_DISCOVER":
                sock.sendto(resp, addr)
        except socket.timeout:
            continue
        except OSError:
            break


def discover_servers(timeout: float = 2.0) -> list[dict]:
    """Broadcast a discovery probe and collect responses."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    # Send to broadcast address
    msg = b"LAN_TRANSFER_DISCOVER"
    servers = []

    try:
        sock.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))
    except OSError:
        sock.close()
        return servers

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock.settimeout(max(0.1, deadline - time.time()))
            data, addr = sock.recvfrom(4096)
            info = json.loads(data.decode())
            if info.get("type") == "lan_transfer_server":
                # Avoid duplicates
                key = f"{info['ip']}:{info['port']}"
                if not any(f"{s['ip']}:{s['port']}" == key for s in servers):
                    servers.append(info)
        except (socket.timeout, json.JSONDecodeError):
            continue
        except OSError:
            break

    sock.close()
    return servers


# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the main page."""
    local_ip = get_ip()
    return render_template("index.html", ip=local_ip, port=request.host.split(":")[-1])


@app.route("/api/info")
def api_info():
    """Return server info."""
    return jsonify({
        "ip": get_ip(),
        "port": request.host.split(":")[-1],
        "hostname": socket.gethostname(),
    })


@app.route("/api/files")
def api_files():
    """List all uploaded files (recursively from date subdirectories)."""
    files = []
    for f in sorted(UPLOAD_DIR.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and not f.name.startswith("."):
            # Skip chunk temp dirs
            if ".chunk_" in str(f):
                continue
            files.append(file_metadata(f))
    return jsonify({"files": files})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Upload one or more files. Saved to today's date folder."""
    date_dir = get_date_dir()
    uploaded = []
    for key in request.files:
        for f in request.files.getlist(key):
            if f.filename:
                filename = safe_filename(f.filename)
                dest = date_dir / filename
                if dest.exists():
                    stem, ext = os.path.splitext(filename)
                    dest = date_dir / f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
                f.save(str(dest))
                uploaded.append(file_metadata(dest))

    if uploaded:
        broadcast_event("new_files", {"files": uploaded})

    return jsonify({"status": "ok", "uploaded": uploaded})


@app.route("/api/upload/chunk", methods=["POST"])
def api_upload_chunk():
    """Upload a chunk of a large file.

    Headers/Form fields:
        X-File-Name: original filename
        X-Chunk-Index: zero-based chunk index
        X-Chunk-Total: total number of chunks
        X-Upload-Id: unique upload session id
    """
    chunk_data = request.get_data()
    filename = safe_filename(request.headers.get("X-File-Name", "unknown"))
    chunk_index = int(request.headers.get("X-Chunk-Index", 0))
    chunk_total = int(request.headers.get("X-Chunk-Total", 1))
    upload_id = request.headers.get("X-Upload-Id", uuid.uuid4().hex)

    # Temp directory for this upload session
    tmp_dir = UPLOAD_DIR / f".chunk_{upload_id}"
    tmp_dir.mkdir(exist_ok=True)

    # Save chunk
    chunk_path = tmp_dir / f"{chunk_index:06d}"
    chunk_path.write_bytes(chunk_data)

    # If all chunks received, reassemble into today's date folder
    assembled = False
    if len(list(tmp_dir.iterdir())) == chunk_total:
        date_dir = get_date_dir()
        dest = date_dir / filename
        if dest.exists():
            stem, ext = os.path.splitext(filename)
            dest = date_dir / f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
        with open(dest, "wb") as out:
            for i in range(chunk_total):
                part = tmp_dir / f"{i:06d}"
                out.write(part.read_bytes())
        # Cleanup temp
        shutil.rmtree(tmp_dir, ignore_errors=True)
        assembled = True
        meta = file_metadata(dest)
        broadcast_event("new_files", {"files": [meta]})

    return jsonify({
        "status": "ok",
        "chunk": chunk_index,
        "assembled": assembled,
    })


@app.route("/api/download/<path:filepath>")
def api_download(filepath: str):
    """Download a file by its relative path (e.g. 2026-07-11/photo.jpg)."""
    filepath = unquote(filepath)
    path = safe_resolve_path(filepath)
    if path is None or not path.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(str(path), as_attachment=True, download_name=path.name)


@app.route("/api/files/<path:filepath>", methods=["DELETE"])
def api_delete(filepath: str):
    """Delete a file by its relative path."""
    filepath = unquote(filepath)
    path = safe_resolve_path(filepath)
    if path is None or not path.exists():
        return jsonify({"error": "file not found"}), 404
    filename = path.name
    path.unlink()
    # Remove empty date folder
    parent = path.parent
    if parent != UPLOAD_DIR and not any(parent.iterdir()):
        parent.rmdir()
    broadcast_event("files_deleted", {"files": [filename]})
    return jsonify({"status": "ok"})


@app.route("/api/text", methods=["POST"])
def api_text():
    """Save a text message as a .txt file in today's date folder.
    Filename: HHMMSS.txt (time only)."""
    data = request.get_json(force=True)
    content = data.get("content", "")
    title = data.get("title", "").strip()

    if not content:
        return jsonify({"error": "empty content"}), 400

    ts = datetime.now().strftime("%H%M%S")
    if title:
        filename = f"{ts}_{safe_filename(title)}.txt"
    else:
        filename = f"{ts}.txt"

    filename = safe_filename(filename)
    path = get_date_dir() / filename
    # Avoid overwrite within same second
    if path.exists():
        path = get_date_dir() / f"{ts}_{uuid.uuid4().hex[:4]}.txt"
    path.write_text(content, encoding="utf-8")

    meta = file_metadata(path)
    broadcast_event("new_files", {"files": [meta]})
    return jsonify({"status": "ok", "file": meta})


@app.route("/api/clipboard", methods=["POST"])
def api_clipboard():
    """Receive clipboard content (text/image) and save to today's date folder."""
    data = request.get_json(force=True)
    content_type = data.get("type", "text")

    ts = datetime.now().strftime("%H%M%S")
    date_dir = get_date_dir()

    if content_type == "image":
        import base64
        img_data = data.get("data", "")
        if img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[1]
        filename = f"paste_{ts}.png"
        path = date_dir / filename
        path.write_bytes(base64.b64decode(img_data))
    else:
        content = data.get("content", "")
        filename = f"{ts}.txt"
        path = date_dir / filename
        path.write_text(content, encoding="utf-8")

    meta = file_metadata(path)
    broadcast_event("new_files", {"files": [meta]})
    return jsonify({"status": "ok", "file": meta})


@app.route("/api/stream")
def api_stream():
    """SSE endpoint for real-time updates."""
    def event_stream():
        q: queue.Queue = queue.Queue(maxsize=64)
        with sse_lock:
            sse_clients.append(q)
        try:
            # Send initial heartbeat
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/discover")
def api_discover():
    """Scan LAN for other transfer servers."""
    servers = discover_servers()
    return jsonify({"servers": servers})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="LAN File Transfer Tool - 局域网文件传输工具"
    )
    parser.add_argument("-p", "--port", type=int, default=5000,
                        help="HTTP server port (default: 5000)")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="Disable auto-cleanup of old files")
    parser.add_argument("--no-discovery", action="store_true",
                        help="Disable LAN discovery")
    return parser.parse_args()


def main():
    args = parse_args()
    port = args.port

    local_ip = get_ip()

    # Start cleanup thread
    if not args.no_cleanup:
        t = threading.Thread(target=cleanup_loop, daemon=True)
        t.start()

    # Start discovery listener
    if not args.no_discovery:
        t = threading.Thread(target=discovery_listener, args=(port,), daemon=True)
        t.start()

    # Print startup banner
    print()
    print("=" * 60)
    print("  📁  LAN File Transfer Tool - 局域网文件传输工具")
    print("=" * 60)
    print()
    print(f"  🌐  Local:    http://{local_ip}:{port}")
    print(f"  🏠  Localhost: http://127.0.0.1:{port}")
    print()
    print("  📤  Send files, images, or text from one device")
    print("  📥  Receive on another device on the same LAN")
    print()
    print("  💡  Open the above address in a browser on BOTH")
    print("      the sender's and receiver's devices.")
    print()
    print("  ⚠️   IMPORTANT:  Type the FULL address including http://")
    print("      Browsers may auto-upgrade to https:// — that won't work.")
    print("      If you see 'Bad request' or TLS errors, check the URL bar!")
    print()
    print("  Press Ctrl+C to stop the server")
    print("=" * 60)
    print()

    # Show all detected IPs if > 1
    all_ips = get_all_local_ips()
    if len(all_ips) > 1:
        print("  Detected network interfaces:")
        for ip in all_ips:
            print(f"    • http://{ip}:{port}")
        print()

    # Build a custom WSGI server that intercepts TLS ClientHello before
    # the HTTP parser chokes on it, and returns a friendly redirect page.
    from werkzeug.serving import make_server, WSGIRequestHandler

    class TLSDetectHandler(WSGIRequestHandler):
        """Custom handler: peek at first byte; if TLS, send redirect page."""

        def handle(self):
            try:
                peek = self.connection.recv(1, socket.MSG_PEEK)
                if peek and peek[0] == 0x16:
                    # TLS ClientHello detected
                    body = _build_tls_redirect_page(
                        self.server.server_address[0], port
                    )
                    resp = (
                        "HTTP/1.1 400 Bad Request\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n"
                        "\r\n"
                    ).encode() + body
                    self.connection.sendall(resp)
                    self.connection.close()
                    return
            except OSError:
                pass
            # Not TLS — let the normal WSGI handler process it
            super().handle()

    server = make_server(
        host="0.0.0.0",
        port=port,
        app=app,
        threaded=True,
        request_handler=TLSDetectHandler,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n  👋 Server stopped. Goodbye!\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
