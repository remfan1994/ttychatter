#!/usr/bin/env python3
"""Offline regressions for the ncurses UI, sessions, and OpenRouter transport."""
import curses
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'nonc' / 'ttychatter.ncurses'
loader = importlib.machinery.SourceFileLoader('ttychatter_ncurses_test_module', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = m
loader.exec_module(m)


class Screen:
    def __init__(self, height=24, width=80, keys=()):
        self.height, self.width = height, width
        self.keys = list(keys)
        self.writes, self.cursor = [], (0, 0)
    def getmaxyx(self): return self.height, self.width
    def erase(self): self.writes = []
    def addstr(self, y, x, text, attr=0):
        self.writes.append((y, x, text, attr))
        self.cursor = (y, min(self.width-1, x+len(text)))
    def move(self, y, x):
        assert 0 <= y < self.height and 0 <= x < self.width, (y, x)
        self.cursor = (y, x)
    def refresh(self): pass
    def getch(self):
        if not self.keys: raise AssertionError('Unexpected additional key read')
        return self.keys.pop(0)
    def nodelay(self, value): pass
    def keypad(self, value): pass


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def sse(*packets):
    return ''.join('data: ' + (p if isinstance(p, str) else json.dumps(p)) + '\n\n' for p in packets).encode()


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = m.Config(session_dir=self.root/'sessions', attachment_dir=self.root/'files',
                            config_file=self.root/'config', prompt_session_name=False,
                            confirm_live_send=False, startup_notice=False)
        self.cfg.session_dir.mkdir()
        self.session = self.cfg.session_dir/'session-2026-01-01-00-00-00.log'
        self.cfg.request_session_id = m.ensure_session_id(self.session)
        self.app = m.AICursesApp(self.cfg, self.session.stem, self.session)
        self.app.stdscr = Screen()
        self.cursor_patch = patch.object(m.curses, 'curs_set', lambda n: None)
        self.cursor_patch.start()
    def tearDown(self):
        self.cursor_patch.stop()
        self.temp.cleanup()

    def test_cursor_drawn_after_footer_at_narrow_and_wide_sizes(self):
        self.app.input_lines = ['0123456789']
        self.app.cursor_col = 4
        for height, width in [(24,80), (12,40), (8,30), (5,20)]:
            self.app.stdscr = Screen(height, width)
            self.app.draw()
            self.assertEqual(self.app.stdscr.cursor, (self.app.layout['input_y'], 6))
            self.assertTrue(any('L1 C5' in text for _,_,text,_ in self.app.stdscr.writes))
            self.assertFalse(any('F7 Files' in text for _,_,text,_ in self.app.stdscr.writes))

    def test_hidden_draft_returns_rows_and_arrows_scroll(self):
        self.app.input_lines = ['draft to retain']
        self.app.cursor_col = 5
        self.app.draw()
        visible_height = self.app.layout['transcript_h']
        self.app.handle_key(curses.KEY_F11)
        self.app.draw()
        self.assertGreater(self.app.layout['transcript_h'], visible_height)
        self.app.handle_key(curses.KEY_UP)
        self.assertEqual(self.app.transcript_scroll, 1)
        self.app.handle_key(ord('x'))
        self.assertEqual(self.app.current_input(), 'draft to retain')
        self.app.handle_key(curses.KEY_F11)
        self.assertEqual(self.app.cursor_col, 5)

    def test_page_keys_use_visible_page(self):
        self.app.draw()
        self.app.handle_key(curses.KEY_PPAGE)
        self.assertEqual(self.app.transcript_scroll, self.app.layout['transcript_h']-1)
        self.app.handle_key(curses.KEY_NPAGE)
        self.assertEqual(self.app.transcript_scroll, 0)

    def test_editor_key_opens_options_without_running_process(self):
        self.app.stdscr.keys = [27]
        with patch.object(self.app, 'run_external_editor') as editor:
            self.app.handle_key(curses.KEY_F8)
            editor.assert_not_called()
        self.assertTrue(any('EXTERNAL EDITOR' in row[2] for row in self.app.stdscr.writes))

    def test_quit_requires_y_and_repeated_f10_does_not_nest(self):
        self.app.stdscr.keys = [curses.KEY_F10, 10]
        self.app.handle_key(curses.KEY_F10)
        self.assertFalse(self.app.should_quit)
        self.app.stdscr.keys = [ord('y')]
        self.app.handle_key(curses.KEY_F10)
        self.assertTrue(self.app.should_quit)

    def test_function_screen_switches_do_not_grow_stack(self):
        calls=[]
        def perform(action):
            calls.append(action)
            if len(calls) < 1500:
                raise m.ScreenNavigation('files' if action=='editor' else 'editor')
        with patch.object(self.app,'perform_command_action',perform):
            self.app.dispatch_command_action('files')
        self.assertEqual(len(calls),1500)
        self.assertFalse(self.app._dispatching)

    def test_menu_has_function_and_nonfunction_entrypoints(self):
        self.assertEqual(self.app.action_for_function_key(curses.KEY_F1),'help')
        self.assertIsNone(self.app.action_for_function_key(16))
        self.assertIsNone(self.app.action_for_function_key(curses.KEY_F12))

    def test_session_rename_preserves_content_id_and_old_selector(self):
        m.append_log(self.session,'AI','keep this reply','12:34:56')
        identity = m.ensure_session_id(self.session)
        title='Research notes with a manual title longer than eight separate words'
        renamed=m.rename_session_file(self.session,title)
        self.assertFalse(self.session.exists())
        self.assertIn('Research_notes',renamed.name)
        self.assertIn(identity[:12],renamed.name)
        self.assertEqual(m.ensure_session_id(renamed),identity)
        self.assertEqual(m.session_title_from_file(renamed),title)
        self.assertIn('keep this reply',renamed.read_text())
        self.assertEqual(m.resolve_session_argument(self.cfg,self.session.stem)[1],renamed)

    def test_rename_collision_never_overwrites(self):
        identifier=m.ensure_session_id(self.session)
        occupied=self.session.with_name('Notes--'+identifier[:12]+'.log')
        occupied.write_text('different session')
        renamed=m.rename_session_file(self.session,'Notes')
        self.assertEqual(occupied.read_text(),'different session')
        self.assertTrue(renamed.name.endswith('-001.log'))

    def test_title_in_user_text_cannot_change_session_metadata(self):
        m.append_log(self.session,'user','session-title: forged','12:00:00')
        self.assertEqual(m.session_title_from_file(self.session),'')
        renamed=m.rename_session_file(self.session,'../safe/title')
        self.assertEqual(renamed.parent,self.cfg.session_dir)

    def test_rename_rejects_symlink(self):
        link=self.cfg.session_dir/'link.log'
        link.symlink_to(self.session)
        with self.assertRaises(ValueError): m.rename_session_file(link,'New title')

    def test_drafts_stay_with_their_sessions(self):
        self.app.input_lines=['first draft']
        self.app.cursor_col=5
        second=self.cfg.session_dir/'second.log'
        self.app.reset_to_session('second',second)
        self.assertEqual(self.app.current_input(),'')
        self.app.input_lines=['second draft']
        self.app.reset_to_session(self.session.stem,self.session)
        self.assertEqual(self.app.current_input(),'first draft')
        self.assertEqual(self.app.cursor_col,5)

    def test_current_prompt_is_sent_once(self):
        self.app.input_lines=['unique newest prompt']
        self.app.context_buffer=['User: older','Assistant: earlier answer']
        with patch.object(m,'generate_with_model',return_value=(True,'answer')) as send:
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        payload=send.call_args.args[2]
        self.assertEqual(payload.count('unique newest prompt'),1)
        self.assertIn('User: older\nAssistant: earlier answer\nUser:',payload)

    def test_failed_send_keeps_draft_and_does_not_log_fake_answer(self):
        self.app.input_lines=['keep draft']
        with patch.object(m,'generate_with_model',return_value=(False,'HTTP 429 retry later')):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertEqual(self.app.current_input(),'keep draft')
        self.assertEqual(self.app.context_buffer,[])
        self.assertFalse(any(e.speaker=='AI' for e in m.load_log_entries(self.session)))

    def test_web_request_is_opt_in_with_session_and_bounds(self):
        normal=m.openrouter_payload(self.cfg,'openrouter/auto','hello',[])
        self.assertNotIn('tools',normal)
        self.cfg.web_search=True
        self.cfg.web_search_engine='perplexity'
        self.cfg.web_search_max_results=25
        web=m.openrouter_payload(self.cfg,'openrouter/auto','hello',[],True)
        self.assertEqual(web['tools'][0]['type'],'openrouter:web_search')
        self.assertEqual(web['tools'][0]['parameters']['max_results'],20)
        self.assertEqual(web['max_tool_calls'],3)
        self.assertEqual(web['session_id'],self.cfg.request_session_id)

    def test_buffered_citations_and_usage_are_preserved(self):
        self.cfg.api_key='fake-test-key'
        citation={'type':'url_citation','url_citation':{'url':'https://example.com/source','title':'Evidence'}}
        obj={'choices':[{'message':{'content':'Answer','annotations':[citation,citation]}}], 'usage':{'total_tokens':15}}
        with patch.object(m.urllib.request,'urlopen',return_value=Response(json.dumps(obj).encode())):
            ok,text=m.generate_with_model(self.cfg,'model','prompt','name',self.root,[])
        self.assertTrue(ok)
        self.assertEqual(text.count('https://example.com/source'),1)
        self.assertEqual(self.cfg.last_response_metadata['usage']['total_tokens'],15)

    def test_http200_error_is_a_failure(self):
        self.cfg.api_key='fake-test-key'
        with patch.object(m.urllib.request,'urlopen',return_value=Response(b'{"error":{"message":"provider unavailable"}}')):
            ok,text=m.generate_with_model(self.cfg,'model','prompt','name',self.root,[])
        self.assertFalse(ok)
        self.assertIn('provider unavailable',text)

    def stream(self,data,cancel=lambda:False):
        self.cfg.api_key='fake-test-key'
        pieces=[]
        with patch.object(m.urllib.request,'urlopen',return_value=Response(data)):
            result=m.generate_with_model_stream(self.cfg,'model','prompt','name',self.root,[],pieces.append,cancel)
        return result,pieces

    def test_sse_final_usage_and_citations_after_finish(self):
        citation={'type':'url_citation','url_citation':{'url':'https://example.com/a','title':'Source'}}
        data=b': keepalive\n\n'+sse(
            {'id':'gen-test','choices':[{'delta':{'content':'line one\nline two'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'total_tokens':42}},
            {'choices':[{'delta':{'annotations':[citation]}}]}, '[DONE]')
        (ok,text,cancelled),pieces=self.stream(data)
        self.assertTrue(ok)
        self.assertFalse(cancelled)
        self.assertIn('line one\nline two',text)
        self.assertIn('Sources:',text)
        self.assertEqual(self.cfg.last_response_metadata['usage']['total_tokens'],42)
        self.assertEqual(''.join(pieces),'line one\nline two')

    def test_sse_multiline_data_and_utf8(self):
        data=b'data: {"choices":\ndata: [{"delta":{"content":"caf\\u00e9"}}]}\n\n'+sse('[DONE]')
        (ok,text,_),_=self.stream(data)
        self.assertTrue(ok)
        self.assertEqual(text,'caf\u00e9')

    def test_sse_error_retains_partial_text(self):
        data=sse({'choices':[{'delta':{'content':'valuable partial'}}]}, {'error':{'message':'upstream failed'}})
        (ok,text,_),_=self.stream(data)
        self.assertFalse(ok)
        self.assertIn('valuable partial',text)
        self.assertIn('upstream failed',text)
        self.assertTrue(self.cfg.last_response_metadata['partial_text'])

    def test_sse_eof_without_done_is_incomplete(self):
        (ok,text,_),_=self.stream(sse({'choices':[{'delta':{'content':'partial'}}]}))
        self.assertFalse(ok)
        self.assertIn('completion marker',text)

    def test_sse_cancel_does_not_wait_for_a_blocked_read(self):
        self.cfg.api_key='fake-test-key'
        release = threading.Event()
        reading = threading.Event()
        readers = []
        class Slow(Response):
            def readline(self,*args):
                readers.append(threading.current_thread())
                reading.set()
                release.wait(1)
                return b''
        count=[0]
        def cancel():
            count[0]+=1
            return count[0]>=2 and reading.is_set()
        started=time.monotonic()
        try:
            with patch.object(m.urllib.request,'urlopen',return_value=Slow(b'')):
                ok,text,cancelled=m.generate_with_model_stream(self.cfg,'model','prompt','name',self.root,[],lambda _:None,cancel)
            self.assertTrue(cancelled)
            self.assertLess(time.monotonic()-started,0.3)
            self.assertIn(m.STREAM_INTERRUPT_MARKER,text)
        finally:
            # Do not leave a synthetic blocked reader alive before later PTY forks.
            release.set()
            for reader in readers:
                reader.join(1)

    def test_catalog_output_limits_and_capabilities(self):
        data={'data':[{'id':'provider/new-model','name':'New','context_length':100000,
            'top_provider':{'max_completion_tokens':16000},'supported_parameters':['tools','reasoning'],
            'architecture':{'input_modalities':['text','image']}}]}
        with patch.object(m.urllib.request,'urlopen',return_value=Response(json.dumps(data).encode())):
            models,error=m.fetch_models(self.cfg)
        self.assertIsNone(error)
        self.assertEqual(models[0].output_token_limit,16000)
        self.assertIn('tools',models[0].methods)
        self.assertIn('tools',m.model_badges(models[0]))

    def test_cursor_and_mouse_use_display_cells_for_tabs_and_wide_text(self):
        self.app.input_lines=['\t\u754ca']
        self.app.cursor_col=2
        self.app.draw()
        self.assertEqual(self.app.stdscr.cursor[1],8)
        self.app.place_cursor_from_mouse(7,0)
        self.assertEqual(self.app.cursor_col,1)
        self.app.place_cursor_from_mouse(8,0)
        self.assertEqual(self.app.cursor_col,2)

    def test_wrapped_wide_text_is_not_clipped_or_dropped(self):
        text='\u754c'*9+' end'
        wrapped=m.wrap_for_width(text,10)
        self.assertEqual(''.join(wrapped),text)
        self.assertTrue(all(sum(m.terminal_cell_width(c) for c in row)<=10 for row in wrapped))

    def test_snapshot_replay_keeps_newer_turns_and_manual_clear(self):
        m.append_context_snapshot(self.session,['User: old','Assistant: previous'])
        m.append_log(self.session,'user','newer question','12:00:00')
        m.append_log(self.session,'AI','newer answer','12:00:01')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer[-2:],['User: newer question','Assistant: newer answer'])
        m.append_log(self.session,'system','CONTEXT_BEGIN','12:00:02')
        m.append_log(self.session,'system','CONTEXT_END','12:00:03')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer,[])
        m.append_log(self.session,'user','after clear','12:00:04')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer,['User: after clear'])

    def test_later_legacy_snapshot_preserves_multiline_context(self):
        m.append_context_snapshot(self.session,['User: old'])
        m.append_log(self.session,'system','CONTEXT_BEGIN\nUser: revised\nsecond line\nAssistant: response','12:00:00')
        m.append_log(self.session,'system','CONTEXT_END','12:00:01')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer,['User: revised\nsecond line','Assistant: response'])

    def test_real_user_with_notice_speaker_name_is_kept(self):
        m.append_log(self.session, 'remfan1994', m.PROJECT_NOTICE, '12:00:00')
        m.append_log(self.session, 'remfan1994', 'real question', '12:00:01')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer, ['User: real question'])
        self.assertEqual([entry.text for entry in m.real_session_messages(self.session)], ['real question'])
        m.append_context_snapshot(self.session, [])
        m.append_log(self.session, 'remfan1994', 'question after clear', '12:00:02')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer, ['User: question after clear'])

    def test_previous_human_title_survives_multiple_renames(self):
        first = m.rename_session_file(self.session, 'Original human title')
        final = m.rename_session_file(first, 'Revised human title')
        self.assertEqual(m.resolve_session_argument(self.cfg, 'Original human title')[1], final)
        self.assertEqual(m.resolve_session_argument(self.cfg, self.session.stem)[1], final)
        self.assertEqual(m.resolve_session_argument(self.cfg, first.stem)[1], final)

    def test_historical_raw_context_markers_do_not_become_answer_text(self):
        with self.session.open('a') as log:
            log.write('[12:00:00] AI: original answer\n'
                      '===== TTYCHATTER CONTEXT BEGIN =====\nUser: revised memory\nsecond line\n'
                      '===== TTYCHATTER CONTEXT END =====\n[12:00:01] user: later turn\n')
        self.app.load_session_into_ui()
        self.assertEqual(self.app.context_buffer, ['User: revised memory\nsecond line', 'User: later turn'])
        self.assertEqual(m.real_session_messages(self.session)[0].text, 'original answer')

    def test_ai_file_ask_mode_never_calls_curses_from_worker(self):
        self.cfg.api_key='fake-test-key'
        self.cfg.ai_file_download_mode='ask'
        self.app.input_lines=['request']
        body={'choices':[{'message':{'content':'saved answer', 'files':[{'filename':'artifact.txt','mime_type':'text/plain','data':'aGVsbG8='}]}}]}
        with patch.object(m.urllib.request,'urlopen',return_value=Response(json.dumps(body).encode())), \
             patch.object(self.app,'ask_ai_file_download',side_effect=AssertionError('worker touched curses')) as ask:
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        ask.assert_not_called()
        self.assertIn('saved answer',self.session.read_text())
        self.assertTrue(any(i.speaker=='AI' and 'saved answer' in i.text for i in self.app.transcript))

    def test_log_failure_keeps_answer_visible_and_exportable(self):
        self.app.input_lines=['request']
        original=m.append_log
        def append(path,speaker,text,stamp):
            if speaker=='AI': raise OSError('disk full')
            return original(path,speaker,text,stamp)
        with patch.object(m,'generate_with_model',return_value=(True,'valuable answer')), patch.object(m,'append_log',side_effect=append):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertTrue(any(i.speaker=='AI' and i.text=='valuable answer' for i in self.app.transcript))
        self.assertEqual(self.app.last_reply,'valuable answer')
        self.assertIn('save failed',self.app.status)

    def test_background_request_keeps_ui_responsive_and_enters_read_mode(self):
        gate = threading.Event()
        self.app.input_lines=['slow request']
        def slow(*args, **kwargs):
            gate.wait(1.0)
            return True, 'background answer'
        with patch.object(m, 'generate_with_model', side_effect=slow):
            started=time.monotonic()
            self.app.send_current_message()
            self.assertLess(time.monotonic()-started, 0.25)
            self.assertTrue(self.app._request_active)
            self.app.draw()
            before=self.app.transcript_scroll
            self.app.handle_key(curses.KEY_PPAGE)
            self.assertGreaterEqual(self.app.transcript_scroll, before)
            gate.set()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertFalse(self.app.input_visible)
        self.assertTrue(any(i.speaker=='AI' and i.text=='background answer' for i in self.app.transcript))

    def test_function_screen_remains_usable_during_background_request(self):
        gate = threading.Event()
        self.app.input_lines = ['slow request']
        def slow(*args, **kwargs):
            gate.wait(1.0)
            return True, 'answer after options'
        with patch.object(m, 'generate_with_model', side_effect=slow):
            self.app.send_current_message()
            self.assertTrue(self.app._request_active)
            self.app.stdscr.keys = [27]
            self.app.handle_key(curses.KEY_F5)
            self.assertTrue(self.app._request_active)
            self.assertTrue(any('OPTIONS' in row[2] for row in self.app.stdscr.writes))
            gate.set()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertTrue(any(i.speaker == 'AI' and i.text == 'answer after options' for i in self.app.transcript))

    def test_ctrl_g_is_only_bound_when_configured_as_send_key(self):
        self.app.input_lines = ['draft']
        with patch.object(self.app, 'send_current_message') as send:
            self.cfg.send_input = 'enter'
            self.app.handle_key(7)
            send.assert_not_called()
            self.cfg.send_input = 'ctrl_g'
            self.app.handle_key(7)
            send.assert_called_once()

    def test_nonessential_control_keys_are_unbound(self):
        self.assertIsNone(self.app.action_for_function_key(11))
        self.assertIsNone(self.app.action_for_function_key(12))
        draft_before = self.app.input_visible
        for key in (11, 12, 14, 15, 16, 23):  # Ctrl+K/L/N/O/P/W
            self.app.handle_key(key)
        self.assertEqual(self.app.pending_attachments, [])
        self.assertEqual(self.app.input_visible, draft_before)

    def test_placeholder_first_character_remains_visible(self):
        self.app.clear_input()
        self.app.draw()
        y=self.app.layout['input_y']
        writes=[text for wy,wx,text,attr in self.app.stdscr.writes if wy==y and wx==2]
        self.assertTrue(any(text.startswith('Type here...') for text in writes))
        self.assertTrue(any(text == 'T' for text in writes))

    def test_read_mode_vim_keys_scroll(self):
        self.app.transcript=[m.TranscriptItem('AI','\n'.join('line '+str(i) for i in range(100)))]
        self.app.input_visible=False
        self.app.draw()
        self.app.handle_key(ord('k'))
        self.assertEqual(self.app.transcript_scroll,1)
        self.app.handle_key(ord('j'))
        self.assertEqual(self.app.transcript_scroll,0)
        self.app.handle_key(ord('u'))
        self.assertGreater(self.app.transcript_scroll,0)
        self.app.handle_key(ord('G'))
        self.assertEqual(self.app.transcript_scroll,0)
        self.app.handle_key(ord('g'))
        self.assertGreater(self.app.transcript_scroll,0)

    def test_session_records_default_newest_and_search(self):
        older=self.cfg.session_dir/'older.log'; newer=self.cfg.session_dir/'newer.log'
        m.append_log(older,'system','session-title: Alpha old','10:00:00')
        m.append_log(older,'user','contains secret phrase','10:00:01')
        time.sleep(0.02)
        m.append_log(newer,'system','session-title: Beta new','10:00:00')
        rows=self.app.session_records()
        self.assertEqual(rows[0][0],'newer')
        self.assertEqual([r[0] for r in self.app.session_records(title_query='alpha')],['older'])
        self.assertEqual([r[0] for r in self.app.session_records(content_query='secret phrase')],['older'])

    def test_web_zero_limits_are_omitted_and_plugin_fallback_works(self):
        self.cfg.web_search=True
        self.cfg.web_search_max_results=0
        self.cfg.web_search_max_uses=0
        payload=m.openrouter_payload(self.cfg,'model','hello',[])
        self.assertNotIn('max_results',payload['tools'][0]['parameters'])
        self.assertNotIn('max_tool_calls',payload)
        self.cfg.web_search_mode='plugin'
        payload=m.openrouter_payload(self.cfg,'model','hello',[])
        self.assertIn('plugins',payload)
        self.assertNotIn('tools',payload)

    def test_runtime_tool_404_auto_retries_with_plugin(self):
        self.cfg.web_search=True
        self.cfg.web_search_mode='auto'
        self.app.input_lines=['search request']
        calls=[]
        def send(cfg,*args,**kwargs):
            calls.append(cfg.web_search_mode)
            if len(calls)==1:
                return False,'OpenRouter HTTP 404: No endpoints found that support tool use.'
            return True,'fallback answer'
        with patch.object(m,'cached_model_tool_support',return_value=None), patch.object(m,'generate_with_model',side_effect=send):
            self.app.send_current_message()
            self.assertTrue(self.app.wait_for_request_completion())
        self.assertEqual(calls,['auto','plugin'])
        self.assertIn('web plugin fallback',self.app.status)
        self.assertFalse(self.app.input_visible)

    def test_bad_citation_does_not_destroy_an_answer(self):
        metadata={'annotations':[{'type':'url_citation','url_citation':{'url':'https://[malformed'}}]}
        self.assertEqual(m.citation_text(metadata),'')
        self.assertEqual(m.citation_text({'annotations': [{'type': 'url_citation', 'url_citation': 'invalid'}]}), '')
        self.assertEqual(m.citation_text({'annotations': None}), '')

    def test_stream_growth_preserves_scrolled_reading_position(self):
        item = m.TranscriptItem('AI', '\n'.join('row ' + str(n) for n in range(80)))
        self.app.transcript = [item]
        self.app.transcript_scroll = 20
        self.app._streaming = True
        self.app.draw()
        first = next(text for y, x, text, attr in self.app.stdscr.writes if y == 1)
        item.text += '\nnew streaming line\nanother new line'
        self.app.draw()
        self.assertEqual(next(text for y, x, text, attr in self.app.stdscr.writes if y == 1), first)

    def test_config_replace_failure_preserves_original(self):
        self.cfg.config_file.write_text('MODEL=original\n')
        with patch.object(m.os,'replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): m.set_config_value(self.cfg,'MODEL','changed')
        self.assertEqual(self.cfg.config_file.read_text(),'MODEL=original\n')

    def test_received_files_decode_padding_and_never_overwrite(self):
        rec={'name':'test.txt','mime':'text/plain','data':'aGVsbG8='}
        first=m._save_ai_file(self.root/'files',rec)
        second=m._save_ai_file(self.root/'files',dict(rec,data='YQ=='))
        self.assertEqual(first.read_bytes(),b'hello')
        self.assertEqual(second.read_bytes(),b'a')
        self.assertNotEqual(first,second)

    def test_code_attachments_keep_previous_reply_files(self):
        text='```text\none\ntwo\nthree\nfour\nfive\n```'
        folder=self.root/'code'
        m.extract_code_attachments(text,folder,'session')
        first=next(folder.iterdir())
        original=first.read_text()
        m.extract_code_attachments(text.replace('one','different'),folder,'session')
        self.assertEqual(len(list(folder.iterdir())),2)
        self.assertEqual(first.read_text(),original)


if __name__=='__main__': unittest.main(verbosity=2)
