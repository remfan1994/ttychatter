#!/usr/bin/env python3
"""Fault and load regressions for the real ncurses request/UI boundary."""
import curses
import json
import random
import threading
import time
import types
import unittest
from unittest.mock import patch

import test_ncurses as common

m = common.m


class StabilityTests(unittest.TestCase):
    setUp = common.RegressionTests.setUp
    tearDown = common.RegressionTests.tearDown

    def test_worker_exception_finishes_request_and_restores_draft(self):
        self.app.input_lines = ['keep this question']
        with patch.object(m, 'generate_with_model', side_effect=RuntimeError('transport broke')):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion(0.5))
        self.assertEqual(self.app.current_input(), 'keep this question')
        self.assertIn('transport broke', self.session.read_text())

    def test_failure_log_error_keeps_ui_and_draft_alive(self):
        self.app.input_lines = ['retry this question']
        append = m.append_log
        def fail_system(path, speaker, text, stamp):
            if speaker == 'system':
                raise OSError('disk full')
            return append(path, speaker, text, stamp)
        with patch.object(m, 'generate_with_model', return_value=(False, 'provider unavailable')), \
             patch.object(m, 'append_log', side_effect=fail_system):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertEqual(self.app.current_input(), 'retry this question')
        self.assertFalse(self.app._request_active)
        self.assertIn('save failed', self.app.status)

    def test_failed_prompt_stays_out_of_context_after_reopening(self):
        self.app.context_buffer = ['User: old', 'Assistant: answer']
        m.append_context_snapshot(self.session, self.app.context_buffer)
        self.app.input_lines = ['failed request']
        with patch.object(m, 'generate_with_model', return_value=(False, 'unavailable')):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        reopened = m.AICursesApp(self.cfg, self.session.stem, self.session)
        reopened.load_session_into_ui()
        self.assertEqual(reopened.context_buffer, ['User: old', 'Assistant: answer'])

    def test_escape_cancels_active_stream_without_opening_menu(self):
        self.app._request_active = True
        self.app._request_state = {'streaming': True}
        with patch.object(self.app, 'dispatch_command_action') as menu:
            self.app.handle_key(27)
        menu.assert_not_called()
        self.assertTrue(self.app._request_cancel.is_set())

    def test_stream_events_preserve_read_position_and_next_draft(self):
        item = m.TranscriptItem('AI', '\n'.join('row ' + str(i) for i in range(100)))
        self.app.transcript = [item]
        self.app._request_active = True
        self.app._streaming = True
        self.app._request_state = {'stream_item': item, 'response_started': True, 'streaming': True}
        self.app.input_visible = True
        self.app.input_lines = ['draft for next request']
        self.app.transcript_scroll = 20
        self.app.draw()
        before = next(text for y, x, text, attr in self.app.stdscr.writes if y == 1)
        self.app._request_events.put(('piece', '\nnew line\nanother line'))
        self.app.process_request_events()
        self.app.draw()
        self.assertTrue(self.app.input_visible)
        self.assertEqual(self.app.current_input(), 'draft for next request')
        self.assertEqual(next(text for y, x, text, attr in self.app.stdscr.writes if y == 1), before)

    def test_fast_producer_cannot_fill_an_unbounded_ui_queue(self):
        self.cfg.stream = True
        self.app.input_lines = ['stream please']
        started = threading.Event()
        def flood(cfg, model, prompt, name, folder, files, on_piece, should_cancel, **kw):
            started.set()
            for _ in range(12000):
                if should_cancel():
                    return True, m.STREAM_INTERRUPT_MARKER, True
                on_piece('x' * 200)
            return True, 'complete', False
        try:
            with patch.object(m, 'generate_with_model_stream', side_effect=flood):
                self.app.send_current_message()
                self.assertTrue(started.wait(1))
                time.sleep(0.08)
                self.assertLessEqual(self.app._request_events.qsize(), 64)
                self.app._request_cancel.set()
                self.assertTrue(self.app.wait_for_request_completion(2))
        finally:
            self.app._request_cancel.set()
            self.app.wait_for_request_completion(2)

    def test_done_closes_transport_even_when_server_keeps_connection_open(self):
        self.cfg.api_key = 'fake-test-key'
        released = threading.Event()
        closed = threading.Event()
        readers = []
        class Socket:
            def shutdown(self, how):
                released.set()
        class KeptOpen(common.Response):
            def __init__(self):
                super().__init__(common.sse({'choices': [{'delta': {'content': 'answer'}}]}, '[DONE]'))
                self.fp = types.SimpleNamespace(raw=types.SimpleNamespace(_sock=Socket()))
            def readline(self, *args):
                readers.append(threading.current_thread())
                data = super().readline(*args)
                if not data:
                    released.wait(1)
                return data
            def close(self):
                closed.set()
                super().close()
        try:
            with patch.object(m.urllib.request, 'urlopen', return_value=KeptOpen()):
                ok, text, stopped = m.generate_with_model_stream(self.cfg, 'model', 'prompt', 'name', self.root, [], lambda p: None, lambda: False)
            self.assertTrue(ok)
            self.assertEqual(text, 'answer')
            self.assertTrue(closed.wait(0.3), 'completed stream left its reader and connection alive')
        finally:
            released.set()
            for reader in set(readers):
                reader.join(1)

    def test_buffered_response_limit_is_checked_before_parsing(self):
        self.cfg.api_key = 'fake-test-key'
        body = json.dumps({'choices': [{'message': {'content': 'x' * 400}}]}).encode()
        with patch.object(m, 'MAX_RESPONSE_BYTES', 128, create=True), \
             patch.object(m.urllib.request, 'urlopen', return_value=common.Response(body)):
            ok, text = m.generate_with_model(self.cfg, 'model', 'prompt', 'name', self.root, [])
        self.assertFalse(ok)
        self.assertIn('limit', text)

    def test_session_navigation_reuses_unchanged_log_metadata(self):
        m.append_log(self.session, 'system', 'session-title: Original', '12:00:00')
        with patch.object(m, 'session_title_from_file', wraps=m.session_title_from_file) as title:
            for _ in range(6):
                self.assertEqual(self.app.session_records()[0][2], 'Original')
            self.assertEqual(title.call_count, 1)
            m.append_log(self.session, 'system', 'session-title: Changed', '12:00:01')
            self.assertEqual(self.app.session_records()[0][2], 'Changed')
            self.assertEqual(title.call_count, 2)

    def test_full_text_search_cache_refreshes_when_log_changes(self):
        with patch.object(self.app, 'session_file_contains', wraps=self.app.session_file_contains) as search:
            self.assertEqual(self.app.session_records(content_query='new phrase'), [])
            self.assertEqual(self.app.session_records(content_query='new phrase'), [])
            self.assertEqual(search.call_count, 1)
            m.append_log(self.session, 'user', 'new phrase', '12:00:00')
            self.assertEqual(len(self.app.session_records(content_query='new phrase')), 1)
            self.assertEqual(search.call_count, 2)

    def test_context_replay_is_bounded_and_reloading_does_not_duplicate_rows(self):
        for index in range(200):
            m.append_log(self.session, 'user', 'question ' + str(index), '12:00:00')
            m.append_log(self.session, 'AI', 'answer ' + str(index), '12:00:01')
        self.cfg.context_turns = 3
        self.app.load_session_into_ui()
        self.assertEqual(len(self.app.context_buffer), 6)
        self.assertEqual(self.app.context_buffer[0], 'User: question 197')
        self.assertEqual(len(self.app.transcript), 80)
        self.assertEqual(self.app.last_reply, 'answer 199')
        self.app.load_session_into_ui()
        self.assertEqual(len(self.app.transcript), 80)
        m.append_context_snapshot(self.session, ['User: edited memory'])
        m.append_log(self.session, 'AI', 'after edit', '12:00:02')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer, ['User: edited memory', 'Assistant: after edit'])

    def test_wrap_preserves_words_tabs_and_display_widths(self):
        def old_wrap(text, width):
            result = []
            for paragraph in text.splitlines() or ['']:
                remaining = ''.join(' ' if ord(c) < 32 or ord(c) == 127 else c for c in paragraph.expandtabs(4))
                if not remaining:
                    result.append('')
                while remaining:
                    chunk = m.clip_terminal_text(remaining, max(1, width)) or remaining[:1]
                    if len(chunk) < len(remaining):
                        split = chunk.rfind(' ')
                        if split > 0 and chunk[:split].strip():
                            chunk = chunk[:split + 1]
                    result.append(chunk)
                    remaining = remaining[len(chunk):]
            return result or ['']
        rng = random.Random(17)
        for width in (1, 2, 7, 40, 80):
            for _ in range(40):
                text = ''.join(rng.choice('abc  \t\n\x01\u754c\u0301') for _ in range(180))
                self.assertEqual(m.wrap_for_width(text, width), old_wrap(text, width))

    def test_model_catalog_invalid_shapes_do_not_crash(self):
        for obj in ([], {'data': 3}):
            with patch.object(m.urllib.request, 'urlopen', return_value=common.Response(json.dumps(obj).encode())):
                models, error = m.fetch_models(self.cfg)
            self.assertEqual(models, [])
            self.assertTrue(error)
        obj = {'data': [None, 3, {'id': 'valid/model', 'top_provider': 7, 'supported_parameters': [{}, [], 'tools']}]}
        with patch.object(m.urllib.request, 'urlopen', return_value=common.Response(json.dumps(obj).encode())):
            models, error = m.fetch_models(self.cfg)
        self.assertIsNone(error)
        self.assertEqual([model.model_id for model in models], ['valid/model'])

    def test_auto_title_save_error_keeps_received_answer_and_ui_alive(self):
        self.cfg.session_auto_title = True
        self.app.input_lines = ['question']
        with patch.object(m, 'generate_with_model', return_value=(True, 'answer')), \
             patch.object(m, 'apply_session_title', side_effect=PermissionError('rename refused')):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertEqual(self.app.last_reply, 'answer')
        self.assertIn('AI: answer', self.session.read_text())
        self.assertIn('title could not be saved', self.app.status)

    def test_optional_title_wait_does_not_delay_answer_saving(self):
        self.cfg.session_auto_title = True
        self.app.input_lines = ['original question']
        started, release = threading.Event(), threading.Event()
        title_thread = []
        def slow_title(cfg, name, question, answer):
            title_thread.append(threading.current_thread())
            self.assertEqual(question, 'original question')
            self.assertEqual(answer, 'received answer')
            started.set()
            release.wait(2)
            return 'Useful title'
        try:
            with patch.object(m, 'generate_with_model', return_value=(True, 'received answer')), \
                 patch.object(m, 'suggest_session_title', side_effect=slow_title):
                self.app.send_current_message()
                self.assertTrue(started.wait(1))
                self.app.process_request_events()
                self.assertTrue(self.app._request_active)
                self.assertIn('AI: received answer', self.session.read_text())
                self.assertEqual(self.app.last_reply, 'received answer')
                self.app.handle_key(curses.KEY_F11)
                self.assertTrue(self.app.input_visible)
                release.set()
                self.assertTrue(self.app.wait_for_request_completion())
            self.assertIsNot(title_thread[0], threading.current_thread())
            self.assertEqual(self.app.session_title, 'Useful title')
            self.assertTrue(self.app.session_path.is_file())
            self.assertTrue(self.app.input_visible)
        finally:
            release.set()

    def test_provider_lone_surrogate_is_safe_to_save_and_export(self):
        self.cfg.api_key = 'fake-test-key'
        self.app.input_lines = ['question']
        body = b'{"choices":[{"message":{"content":"answer \\ud800 caf\\u00e9"}}]}'
        with patch.object(m.urllib.request, 'urlopen', return_value=common.Response(body)):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertEqual(self.app.last_reply, 'answer ? caf\u00e9')
        self.assertIn('answer ? caf\u00e9', self.session.read_text())
        pieces = m.parse_openrouter_stream_piece('{"choices":[{"delta":{"content":"\\udfff"}}]}')
        self.assertEqual(pieces, ['?'])

    def test_failed_model_cache_save_preserves_previous_cache(self):
        self.cfg.model_cache_file = self.root / 'models.json'
        self.cfg.model_cache_file.write_text('previous cache')
        with patch.object(m.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                m.save_model_cache(self.cfg, [])
        self.assertEqual(self.cfg.model_cache_file.read_text(), 'previous cache')

    def test_idle_ui_draws_once_until_something_changes(self):
        app = self.app
        class IdleScreen(common.Screen):
            reads = 0
            def timeout(self, value): pass
            def bkgd(self, *args): pass
            def getch(self):
                self.reads += 1
                if self.reads >= 30:
                    app.should_quit = True
                return -1
        with patch.object(m.curses, 'noecho'), patch.object(m.curses, 'cbreak'), \
             patch.object(m.curses, 'start_color'), patch.object(m.curses, 'use_default_colors'), \
             patch.object(m.curses, 'mousemask'), patch.object(m.curses, 'set_escdelay'), \
             patch.object(app, 'draw') as draw:
            app.run(IdleScreen())
        self.assertEqual(draw.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
