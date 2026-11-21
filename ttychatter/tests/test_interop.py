#!/usr/bin/env python3
"""Offline session interchange through the real C, Bash, Python and ncurses clients.

Build c/ttychatter first, then run:
    python3 -B -m unittest discover -s tests -p test_interop.py -v

TTYCHATTER_TEST_C_BINARY can select another C build. Bash JSON interchange
requires jq. Ncurses sends use a real PTY; exact memory inspection uses its
own parser because it has no memory CLI. Shell memory inspection likewise
uses the real helpers. No model, mailbox, or other network service is used.
"""

import errno
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
C_BINARY = Path(os.environ.get("TTYCHATTER_TEST_C_BINARY", ROOT / "c/ttychatter")).resolve()
CLIENTS = ("c", "python", "ncurses", "bash")
NCURSES_SOURCE = ROOT / "nonc/ttychatter.ncurses"
loader = importlib.machinery.SourceFileLoader("ttychatter_interop_ncurses", str(NCURSES_SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
ncurses = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = ncurses
loader.exec_module(ncurses)


@unittest.skipUnless(C_BINARY.is_file(), "Build c/ttychatter or set TTYCHATTER_TEST_C_BINARY first.")
@unittest.skipUnless(shutil.which("jq"), "Install jq for four-client JSON snapshot interchange.")
class SessionInterop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Freeze one build so a concurrent make cannot replace an executable
        # between the many short CLI invocations in this interchange test.
        cls.binary_temp = tempfile.TemporaryDirectory(prefix="ttychatter-interop-build-")
        cls.addClassCleanup(cls.binary_temp.cleanup)
        cls.binary = Path(cls.binary_temp.name) / "ttychatter"
        shutil.copy2(C_BINARY, cls.binary)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ttychatter-interop-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.logs = self.base / "data/ttychatter/openrouter/sessions"
        self.logs.mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.base), TERM="xterm-256color",
                        XDG_CONFIG_HOME=str(self.base / "config"), XDG_DATA_HOME=str(self.base / "data"),
                        XDG_CACHE_HOME=str(self.base / "cache"), XDG_STATE_HOME=str(self.base / "state"))
        for key in ("OPENROUTER_API_KEY", "TTYCHATTER_API_KEY", "API_KEY", "EDITOR", "VISUAL"):
            self.env.pop(key, None)
        config = self.base / "config/ttychatter/openrouter/config"
        config.parent.mkdir(parents=True)
        config.write_text("DEMO_MODE=1\nMODEL=demo/auto\nSTARTUP_NOTICE=0\n"
                          "PROMPT_SESSION_NAME=0\nSESSION_AUTO_TITLE=0\nCONTEXT_TURNS=100\n"
                          "WEB_SEARCH=0\nAI_FILE_DOWNLOAD_MODE=off\nSTREAM=0\nSEND_INPUT=enter\n", encoding="utf-8")
        self.cfg = ncurses.Config(config_file=config, session_dir=self.logs,
                                  attachment_dir=self.base / "files", demo_mode=True,
                                  context_turns=100, startup_notice=False, prompt_session_name=False)
        self.commands = {"c": [str(self.binary)], "ncurses": [sys.executable, str(NCURSES_SOURCE)]}
        self.helpers = {}
        for variant in ("python", "bash"):
            source = ROOT / "nonc" / ("ttychatter." + variant)
            self.commands[variant] = ["bash", str(source)]
            text = source.read_text(encoding="utf-8")
            helper = self.base / (variant + "-helpers.sh")
            helper.write_text(text[:text.index('\nwhile [ "$#" -gt 0 ]; do')], encoding="utf-8")
            self.helpers[variant] = helper

    def cli(self, variant, *args, data=""):
        result = subprocess.run(self.commands[variant] + ["--demo", *map(str, args)],
                                input=data, text=True, capture_output=True, env=self.env, timeout=15)
        self.assertEqual(result.returncode, 0, f"{variant} {args!r}\n{result.stderr}\n{result.stdout}")
        return result

    def shell_helper(self, variant, path, body):
        command = "source " + shlex.quote(str(self.helpers[variant]))
        command += "\nSESSION=" + shlex.quote(str(path)) + "\n" + body
        result = subprocess.run(["bash", "-c", command], env=self.env,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def assert_memory(self, path, expected):
        for reader in CLIENTS:
            with self.subTest(reader=reader, file=path.name):
                if reader == "c":
                    want = "".join(item.split(": ", 1)[0].lower() + ": " + item.split(": ", 1)[1] + "\n"
                                   for item in expected) if expected else "[memory empty]\n"
                    self.assertEqual(self.cli(reader, "--show-memory", path).stdout, want)
                elif reader == "ncurses":
                    app = ncurses.AICursesApp(self.cfg, path.stem, path)
                    app.load_session_into_ui()
                    self.assertEqual(app.context_buffer, expected)
                else:
                    load = "rebuild_context_from_session" if reader == "python" else "load_context"
                    raw = self.shell_helper(reader, path, load + '\nif [ "${#CONTEXT_BUFFER[@]}" -gt 0 ]; then printf \'%s\\0\' "${CONTEXT_BUFFER[@]}"; fi')
                    actual = [item.decode("utf-8") for item in raw.split(b"\0")[:-1]] if raw else []
                    self.assertEqual(actual, expected)

    def ncurses_send(self, selector, prompt, resume=False):
        pid, fd = pty.fork()
        if pid == 0:
            fcntl.ioctl(1, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
            args = self.commands["ncurses"] + ["--demo", "--resume" if resume else "--session", selector]
            os.execve(sys.executable, args, self.env)
        tail = bytearray()

        def read_until(token):
            pending = bytearray()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    pending.extend(chunk)
                    tail.extend(chunk)
                    if token in pending:
                        return
            self.fail(f"ncurses did not show {token!r}: {bytes(tail[-3000:])!r}")

        try:
            read_until(b"Draft |")
            os.write(fd, prompt.encode("ascii") + b"\r")
            read_until(b"Response complete")
            os.write(fd, b"\x1b[21~")
            read_until(b"CLOSE SESSION")
            os.write(fd, b"y")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                child, status = os.waitpid(pid, os.WNOHANG)
                if child:
                    pid = None
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                    return
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        os.read(fd, 65536)
                    except OSError:
                        pass
            self.fail("ncurses did not exit after confirming F10")
        finally:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
            os.close(fd)

    def send(self, variant, selector, prompt, resume=False):
        if variant == "ncurses":
            self.ncurses_send(selector, prompt, resume)
        elif variant == "c" and resume:
            # C --resume enters its line-oriented command loop.
            self.cli(variant, "--resume", selector, data=prompt + "\n:q\n")
        else:
            self.cli(variant, "--resume" if resume else "--session", selector, "--prompt", prompt)

    def identity(self, path):
        ids = re.findall(r"^\[\d\d:\d\d:\d\d\] system: session-id: ([0-9a-f]{32})$",
                         path.read_text(encoding="utf-8"), re.M)
        self.assertEqual(len(ids), 1, f"Expected one stable identity in {path}")
        return ids[0]

    def rename(self, variant, selector, title):
        result = self.cli(variant, "--rename-session", selector, title)
        markers = re.findall(r"^session: (.+)$", result.stdout + "\n" + result.stderr, re.M)
        self.assertTrue(markers, result)
        path = Path(markers[-1])
        self.assertEqual(path.parent, self.logs)
        self.assertTrue(path.is_file(), result)
        return path

    def append(self, path, text):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def test_created_sessions_survive_two_other_clients_renames_and_old_selectors(self):
        for index, creator in enumerate(CLIENTS):
            with self.subTest(creator=creator):
                selector = "origin-" + creator
                before = set(self.logs.glob("*.log"))
                self.send(creator, selector, "initial turn from " + creator)
                new_logs = set(self.logs.glob("*.log")) - before
                self.assertEqual(len(new_logs), 1)
                original = new_logs.pop()
                identity = self.identity(original)
                memory = ["User: retained question\nAssistant: literal text within that question",
                          "Assistant: retained reply\nsecond reply line"]
                self.append(original, "[12:00:00] system: CONTEXT_SNAPSHOT_JSON " + json.dumps(memory) + "\n"
                            "[12:00:01] Human: later question\ncontinued question\n"
                            "[12:00:02] Assistant: later reply\ncontinued reply\n"
                            '[12:00:03] system: openrouter-response: {"usage":{"total_tokens":17}}\n')
                expected = memory + ["User: later question\ncontinued question", "Assistant: later reply\ncontinued reply"]
                self.assert_memory(original, expected)
                first_title = "First shared title " + creator
                first = self.rename(CLIENTS[(index + 1) % 4], original.name, first_title)
                self.assertFalse(original.exists())
                second_title = "Second shared title " + creator
                second = self.rename(CLIENTS[(index + 2) % 4], first.name, second_title)
                self.assertFalse(first.exists())
                self.assertEqual(self.identity(second), identity)
                self.assertIn(identity[:12], second.name)
                self.assert_memory(second, expected)
                for reader in CLIENTS:
                    for lookup in (selector, first_title, second_title):
                        with self.subTest(exporter=reader, selector=lookup):
                            exported = self.cli(reader, "--export", lookup, "--format", "text").stdout
                            self.assertIn("initial turn from " + creator, exported)
                            self.assertIn("later question\ncontinued question", exported)
                            self.assertIn("later reply\ncontinued reply", exported)
                            self.assertNotIn("CONTEXT_SNAPSHOT_JSON", exported)
                            self.assertNotIn("openrouter-response:", exported)
                self.send(creator, selector, "resumed through original selector", resume=True)
                self.send(creator, first_title, "resumed through previous human title", resume=True)
                self.assertEqual(self.identity(second), identity)
                self.assertEqual(set(self.logs.glob("*.log")) - before, {second})
                text = second.read_text(encoding="utf-8")
                self.assertIn("resumed through original selector", text)
                self.assertIn("resumed through previous human title", text)

    def test_latest_json_and_legacy_snapshots_with_later_turns(self):
        cases = [
            ("json-after-legacy", '[12:00:00] system: CONTEXT_BEGIN\nUser: obsolete\n[12:00:01] system: CONTEXT_END\n'
             '[12:00:02] system: CONTEXT_SNAPSHOT_JSON ["User: revised\\nsecond line", "Assistant: saved reply"]\n',
             ["User: revised\nsecond line", "Assistant: saved reply"]),
            ("legacy-after-json", '[12:00:00] system: CONTEXT_SNAPSHOT_JSON ["User: obsolete"]\n'
             '[12:00:01] system: CONTEXT_BEGIN\nUser: revised\nsecond line\nAssistant: saved reply\n[12:00:02] system: CONTEXT_END\n',
             ["User: revised\nsecond line", "Assistant: saved reply"]),
            ("empty-json", '[12:00:00] Human: forgotten\n[12:00:01] system: CONTEXT_SNAPSHOT_JSON []\n', []),
        ]
        for name, log, expected in cases:
            with self.subTest(snapshot=name):
                path = self.logs / (name + ".log")
                path.write_text(log, encoding="utf-8")
                self.assert_memory(path, expected)
                self.append(path, "[12:00:03] Human: after snapshot\nsecond new line\n")
                self.assert_memory(path, expected + ["User: after snapshot\nsecond new line"])

    def test_each_clients_empty_json_snapshot_clears_every_reader(self):
        for writer in CLIENTS:
            with self.subTest(writer=writer):
                path = self.logs / ("clear-" + writer + ".log")
                path.write_text('[12:00:00] system: CONTEXT_SNAPSHOT_JSON ["User: forgotten\\nsecond line"]\n', encoding="utf-8")
                if writer in ("c", "python"):
                    self.cli(writer, "--clear-memory", path)
                elif writer == "ncurses":
                    ncurses.append_context_snapshot(path, [])
                else:
                    self.shell_helper(writer, path, 'CONTEXT_BUFFER=(); write_context_snapshot "12:00:01"')
                snapshots = re.findall(r"^\[[^]]+\] system: CONTEXT_SNAPSHOT_JSON (.*)$", path.read_text(), re.M)
                self.assertGreaterEqual(len(snapshots), 2)
                self.assertEqual(json.loads(snapshots[-1]), [])
                self.assert_memory(path, [])
                self.append(path, "[12:00:02] Human: after clear\n")
                self.assert_memory(path, ["User: after clear"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
