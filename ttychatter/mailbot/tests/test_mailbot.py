"""Offline regression tests for mail transport, backend wiring, and sessions.

Run: python3 -m unittest discover -s mailbot/tests -v
No mailbox, SMTP server, or OpenRouter account is used.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "ttychatter.mailbot"
LOADER = importlib.machinery.SourceFileLoader("ttychatter_mailbot_tested", str(SOURCE))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
bot = importlib.util.module_from_spec(SPEC)
sys.modules[LOADER.name] = bot
LOADER.exec_module(bot)

SESSION_ID = "0123456789abcdef0123456789abcdef"


class MailbotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ttychatter-mailbot-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = bot.Config(
            config_file=self.root / "mailbot.conf",
            session_dir=self.root / "sessions",
            attachment_dir=self.root / "attachments",
            legacy_openrouter_config_file=self.root / "ttychatter.conf",
            work_dir=self.root / "work",
            processed_db=self.root / "processed.txt",
            public_session_index_file=self.root / "public.json",
            mail_from="bot@example.test", mail_to="",
            user_registry=False,
        )
        self.cfg.session_dir.mkdir()

    def session(self, name="old", title="", identity=SESSION_ID):
        path = self.cfg.session_dir / f"{name}.log"
        contents = f"[10:00:00] system: session-id: {identity}\n"
        if title:
            contents += f"[10:00:00] system: session-title: {title}\n"
        contents += "[10:00:01] user: original question\n[10:00:02] assistant: original answer\n"
        path.write_text(contents, encoding="utf-8")
        return path

    def email(self, subject="tc:{new}"):
        msg = bot.EmailMessage()
        msg["From"] = "reader@example.test"
        msg["To"] = self.cfg.mail_from
        msg["Subject"] = subject
        msg["Message-ID"] = "<input@example.test>"
        msg.set_content("hello")
        return msg

    def test_web_defaults_user_preference_and_cli_precedence(self):
        self.assertFalse(bot.effective_web_settings(self.cfg)["WEB_SEARCH"])
        self.cfg.web_search = True
        self.cfg.legacy_openrouter_config_file.write_text("WEB_SEARCH=0\nWEB_SEARCH_ENGINE=perplexity\nWEB_SEARCH_MAX_RESULTS=25\n")
        settings = bot.effective_web_settings(self.cfg)
        self.assertFalse(settings["WEB_SEARCH"])
        self.assertEqual(settings["WEB_SEARCH_MAX_RESULTS"], 20)
        self.cfg.web_cli_overrides = {"WEB_SEARCH": "1", "WEB_SEARCH_ENGINE": "exa", "WEB_SEARCH_MAX_USES": "2"}
        settings = bot.effective_web_settings(self.cfg)
        self.assertTrue(settings["WEB_SEARCH"])
        self.assertEqual(settings["WEB_SEARCH_ENGINE"], "exa")
        self.assertEqual(settings["WEB_SEARCH_MAX_USES"], 2)

    def test_web_settings_validate_before_spawning_backend(self):
        self.cfg.web_search_max_results = 26
        with mock.patch.object(bot, "run_backend_process") as run:
            rc, output, errors = bot.run_ttychatter(self.cfg, "topic", "hello", [])
        self.assertEqual(rc, 2)
        self.assertIn("0 to 25", errors)
        run.assert_not_called()
        self.cfg.web_search_max_results = 0
        self.cfg.web_search_max_uses = 0
        with mock.patch.object(bot, "run_backend_process", return_value=(0, "ok", "")) as run:
            rc, output, errors = bot.run_ttychatter(self.cfg, "topic", "hello", [])
        self.assertEqual(rc, 0)
        command = run.call_args.args[1]
        self.assertEqual(command[command.index("--web-max-results") + 1], "0")
        self.assertEqual(command[command.index("--web-max-uses") + 1], "0")
        with self.assertRaises(ValueError):
            bot.set_config_value(self.cfg.config_file, "WEB_SEARCH_ENGINE", "invented")

    def test_web_cli_does_not_enable_search_when_omitted(self):
        for args, expected in [([], None), (["--web"], True), (["--no-web"], False)]:
            with self.subTest(args=args), mock.patch.object(sys, "argv", [str(SOURCE)] + args):
                self.assertIs(bot.parse_args().web_search, expected)

    def test_web_flags_reach_every_backend_and_subject_title_is_retained(self):
        for backend, executable in [("python", "ttychatter.python"), ("bash", "ttychatter.bash"), ("c", "ttychatter")]:
            with self.subTest(backend=backend):
                self.cfg.ttychatter_backend = backend
                self.cfg.web_search = True
                self.cfg.web_search_engine = "exa"
                self.cfg.web_search_max_results = 4
                self.cfg.web_search_max_uses = 2
                self.cfg.ttychatter_extra_args = "--demo"
                title = f"Build notes {backend}"

                def run(cfg, cmd, body, timeout):
                    self.assertEqual(cmd[0], executable)
                    self.assertIn("--demo", cmd)
                    self.assertIn("--web", cmd)
                    self.assertEqual(cmd[cmd.index("--web-engine") + 1], "exa")
                    self.assertEqual(cmd[cmd.index("--web-max-results") + 1], "4")
                    self.assertEqual(cmd[cmd.index("--web-max-uses") + 1], "2")
                    self.assertEqual(body, "hello")
                    self.assertEqual(timeout, 600)
                    target = Path(cmd[cmd.index("--session") + 1])
                    target.write_text(f"[10:00:00] system: session-id: {SESSION_ID}\n[10:00:01] assistant: answer\n")
                    return 0, "answer", f"session: {target}"

                with mock.patch.object(bot, "run_backend_process", side_effect=run):
                    rc, output, errors = bot.run_ttychatter(self.cfg, title, "hello", [])
                self.assertEqual((rc, output), (0, "answer"))
                actual = bot.session_log_path(self.cfg, title)
                self.assertEqual(bot.session_title_from_file(actual), title)
                self.assertEqual(bot.display_session_name(title, errors), actual.stem)

    def test_web_command_uses_correct_backend_set_syntax(self):
        self.cfg.runtime_ttychatter_config_file = self.cfg.legacy_openrouter_config_file
        for backend in ("c", "python", "bash"):
            with self.subTest(backend=backend):
                self.cfg.ttychatter_backend = backend

                def run(cfg, args, **kwargs):
                    self.assertEqual(args, ["--set", "WEB_SEARCH=1"] if backend == "c" else ["--set", "WEB_SEARCH", "1"])
                    cfg.runtime_ttychatter_config_file.write_text("WEB_SEARCH=1\n")
                    return 0, "saved", ""

                with mock.patch.object(bot, "run_ttychatter_cli", side_effect=run):
                    ok, output, attachments = bot.dispatch_mailbot_command(self.cfg, "web on")
                self.assertTrue(ok)
                self.assertIn("Web search: on", output)
        request = bot.parse_subject_request("MAILBOT", self.cfg, "web status\n")
        self.assertEqual((request.kind, request.value), ("command", "web status"))

    def test_new_subject_title_replaces_backend_filename_placeholder(self):
        def run(cfg, cmd, body, timeout):
            target = Path(cmd[cmd.index("--session") + 1])
            target.write_text(
                f"[10:00:00] system: session-id: {SESSION_ID}\n"
                f"[10:00:00] system: session-title: {target.stem}\n"
                "[10:00:01] assistant: answer\n"
            )
            return 0, "answer", f"session: {target}"

        with mock.patch.object(bot, "run_backend_process", side_effect=run):
            rc, output, errors = bot.run_ttychatter(self.cfg, "Build notes", "hello", [])
        self.assertEqual(rc, 0)
        target = bot.session_log_path(self.cfg, "Build notes")
        self.assertEqual(bot.session_title_from_file(target), "Build notes")
        bot.append_session_title_metadata(target, "Chosen later by the user")
        with mock.patch.object(bot, "run_backend_process", return_value=(0, "answer", f"session: {target}")):
            bot.run_ttychatter(self.cfg, "Build notes", "continue", [])
        self.assertEqual(bot.session_title_from_file(target), "Chosen later by the user")

    def test_utility_commands_keep_operator_extra_args(self):
        self.cfg.ttychatter_extra_args = "--demo"
        with mock.patch.object(bot, "run_backend_process", return_value=(0, "ok", "")) as run:
            bot.run_ttychatter_cli(self.cfg, ["--update-models"])
        self.assertIn("--demo", run.call_args.args[1])

    def test_old_subject_resolves_after_manual_backend_rename(self):
        old = self.session("Work_notes")
        bot.remember_session_reference(self.cfg, "Work notes", old)
        new = old.with_name(f"Better-title--{SESSION_ID[:12]}.log")
        old.rename(new)
        self.assertEqual(bot.session_log_path(self.cfg, "Work notes"), new)
        bot.remember_session_reference(self.cfg, "Work notes", new)
        self.assertEqual(bot.session_log_path(self.cfg, "Work_notes.log"), new)
        self.assertEqual(bot.session_log_path(self.cfg, new.stem), new)
        self.assertFalse(old.exists())

    def test_managed_filename_recovers_without_prior_mailbot_index(self):
        current = self.session(f"Renamed--{SESSION_ID[:12]}")
        self.assertEqual(bot.session_log_path(self.cfg, f"Old--{SESSION_ID[:12]}"), current)

    def test_missing_alias_fails_instead_of_forking_empty_session(self):
        old = self.session()
        bot.remember_session_reference(self.cfg, "old topic", old)
        old.unlink()
        with self.assertRaises(FileNotFoundError):
            bot.session_log_path(self.cfg, "old topic")
        self.assertFalse((self.cfg.session_dir / "old_topic.log").exists())

    def test_backend_reported_final_path_updates_named_session(self):
        old = self.session("old")
        new = old.with_name(f"Current--{SESSION_ID[:12]}.log")

        def run(cfg, cmd, body, timeout):
            old.rename(new)
            return 0, "continued", f"session: {old}\nsession: {new}"

        with mock.patch.object(bot, "run_backend_process", side_effect=run):
            rc, output, errors = bot.run_ttychatter(self.cfg, "old", "continue", [])
        self.assertEqual(rc, 0)
        self.assertEqual(bot.session_log_path(self.cfg, "old"), new)
        self.assertEqual(bot.display_session_name("old", errors), new.stem)

    def test_backend_marker_cannot_point_outside_sender_root(self):
        outside = self.root / "private.log"
        outside.write_text("private data")
        with mock.patch.object(bot, "run_backend_process", return_value=(0, "answer", f"session: {outside}")):
            rc, output, errors = bot.run_ttychatter(self.cfg, "topic", "hello", [])
        self.assertNotEqual(rc, 0)
        self.assertIn("session tracking failed", errors)
        self.assertNotIn("private data", errors)

    def test_symlink_sessions_are_not_read_or_modified(self):
        outside = self.root / "private.log"
        outside.write_text("secret original")
        link = self.cfg.session_dir / "alias.log"
        link.symlink_to(outside)
        self.assertEqual(bot.session_files_newest_first(self.cfg), [])
        for command in ("search-session alias secret", "rename-session alias New", "delete-session alias", "turns alias"):
            ok, output, _ = bot.dispatch_mailbot_command(self.cfg, command)
            self.assertFalse(ok, command)
        self.assertEqual(outside.read_text(), "secret original")

    def test_rename_command_tracks_actual_backend_file(self):
        old = self.session()
        new = old.with_name(f"Release-notes--{SESSION_ID[:12]}.log")

        def run(cfg, args, **kwargs):
            self.assertEqual(args, ["--rename-session", str(old), "Release notes"])
            old.rename(new)
            bot.append_session_title_metadata(new, "Release notes")
            return 0, f"renamed\nsession: {new}", ""

        with mock.patch.object(bot, "run_ttychatter_cli", side_effect=run):
            ok, text, attachments = bot.dispatch_mailbot_command(self.cfg, 'rename-session old "Release notes"')
        self.assertTrue(ok, text)
        self.assertIn(new.name, text)
        self.assertEqual(bot.session_log_path(self.cfg, "old"), new)
        bot.delete_session_file(self.cfg, "old")
        self.assertFalse(new.exists())
        self.assertEqual(bot.load_session_aliases(self.cfg), {})

    def test_legacy_rename_never_overwrites_existing_log(self):
        old = self.session()
        other = self.session("occupied", identity="f" * 32)
        with self.assertRaises(FileExistsError):
            bot.rename_session_file_legacy(self.cfg, "old", "occupied")
        self.assertTrue(old.exists())
        self.assertEqual(bot.session_id_from_file(other), "f" * 32)

    def test_reply_to_new_uses_created_session_and_prompts_for_name(self):
        path = self.session("session-2026-10-18-07-00-00")
        reply = bot.build_reply(self.cfg, self.email(), path.stem, "answer", "", True)
        self.assertEqual(reply["Subject"], f"Re: tc:{{{path.stem}}}")
        self.assertIn("Please give this session a name", reply.get_content())
        self.assertIn(f"rename-session {path.stem}", reply.get_content())
        request = bot.parse_subject_request(reply["Subject"], self.cfg)
        self.assertEqual(request.value, path.stem)

    def test_trim_preserves_identity_title_and_idle_timestamp(self):
        path = self.session("public-test", "A named session")
        with path.open("a") as fh:
            for index in range(5):
                fh.write(f"[11:00:00] user: question {index}\n[11:00:01] assistant: answer {index}\n")
            fh.write('[11:00:02] system: openrouter-response: {"usage":{"total_tokens":20}}\n')
        self.cfg.public_session_max_turns = 2
        old_ns = 1234567890000000000
        os.utime(path, ns=(old_ns, old_ns))
        bot.trim_public_session_log(self.cfg, path)
        text = path.read_text()
        self.assertEqual(bot.session_id_from_file(path), SESSION_ID)
        self.assertEqual(bot.session_title_from_file(path), "A named session")
        self.assertIn("answer 4", text)
        self.assertNotIn("question 0", text)
        self.assertEqual(path.stat().st_mtime_ns, old_ns)
        head, tail, _ = bot.read_session_head_tail(path, 3, 3, 100)
        self.assertNotIn("openrouter-response", "\n".join(head + tail))

    def test_registry_environment_cannot_inherit_operator_key(self):
        self.cfg.runtime_ttychatter_home = self.root / "user-home"
        self.cfg.runtime_user_id = "private-user"
        self.cfg.runtime_api_key_source = "missing user API key; operator fallback disabled"
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "operator-secret", "TTYCHATTER_API_KEY": "operator-secret", "API_KEY": "operator-secret", "XDG_STATE_HOME": "operator-state"}):
            env = bot.ttychatter_environment(self.cfg)
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("TTYCHATTER_API_KEY", env)
        self.assertNotIn("API_KEY", env)
        self.assertEqual(env["XDG_STATE_HOME"], str(self.cfg.runtime_ttychatter_home / ".local" / "state"))

    def test_trim_retains_distinct_old_names_and_hides_inline_aliases(self):
        path = self.session("public-renamed", "Current title")
        with path.open("a") as stream:
            stream.write("[10:00:03] system: session-alias: old-filename\n")
            stream.write("[10:00:04] system: session-alias: Earlier human title\n")
            for index in range(5):
                stream.write(f"[11:00:00] user: question {index}\n[11:00:01] assistant: answer {index}\n")
            stream.write("[11:00:02] system: session-alias: old-filename\n")
            stream.write("[11:00:03] system: session-alias: old-filename\n")
        self.cfg.public_session_max_turns = 2
        bot.trim_public_session_log(self.cfg, path)
        text = path.read_text()
        self.assertEqual(text.count("system: session-alias: old-filename\n"), 1)
        self.assertEqual(text.count("system: session-alias: Earlier human title\n"), 1)
        self.assertEqual(bot.session_id_from_file(path), SESSION_ID)
        self.assertEqual(bot.session_title_from_file(path), "Current title")
        head, tail, _ = bot.read_session_head_tail(path, 100, 100, 200)
        self.assertNotIn("session-alias:", "\n".join(head + tail))
        self.assertIn("no matches for: session-alias:", bot.search_session_logs(self.cfg, "session-alias:"))

    def test_chat_timeout_retains_partial_output(self):
        code = "import time; print('partial answer', flush=True); time.sleep(10)"
        started = time.monotonic()
        rc, output, errors = bot.run_backend_process(self.cfg, [sys.executable, "-c", code], "", 0.15)
        self.assertEqual(rc, 124)
        self.assertEqual(output, "partial answer")
        self.assertIn("timed out", errors)
        self.assertLess(time.monotonic() - started, 3)

    def test_backend_output_is_bounded_while_both_pipes_are_drained(self):
        self.cfg.max_command_output_bytes = 1000
        self.cfg.max_outgoing_attachment_bytes = 2000
        code = (
            "import sys\n"
            "for _ in range(100):\n"
            " sys.stdout.buffer.write(b'x' * 4096); sys.stdout.buffer.flush()\n"
            " sys.stderr.buffer.write(b'y' * 4096); sys.stderr.buffer.flush()\n"
            "sys.stderr.write('\\nsession: last-session.log\\n')\n"
        )
        rc, output, errors = bot.run_backend_process(self.cfg, [sys.executable, "-c", code], "", 3)
        self.assertEqual(rc, 0)
        self.assertTrue(output.startswith("x" * 2000))
        self.assertLess(len(output), 2200)
        self.assertIn("truncated", output)
        self.assertLess(len(errors), 1200)
        self.assertIn("truncated", errors)
        self.assertTrue(errors.endswith("session: last-session.log"))

    def test_backend_large_input_and_output_do_not_deadlock(self):
        payload = "A" * 300000
        code = "import sys; sys.stdout.write('x'*300000); sys.stdout.flush(); data=sys.stdin.read(); sys.stderr.write(str(len(data)))"
        rc, output, errors = bot.run_backend_process(self.cfg, [sys.executable, "-c", code], payload, 3)
        self.assertEqual(rc, 0)
        self.assertEqual(output, "x" * 300000)
        self.assertEqual(errors, str(len(payload)))

    def test_backend_exit_before_consuming_stdin_preserves_result(self):
        code = "import sys; print('refused request'); sys.exit(7)"
        rc, output, errors = bot.run_backend_process(self.cfg, [sys.executable, "-c", code], "A" * 300000, 3)
        self.assertEqual((rc, output, errors), (7, "refused request", ""))

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX process groups")
    def test_backend_timeout_does_not_wait_forever_for_detached_pipe_owner(self):
        code = (
            "import os, time\n"
            "pid = os.fork()\n"
            "if pid == 0:\n"
            " os.setsid()\n"
            " time.sleep(3)\n"
            " os._exit(0)\n"
            "print(pid, flush=True)\n"
        )
        started = time.monotonic()
        output = ""
        try:
            with mock.patch.object(bot, "BACKEND_TERMINATE_GRACE", 0.05), mock.patch.object(bot, "BACKEND_KILL_GRACE", 0.05):
                rc, output, errors = bot.run_backend_process(self.cfg, [sys.executable, "-c", code], "", 0.15)
            self.assertEqual(rc, 124)
            self.assertTrue(output.isdigit(), output)
            self.assertIn("timed out", errors)
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            if output.strip().isdigit():
                try:
                    os.kill(int(output), bot.signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_backend_timeout_still_applies_after_child_closes_pipes(self):
        code = "import os,time; os.close(0); os.close(1); os.close(2); time.sleep(10)"
        started = time.monotonic()
        rc, output, errors = bot.run_backend_process(self.cfg, [sys.executable, "-c", code], "", 0.15)
        self.assertEqual((rc, output), (124, ""))
        self.assertIn("timed out", errors)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_rejected_messages_do_not_starve_later_unread_mail(self):
        self.cfg.max_messages_per_run = 1
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [b"1 2 3"])
        with mock.patch.object(bot, "imap_connect", return_value=conn), mock.patch.object(bot, "process_message", side_effect=[False, False, True]) as process, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bot.run_once(self.cfg), 0)
        self.assertEqual([call.args[2] for call in process.call_args_list], [b"1", b"2", b"3"])

    def test_poll_lock_prevents_overlapping_generation(self):
        self.cfg.processed_db.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.cfg.processed_db.with_name(self.cfg.processed_db.name + ".lock")
        with lock_path.open("a") as lock:
            bot.fcntl.flock(lock, bot.fcntl.LOCK_EX | bot.fcntl.LOCK_NB)
            with mock.patch.object(bot, "imap_connect") as connect, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bot.run_once(self.cfg), 0)
            connect.assert_not_called()

    def test_delivery_failure_retries_saved_email_without_regeneration(self):
        original = self.email("Build notes")
        self.cfg.allowed_from = ["reader@example.test"]
        conn = mock.Mock()
        result = (0, "saved answer", f"session: {self.cfg.session_dir / 'Build_notes.log'}")
        with mock.patch.object(bot, "fetch_message_peek", return_value=original), mock.patch.object(bot, "runtime_config_for_sender", return_value=self.cfg), mock.patch.object(bot, "rate_limit_check", return_value=None), mock.patch.object(bot, "daily_quota_check", return_value=None), mock.patch.object(bot, "run_ttychatter", return_value=result) as generate, mock.patch.object(bot, "send_reply", side_effect=[RuntimeError("mail server offline"), None]) as send, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(bot.process_message(self.cfg, conn, b"1"))
            pending = bot.pending_chat_reply_path(self.cfg, original["Message-ID"], original["From"])
            self.assertTrue(pending.is_file())
            self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
            self.assertTrue(bot.process_message(self.cfg, conn, b"1"))
        generate.assert_called_once()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[1]["Message-ID"], send.call_args_list[1].args[1]["Message-ID"])
        self.assertFalse(pending.exists())
        self.assertIn(original["Message-ID"], bot.load_processed(self.cfg.processed_db))

    def test_corrupt_pending_reply_is_preserved_without_model_retry(self):
        original = self.email("Build notes")
        self.cfg.allowed_from = ["reader@example.test"]
        path = bot.pending_chat_reply_path(self.cfg, original["Message-ID"], original["From"])
        path.parent.mkdir()
        path.write_text("{broken")
        with mock.patch.object(bot, "fetch_message_peek", return_value=original), mock.patch.object(bot, "run_ttychatter") as generate, mock.patch.object(bot, "send_reply") as send, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(bot.process_message(self.cfg, mock.Mock(), b"1"))
        generate.assert_not_called()
        send.assert_not_called()
        self.assertEqual(path.read_text(), "{broken")

    def test_command_delivery_retry_does_not_repeat_operation(self):
        original = self.email("tc:MAILBOT")
        original.set_content("branch old 1")
        self.cfg.allowed_from = ["reader@example.test"]
        conn = mock.Mock()
        with mock.patch.object(bot, "fetch_message_peek", return_value=original), mock.patch.object(bot, "runtime_config_for_sender", return_value=self.cfg), mock.patch.object(bot, "rate_limit_check", return_value=None), mock.patch.object(bot, "daily_quota_check", return_value=None), mock.patch.object(bot, "dispatch_mailbot_command", return_value=(True, "created branch once", [])) as dispatch, mock.patch.object(bot, "send_reply", side_effect=[RuntimeError("mail server offline"), None]) as send, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(bot.process_message(self.cfg, conn, b"1"))
            self.assertTrue(bot.process_message(self.cfg, conn, b"1"))
        dispatch.assert_called_once()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[1]["Message-ID"], send.call_args_list[1].args[1]["Message-ID"])
        self.assertIn("created branch once", send.call_args_list[1].args[1].get_content())

    def test_config_delivery_retry_does_not_reapply_attachment(self):
        original = self.email("tc:CONFIG")
        original.add_attachment(b"POLL_INTERVAL=120\n", maintype="text", subtype="plain", filename="config.txt")
        self.cfg.allowed_from = ["reader@example.test"]
        with mock.patch.object(bot, "fetch_message_peek", return_value=original), mock.patch.object(bot, "rate_limit_check", return_value=None), mock.patch.object(bot, "daily_quota_check", return_value=None), mock.patch.object(bot, "apply_config_attachment", return_value=self.root / "backup") as apply, mock.patch.object(bot, "send_reply", side_effect=[RuntimeError("mail server offline"), None]), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(bot.process_message(self.cfg, mock.Mock(), b"1"))
            self.assertTrue(bot.process_message(self.cfg, mock.Mock(), b"1"))
        apply.assert_called_once()

    def test_unknown_charset_uses_readable_fallback(self):
        original = bot.email.message_from_bytes(
            b'Content-Type: text/plain; charset="x-nonexistent-charset"\r\n'
            b'Content-Transfer-Encoding: base64\r\n\r\nSGVsbG8g8J+MjQo=\r\n',
            policy=bot.email.policy.default,
        )
        self.assertEqual(bot.extract_body(original, 1000), "Hello \U0001f30d")

    def test_fetch_skips_unsolicited_flags_before_message_literal(self):
        original = self.email()
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [b"7 (FLAGS (\\Seen))", (b"8 (UID 123 BODY[] {12}", original.as_bytes()), b")"])
        fetched = bot.fetch_message_peek(conn, b"123")
        self.assertEqual(fetched["Message-ID"], original["Message-ID"])
        self.assertEqual(fetched.get_content().strip(), "hello")
        conn.uid.assert_called_once_with("FETCH", b"123", "(BODY.PEEK[])")

    def test_imap_fetch_limits_bytes_before_parsing_oversized_message(self):
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [(b"1 (UID 9 BODY[]<0> {1001}", b"x" * 1001), b")"])
        with mock.patch.object(bot.email, "message_from_bytes") as parse, contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(bot.fetch_message_peek(conn, b"9", max_bytes=1000))
        conn.uid.assert_called_once_with("FETCH", b"9", "(BODY.PEEK[]<0.1001>)")
        parse.assert_not_called()

    def test_fetched_message_reuses_exact_wire_size_without_serializing(self):
        original = self.email()
        raw = original.as_bytes()
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [(b"1 (UID 9 BODY[]<0> {1}", raw), b")"])
        fetched = bot.fetch_message_peek(conn, b"9", max_bytes=len(raw))
        with mock.patch.object(fetched, "as_bytes", side_effect=AssertionError("unnecessary serialization")):
            self.assertEqual(bot.message_size_bytes(fetched), len(raw))

    def test_process_fetch_limit_preserves_configured_admin_bypass(self):
        for limit_admin, expected in ((True, self.cfg.max_message_bytes), (False, 0)):
            with self.subTest(limit_admin=limit_admin):
                self.cfg.abuse_limit_allowed_from = limit_admin
                with mock.patch.object(bot, "fetch_message_peek", return_value=None) as fetch:
                    self.assertFalse(bot.process_message(self.cfg, mock.Mock(), b"9"))
                self.assertEqual(fetch.call_args.kwargs["max_bytes"], expected)

    def test_bad_message_does_not_abort_later_unread_work(self):
        self.cfg.max_messages_per_run = 1
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [b"1 2"])
        with mock.patch.object(bot, "imap_connect", return_value=conn), mock.patch.object(bot, "process_message", side_effect=[ValueError("invalid message header"), True]) as process, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bot.run_once(self.cfg), 0)
        self.assertEqual([call.args[2] for call in process.call_args_list], [b"1", b"2"])
        conn.logout.assert_called_once()

    def test_disconnected_imap_aborts_poll_instead_of_reusing_connection(self):
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [b"1 2"])
        with mock.patch.object(bot, "imap_connect", return_value=conn), mock.patch.object(bot, "process_message", side_effect=bot.imaplib.IMAP4.abort("lost connection")) as process:
            with self.assertRaises(bot.imaplib.IMAP4.abort):
                bot.run_once(self.cfg)
        process.assert_called_once()
        conn.logout.assert_called_once()

    def test_poll_reads_processed_history_once_and_tracks_new_duplicates(self):
        self.cfg.allowed_from = ["reader@example.test"]
        self.cfg.max_messages_per_run = 10
        self.cfg.mark_seen = False
        first = self.email("Build notes")
        second = self.email("Other notes")
        second.replace_header("Message-ID", "<second@example.test>")
        conn = mock.Mock()
        conn.uid.return_value = ("OK", [b"1 2 3"])
        with mock.patch.object(bot, "imap_connect", return_value=conn), mock.patch.object(bot, "fetch_message_peek", side_effect=[first, first, second]), mock.patch.object(bot, "runtime_config_for_sender", side_effect=lambda cfg, sender: cfg), mock.patch.object(bot, "rate_limit_check", return_value=None), mock.patch.object(bot, "daily_quota_check", return_value=None), mock.patch.object(bot, "run_ttychatter", return_value=(0, "answer", "")) as generate, mock.patch.object(bot, "send_reply") as send, mock.patch.object(bot, "load_processed", wraps=bot.load_processed) as load, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bot.run_once(self.cfg), 0)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(send.call_count, 2)
        load.assert_called_once_with(self.cfg.processed_db)
        self.assertEqual(bot.load_processed(self.cfg.processed_db), {first["Message-ID"], second["Message-ID"]})

    def test_public_ownership_follows_renamed_filename(self):
        self.cfg.open_to_public = True
        old = self.session("public-one")
        owner = "reader@example.test"
        bot.record_public_session(self.cfg, old.stem, owner)
        bot.remember_session_reference(self.cfg, old.stem, old)
        new = old.with_name(f"Readable--{SESSION_ID[:12]}.log")
        old.rename(new)
        bot.remember_session_reference(self.cfg, old.stem, new)
        self.assertTrue(bot.public_session_owner_allows(self.cfg, old.stem, owner))
        self.assertFalse(bot.public_session_owner_allows(self.cfg, old.stem, "another@example.test"))
        self.assertEqual(bot.public_resume_session_allowed(self.cfg, new.stem), new.stem)
        self.assertIn(new, bot.public_cleanup_session_candidates(self.cfg))
        bot.delete_session_file(self.cfg, old.stem)
        self.assertNotIn(new.stem, bot.load_public_session_index(self.cfg)["sessions"])

    def test_imap_tls_verification_and_timeout_are_explicit(self):
        self.cfg.imap_user = "mailbox"
        self.cfg.imap_password = "test-only-password"
        conn = mock.Mock()
        conn.select.return_value = ("OK", [b"0"])
        with mock.patch.object(bot.imaplib, "IMAP4_SSL", return_value=conn) as connect:
            self.assertIs(bot.imap_connect(self.cfg), conn)
        context = connect.call_args.kwargs["ssl_context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, bot.ssl.CERT_REQUIRED)
        self.assertEqual(connect.call_args.kwargs["timeout"], self.cfg.ttychatter_control_timeout)

    def test_lost_smtp_quit_response_does_not_retry_accepted_email(self):
        smtp = mock.Mock()
        smtp.quit.side_effect = bot.smtplib.SMTPServerDisconnected("lost QUIT response")
        reply = self.email()
        with mock.patch.object(bot, "load_password", return_value=""), mock.patch.object(bot.smtplib, "SMTP", return_value=smtp):
            bot.send_reply_smtp(self.cfg, reply)
        smtp.send_message.assert_called_once_with(reply)
        smtp.close.assert_called_once()

    def test_smtp_quit_failure_does_not_hide_original_send_error(self):
        smtp = mock.Mock()
        smtp.send_message.side_effect = bot.smtplib.SMTPDataError(554, b"message refused")
        smtp.quit.side_effect = bot.smtplib.SMTPServerDisconnected("connection closed")
        with mock.patch.object(bot, "load_password", return_value=""), mock.patch.object(bot.smtplib, "SMTP", return_value=smtp):
            with self.assertRaises(bot.smtplib.SMTPDataError) as failure:
                bot.send_reply_smtp(self.cfg, self.email())
        self.assertEqual(failure.exception.smtp_code, 554)


if __name__ == "__main__":
    unittest.main()
