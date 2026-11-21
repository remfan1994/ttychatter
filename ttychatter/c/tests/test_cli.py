#!/usr/bin/env python3
"""Offline CLI and real-TTY regressions for session naming and overrides."""
import errno
import json
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import sys
import tempfile
import time

BINARY = str(Path(sys.argv[1] if len(sys.argv) > 1 else "./ttychatter").resolve())


def run(env, *args, data=None, ok=True):
    completed = subprocess.run([BINARY, *args], input=data, text=True, env=env,
                               capture_output=True, timeout=15)
    if ok:
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode != 0, completed.stdout
    return completed


def session_path(output):
    matches = re.findall(r"^session: (.+)$", output, re.M)
    assert matches, output
    return Path(matches[-1])


def read_until(fd, token, seconds=10):
    result = b""
    deadline = time.monotonic() + seconds
    while token not in result:
        assert time.monotonic() < deadline, (token, result[-2000:])
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            result += chunk
    assert token in result, (token, result[-2000:])
    return result


with tempfile.TemporaryDirectory(prefix="ttychatter-cli-check-") as root:
    base = Path(root)
    env = os.environ.copy()
    for key in ("OPENROUTER_API_KEY", "TTYCHATTER_API_KEY", "VISUAL", "EDITOR"):
        env.pop(key, None)
    env.update(XDG_CONFIG_HOME=str(base / "config"), XDG_DATA_HOME=str(base / "data"),
               XDG_CACHE_HOME=str(base / "cache"), TERM="xterm-256color")
    config = base / "config/ttychatter/openrouter/config"
    first = run(env, "--demo", "--prompt", "first", "--session", "named")
    original = session_path(first.stderr)
    assert original.name == "named.log"
    assert "session-title: named" in original.read_text()
    assert "Name it now" not in first.stderr
    identifier = re.search(r"session-id: ([0-9a-f]{32})", original.read_text()).group(1)
    renamed = run(env, "--rename-session", "named", "Shared human title")
    target = session_path(renamed.stderr)
    assert target.name == "Shared-human-title--" + identifier[:12] + ".log"
    assert target.exists() and not original.exists()
    resumed = run(env, "--demo", "--prompt", "second", "--session", "Shared human title")
    assert session_path(resumed.stderr) == target
    assert target.read_text().count("session-id:") == 1
    assert "second" in target.read_text()
    for previous_name in ("named", "named.log"):
        exported = run(env, "--export", previous_name, "--format", "text")
        assert "first" in exported.stdout and "second" in exported.stdout
        assert "session-alias:" not in exported.stdout

    # A friendly-title collision must require an exact filename.
    run(env, "--demo", "--prompt", "another session", "--session", "collision-owner")
    run(env, "--rename-session", "collision-owner", "Shared human title")
    duplicate = run(env, "--demo", "--resume", "Shared human title", data=":q\n", ok=False)
    assert "ambiguous" in duplicate.stderr
    assert target.exists()

    run(env, "--set", "WEB_SEARCH=1")
    run(env, "--set", "WEB_SEARCH_ENGINE=exa")
    old_config = config.read_bytes()
    opts = run(env, "--demo", "--web", "--web-engine", "parallel", "--web-max-results", "4",
               "--web-max-uses", "2", "--prompt", "override test")
    assert config.read_bytes() == old_config
    assert session_path(opts.stderr).exists()
    for args in [("--web-max-results", "26"), ("--web-max-uses", "26"), ("--web-engine", "invalid")]:
        run(env, "--demo", *args, "--prompt", "must not send", ok=False)
    zero = run(env, "--demo", "--web", "--web-max-results", "0", "--web-max-uses", "0", "--prompt", "zero limits")
    assert session_path(zero.stderr).exists()
    run(env, "--set", "WEB_SEARCH_MAX_RESULTS=0")
    assert "WEB_SEARCH_MAX_RESULTS=0" in config.read_text()
    run(env, "--set", "WEB_SEARCH_MAX_RESULTS=5")
    run(env, "--resume", "missing-session", "--demo", ok=False)

    # Automatic titles rename managed logs, and explicit outputs remain fixed.
    run(env, "--set", "SESSION_AUTO_TITLE=1")
    automatic = run(env, "--demo", "--prompt", "Generated session title")
    assert "Generated-session-title--" in session_path(automatic.stderr).name
    explicit = base / "explicit.log"
    run(env, "--demo", "--prompt", "Keep explicit filename", "--output", str(explicit))
    assert explicit.exists() and "session-title:" in explicit.read_text()
    run(env, "--set", "SESSION_AUTO_TITLE=0")

    # Piped interactive commands are never consumed as a naming answer.
    attach = base / "attachment.txt"
    attach.write_text("queued from the command line")
    piped = run(env, "--demo", "--interactive", "--new", "--web", "--attach", str(attach),
                data=":web\n:web status\n" + "x" * 70000 + "\n:q\n")
    assert "Name it now" not in piped.stderr
    assert piped.stderr.count("web search: on") == 2
    saved = session_path(piped.stderr).read_text()
    assert len(re.findall(r"^\[\d\d:\d\d:\d\d\] User:", saved, re.M)) == 1
    assert "attachment: " + str(attach) in saved
    assert "x" * 70000 in saved

    # The interactive selector changes this run even when config saving is declined.
    selection = run(env, "--demo", "--interactive", "--new",
                    data=":select-model\n1\nn\nmodel check\n:q\n")
    assert "runtime model: demo/fixed-large" in selection.stderr
    assert "Demo response from model demo/fixed-large" in selection.stdout

    # Saving an unchanged memory editor buffer preserves embedded role labels.
    mem = run(env, "--demo", "--session", "memory-edit", "--prompt", "first\nUser: literal role label\n  last ")
    memory_path = session_path(mem.stderr)
    before = run(env, "--show-memory", "memory-edit").stdout
    editor = base / "memory-editor.sh"
    editor.write_text("#!/bin/sh\nexit 0\n")
    editor.chmod(0o700)
    editor_env = dict(env, EDITOR=str(editor))
    run(editor_env, "--edit-memory", "memory-edit")
    assert run(env, "--show-memory", "memory-edit").stdout == before
    snapshots = re.findall(r"system: CONTEXT_SNAPSHOT_JSON (.*)", memory_path.read_text())
    assert json.loads(snapshots[-1])[0] == "User: first\nUser: literal role label\n  last "
    run(env, "--clear-memory", "memory-edit")
    snapshots = re.findall(r"system: CONTEXT_SNAPSHOT_JSON (.*)", memory_path.read_text())
    assert json.loads(snapshots[-1]) == []

    # An actual terminal asks at entry and once after the first answer if skipped.
    master, slave = pty.openpty()
    process = subprocess.Popen([BINARY, "--demo", "--interactive", "--new"], env=env,
                               stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    transcript = b""
    try:
        transcript += read_until(master, b"Enter skips): ")
        os.write(master, b"\n")
        transcript += read_until(master, b"ttychatter> ")
        os.write(master, b"first terminal turn\n")
        transcript += read_until(master, b"Enter skips): ")
        os.write(master, b"Remembered after reply\n")
        transcript += read_until(master, b"ttychatter> ")
        os.write(master, b":rename Final terminal name\n")
        transcript += read_until(master, b"ttychatter> ")
        os.write(master, b":web off\n")
        transcript += read_until(master, b"ttychatter> ")
        os.write(master, b"after physical rename\n")
        transcript += read_until(master, b"ttychatter> ")
        os.write(master, b":q\n")
        process.wait(timeout=10)
        while select.select([master], [], [], 0)[0]:
            try:
                transcript += os.read(master, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
    text = transcript.decode(errors="replace").replace("\r", "")
    final = session_path(text)
    assert final.name.startswith("Final-terminal-name--")
    assert "first terminal turn" in final.read_text()
    assert "after physical rename" in final.read_text()
    assert text.count("This session is unnamed.") == 2
    assert "web search: off" in text
    assert len(list(final.parent.glob("Remembered-after-reply--*.log"))) == 0

print("C offline CLI and PTY regressions passed")
