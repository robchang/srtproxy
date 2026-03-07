#!/usr/bin/env python3
"""
SRT-to-HLS proxy server.

Starts FFmpeg on-demand when a viewer explicitly requests it,
and stops it after a period of inactivity to save resources.
Auto-restarts FFmpeg on unexpected crashes while viewers are active.
"""

import glob
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time

SRT_URL = os.environ.get("SRT_URL", "srt://96.77.253.169:3039")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "9090"))
IDLE_TIMEOUT = int(os.environ.get("IDLE_TIMEOUT", "60"))
HLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hls")
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

os.makedirs(HLS_DIR, exist_ok=True)


class FFmpegManager:
    """Manages the FFmpeg process lifecycle based on viewer activity."""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._last_activity = 0.0
        self._watchdog_thread = None
        self._intentional_stop = False

    def start(self):
        """Explicitly start FFmpeg (called from /api/start)."""
        self._last_activity = time.time()
        with self._lock:
            self._intentional_stop = False
            if self._proc is not None and self._proc.poll() is None:
                return
            self._start()

    def heartbeat(self):
        """Update activity timestamp (called from /hls/ requests)."""
        self._last_activity = time.time()

    def _start(self):
        self._cleanup_hls_files()
        cmd = [
            "ffmpeg",
            "-y",
            "-i", SRT_URL,
            "-c:v", "copy",
            "-c:a", "copy",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", os.path.join(HLS_DIR, "segment_%03d.ts"),
            os.path.join(HLS_DIR, "stream.m3u8"),
        ]
        print(f"[ffmpeg] Starting (viewer connected)")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t = threading.Thread(target=self._log_stderr, daemon=True)
        t.start()
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
            self._watchdog_thread.start()

    def _log_stderr(self):
        proc = self._proc
        if proc and proc.stderr:
            for line in proc.stderr:
                print(f"[ffmpeg] {line.decode(errors='replace').rstrip()}")

    def _watchdog(self):
        while True:
            time.sleep(5)
            with self._lock:
                if self._intentional_stop:
                    return

                proc_dead = self._proc is None or self._proc.poll() is not None
                idle = time.time() - self._last_activity

                # If idle too long, stop intentionally
                if not proc_dead and idle >= IDLE_TIMEOUT:
                    print(f"[ffmpeg] No viewers for {IDLE_TIMEOUT}s, stopping")
                    self._intentional_stop = True
                    self._stop()
                    return

                # If FFmpeg crashed but viewers are still active, restart
                if proc_dead and idle < IDLE_TIMEOUT:
                    print("[ffmpeg] Process crashed, restarting for active viewers")
                    self._start()

    def _stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._cleanup_hls_files()

    def _cleanup_hls_files(self):
        for f in glob.glob(os.path.join(HLS_DIR, "*")):
            try:
                os.remove(f)
            except OSError:
                pass

    def stop(self):
        with self._lock:
            self._intentional_stop = True
            self._stop()

    @property
    def is_running(self):
        with self._lock:
            return self._proc is not None and self._proc.poll() is None


ffmpeg_mgr = FFmpegManager()


class StreamHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.serve_file(os.path.join(ROOT_DIR, "index.html"), "text/html")
        elif self.path == "/api/start":
            ffmpeg_mgr.start()
            self.send_json({"status": "started"})
        elif self.path == "/api/status":
            self.send_json({"running": ffmpeg_mgr.is_running})
        elif self.path.startswith("/hls/"):
            ffmpeg_mgr.heartbeat()
            filename = os.path.basename(self.path[len("/hls/"):])
            filepath = os.path.join(HLS_DIR, filename)
            if os.path.isfile(filepath):
                if filename.endswith(".m3u8"):
                    content_type = "application/vnd.apple.mpegurl"
                elif filename.endswith(".ts"):
                    content_type = "video/mp2t"
                else:
                    content_type = "application/octet-stream"
                self.serve_file(filepath, content_type)
            else:
                self.send_error(404, "Not found")
        else:
            self.send_error(404, "Not found")

    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filepath, content_type):
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404, "Not found")

    def log_message(self, format, *args):
        pass


def main():
    server = http.server.HTTPServer(("0.0.0.0", HTTP_PORT), StreamHandler)
    print(f"[server] Web player available at http://localhost:{HTTP_PORT}")
    print(f"[server] FFmpeg will start on viewer request, stop after {IDLE_TIMEOUT}s idle")

    def shutdown(sig, frame):
        print("\n[server] Shutting down...")
        ffmpeg_mgr.stop()
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
