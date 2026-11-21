#!/usr/bin/env python3
"""Exercise the actual libcurl stream and terminal cleanup on loopback only."""
import contextlib
import http.server
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import termios
import threading
import time


BINARY = str(Path(sys.argv[1]).resolve())
ENV = dict(os.environ, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")


@contextlib.contextmanager
def server(wire, hold_open):
    release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(f"{len(wire):x}\r\n".encode() + wire + b"\r\n")
            self.wfile.flush()
            if hold_open:
                release.wait(8)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/stream"
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def read_until(fd, token):
    text = b""
    deadline = time.monotonic() + 5
    while token not in text and time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.1)
        if readable:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            text += chunk
    assert token in text, text


complete = b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n'
with server(complete, hold_open=True) as url:
    # The server intentionally keeps the HTTP body open after [DONE].
    result = subprocess.run([BINARY, url, "http"], env=ENV, capture_output=True,
                            timeout=3)
    assert result.returncode == 0, result.stderr

partial = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
with server(partial, hold_open=False) as url:
    result = subprocess.run([BINARY, url, "http-truncated"], env=ENV,
                            capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr

with server(partial, hold_open=True) as url:
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    process = subprocess.Popen([BINARY, url, "http-cancel"], env=ENV,
                               stdin=slave, stdout=slave, stderr=slave)
    try:
        read_until(master, b"partial")
        os.write(master, b"\x1b")
        process.wait(timeout=4)
        assert process.returncode == 0
        assert termios.tcgetattr(slave) == original
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)

for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    with server(partial, hold_open=True) as url:
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        process = subprocess.Popen([BINARY, url, "http-cancel"], env=ENV,
                                   stdin=slave, stdout=slave, stderr=slave)
        try:
            read_until(master, b"partial")
            os.kill(process.pid, signum)
            process.wait(timeout=3)
            assert process.returncode == -signum, (signum, process.returncode)
            assert termios.tcgetattr(slave) == original, signum
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)

with server(partial, hold_open=True) as url:
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    # The parent stays in the same session but outside this process group, so
    # the default TSTP action is not suppressed for an orphaned process group.
    process = subprocess.Popen([BINARY, url, "http-cancel"], env=ENV,
                               stdin=slave, stdout=slave, stderr=slave,
                               preexec_fn=os.setpgrp)
    try:
        read_until(master, b"partial")
        streaming = termios.tcgetattr(slave)
        os.kill(process.pid, signal.SIGTSTP)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pid, status = os.waitpid(process.pid, os.WUNTRACED | os.WNOHANG)
            if pid:
                assert os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGTSTP
                break
            time.sleep(0.01)
        else:
            raise AssertionError("stream did not stop on SIGTSTP")
        assert termios.tcgetattr(slave) == original
        os.kill(process.pid, signal.SIGCONT)
        deadline = time.monotonic() + 3
        while termios.tcgetattr(slave) != streaming and time.monotonic() < deadline:
            time.sleep(0.01)
        assert termios.tcgetattr(slave) == streaming
        os.write(master, b"\x1b")
        process.wait(timeout=4)
        assert process.returncode == 0
        assert termios.tcgetattr(slave) == original
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)

print("C loopback HTTP completion, truncation, ESC, signals, suspend/resume, and terminal cleanup passed")
