#!/usr/bin/env python3
"""Focused offline regression tests for both shell frontends. No API calls."""
import json
import os
from pathlib import Path
import re
import pty
import select
import signal
import termios
import time
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
ANNOTATION={"type":"url_citation","url_citation":{"url":"https://example.test/source","title":"A source","start_index":0,"end_index":5}}
RESPONSE={"id":"gen-test","model":"test/model","usage":{"prompt_tokens":9,"completion_tokens":3},"choices":[{"message":{"content":"Hello","annotations":[ANNOTATION,ANNOTATION]}}]}
EVENTS=[{"id":"gen-stream","choices":[{"delta":{"content":"one"}}]}, {"choices":[{"delta":{"content":"\n\n"}}]}, {"choices":[{"delta":{"content":"two"},"finish_reason":"stop"}]}, {"choices":[{"delta":{"annotations":[ANNOTATION]}}],"usage":{"total_tokens":12}}]

class ShellClients(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tty-shell-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.env=dict(os.environ, XDG_CONFIG_HOME=str(self.base/'config'), XDG_DATA_HOME=str(self.base/'data'), XDG_CACHE_HOME=str(self.base/'cache'), OPENROUTER_API_KEY='offline-fixture-key')
        self.libs={}
        for name in ('python','bash'):
            source=(ROOT/'nonc'/('ttychatter.'+name)).read_text()
            prefix=source[:source.index('\nwhile [ "$#" -gt 0 ]; do')]
            prefix+='\n'+re.search(r'^send_one_message\(\) \{.*?^\}',source,re.M|re.S).group(0)+'\n'
            path=self.base/(name+'.sh'); path.write_text(prefix); self.libs[name]=path
        self.response=self.base/'response.json'; self.response.write_text(json.dumps(RESPONSE))
        self.events=self.base/'stream.txt'; self.events.write_text(''.join('data: '+json.dumps(x)+'\r\n\r\n' for x in EVENTS)+'data: [DONE]\r\n\r\n')
        bindir=self.base/'bin'; bindir.mkdir()
        curl=bindir/'curl'
        curl.write_text('''#!/usr/bin/env python3
import json,os,sys,time
from pathlib import Path
args=sys.argv[1:]
if os.environ.get("TC_CURL_PID"): Path(os.environ["TC_CURL_PID"]).write_text(str(os.getpid()))
if "-D" in args: Path(args[args.index("-D")+1]).write_text("HTTP/1.1 "+os.environ.get("TC_HTTP","200")+" OK\\r\\n")
if "-o" in args:
    Path(args[args.index("-o")+1]).write_text(Path(os.environ["TC_RESPONSE"]).read_text())
    print(os.environ.get("TC_HTTP","200"),end="")
else:
    for line in Path(os.environ["TC_EVENTS"]).read_text().splitlines(True):
        print(line,end="",flush=True); time.sleep(float(os.environ.get("TC_DELAY","0")))
    time.sleep(float(os.environ.get("TC_AFTER_DONE_DELAY","0")))
'''); curl.chmod(0o755)
        self.env.update(PATH=str(bindir)+os.pathsep+self.env.get('PATH',''),TC_RESPONSE=str(self.response),TC_EVENTS=str(self.events))
        # A subprocess startup hook intercepts urllib too. Every URL is local fixture data.
        self.site=self.base/'site'; self.site.mkdir()
        (self.site/'sitecustomize.py').write_text('''import os,time,urllib.request
class Fixture:
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def __iter__(self):
        for line in open(os.environ["TC_EVENTS"],"rb"):
            yield line; time.sleep(float(os.environ.get("TC_DELAY","0")))
def urlopen(*a,**kw): return Fixture()
if os.environ.get("TC_MOCK_URLLIB")=="1": urllib.request.urlopen=urlopen
''')

    def shell(self,variant,code,input=None,env=None):
        merged=dict(self.env); merged.update(env or {})
        return subprocess.run(['bash','-c','source '+shlex.quote(str(self.libs[variant]))+'\n'+code], input=input, text=True, capture_output=True, env=merged, timeout=15)

    def program(self,variant,args,input=None,env=None):
        merged=dict(self.env); merged.update(env or {})
        return subprocess.run(['bash',str(ROOT/'nonc'/('ttychatter.'+variant)),*args],input=input,text=True,capture_output=True,env=merged,timeout=15)

    def test_payload_web_options_and_no_web_default(self):
        for v in self.libs:
            fn='make_openrouter_payload' if v=='python' else 'build_openrouter_payload'
            call="printf '%s' 'Hello' | "+fn+' test/model' if v=='python' else fn+" test/model 'Hello'"
            result=self.shell(v,'SESSION_ID=0123456789abcdef0123456789abcdef\n'+call)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout); self.assertNotIn('tools',data); self.assertEqual(data['session_id'],'0123456789abcdef0123456789abcdef')
            result=self.shell(v,'WEB_SEARCH=1; WEB_SEARCH_ENGINE=perplexity; WEB_SEARCH_MAX_RESULTS=25; WEB_SEARCH_MAX_USES=4\n'+call)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout); self.assertEqual(data['tools'][0]['type'],'openrouter:web_search')
            self.assertEqual(data['tools'][0]['parameters'],{'engine':'perplexity','max_results':20})
            self.assertEqual(data['max_tool_calls'],4)
            result=self.shell(v,'WEB_SEARCH=1; WEB_SEARCH_MAX_RESULTS=0; WEB_SEARCH_MAX_USES=0\n'+call)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout); self.assertNotIn('max_results',data['tools'][0]['parameters']); self.assertNotIn('max_tool_calls',data)
            result=self.shell(v,'web_command status')
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn('web search: off',result.stdout)

    def test_buffered_sources_and_metadata(self):
        payload=json.loads(json.dumps(RESPONSE))
        payload['choices'][0]['message']['annotations'].extend([
            {'type':'url_citation','url_citation':'invalid'},
            {'type':'url_citation','url_citation':{'url':42}},
            {'type':'url_citation','url_citation':{'url':'file:///private'}}])
        for v in self.libs:
            fn='parse_openrouter_response' if v=='python' else 'extract_openrouter_text_and_ai_files'
            result=self.shell(v,'SESSION="$SESSION_DIR/test.log"; ensure_prechat_line "$SESSION"; AI_FILE_DOWNLOAD_MODE=off\n'+fn,input=json.dumps(payload))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Hello',result.stdout); self.assertIn('Sources:',result.stdout)
            self.assertEqual(result.stdout.count('https://example.test/source'),1)
            self.assertNotIn('jq:',result.stderr); self.assertNotIn('file:///private',result.stdout)
            log=next((self.base/'data/ttychatter/openrouter/sessions').glob('test.log')).read_text()
            self.assertIn('openrouter-response:',log); self.assertIn('prompt_tokens',log); self.assertIn('url_citation',log)

    def test_stream_keeps_newline_deltas_tail_sources_and_usage(self):
        for v in self.libs:
            call='call_openrouter_model_stream test/model Hello 10' if v=='python' else 'call_model_stream test/model Hello'
            env={'PYTHONPATH':str(self.site),'TC_MOCK_URLLIB':'1'}
            result=self.shell(v,'SESSION="$SESSION_DIR/stream.log"; ensure_prechat_line "$SESSION"\n'+call,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('one\n\ntwo',result.stdout); self.assertIn('https://example.test/source',result.stdout)
            log=(self.base/'data/ttychatter/openrouter/sessions/stream.log').read_text()
            self.assertIn('total_tokens',log)

    def test_failed_stream_keeps_partial_reply_and_reports_failure(self):
        self.events.write_text('data: '+json.dumps({'choices':[{'delta':{'content':'partial'}}]})+'\n\ndata: '+json.dumps({'error':{'code':500,'message':'fixture failure'}})+'\n\n')
        for v in self.libs:
            call='call_openrouter_model_stream test/model Hello 10' if v=='python' else 'call_model_stream test/model Hello'
            result=self.shell(v,call,env={'PYTHONPATH':str(self.site),'TC_MOCK_URLLIB':'1'})
            self.assertNotEqual(result.returncode,0); self.assertIn('partial',result.stdout); self.assertIn('fixture failure',result.stdout+result.stderr)

    def test_stream_done_does_not_wait_for_transport_close(self):
        started=time.monotonic()
        result=self.shell('bash','call_model_stream test/model Hello',
                          env={'TC_AFTER_DONE_DELAY':'3'})
        elapsed=time.monotonic()-started
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('one\n\ntwo',result.stdout)
        self.assertLess(elapsed,2.0,'[DONE] must complete without waiting for a stuck transport')

    def test_bash_stream_uses_one_parser_for_many_events(self):
        real_jq=shutil.which('jq')
        if not real_jq: self.skipTest('jq required for Bash streaming')
        counter=self.base/'jq-calls.txt'
        wrapper=self.base/'bin/jq'
        wrapper.write_text('#!/bin/sh\nprintf x >> '+shlex.quote(str(counter))+
                           '\nexec '+shlex.quote(real_jq)+' "$@"\n')
        wrapper.chmod(0o755)
        event={'choices':[{'delta':{'content':'x'}}]}
        self.events.write_text(('data: '+json.dumps(event)+'\n\n')*120+'data: [DONE]\n\n')
        result=self.shell('bash','call_model_stream test/model Hello')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'x'*120)
        self.assertLess(len(counter.read_text()),12,'parser process count must not grow per token')

    def test_malformed_stream_choices_preserves_partial_reply(self):
        first={'choices':[{'delta':{'content':'partial reply'}}]}
        self.events.write_text('data: '+json.dumps(first)+'\n\ndata: '+
                               json.dumps({'choices':7})+'\n\ndata: [DONE]\n\n')
        result=self.shell('python','call_openrouter_model_stream test/model Hello 10',
                          env={'PYTHONPATH':str(self.site),'TC_MOCK_URLLIB':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('partial reply',result.stdout)
        self.assertIn('response error:',result.stdout)
        self.assertNotIn('Traceback',result.stderr)

    def test_batch_output_path_and_error_exit(self):
        for v in self.libs:
            target=self.base/(v+'-explicit.log')
            result=self.program(v,['--demo','--output',str(target),'--prompt','offline'],input='')
            self.assertEqual(result.returncode,0,result.stderr); self.assertTrue(target.exists())
            self.assertIn('session: '+str(target),result.stderr); self.assertNotIn('Name this conversation',result.stdout+result.stderr)
            self.response.write_text(json.dumps({'error':{'message':'fixture failure','code':400}}))
            target=self.base/(v+'-failure.log')
            result=self.program(v,['--output',str(target),'--prompt','offline'],input='')
            self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn('] AI:',target.read_text()); self.assertIn('response-error:',target.read_text())

    def test_named_rename_preserves_id_and_never_overwrites(self):
        for v in self.libs:
            result=self.program(v,['--demo','--session','legacy-notes','--prompt','offline'],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            logs=self.base/'data/ttychatter/openrouter/sessions'; original=logs/'legacy-notes.log'
            ident=re.search(r'session-id: ([a-f0-9]{32})',original.read_text()).group(1)
            collision=logs/('A_long_manual_session_title_with_more_than_eight_words_allowed--'+ident[:12]+'.log')
            collision.write_text('preserve this existing file')
            title='A long manual session title with more than eight words allowed'
            result=self.program(v,['--rename-session','legacy-notes',title],input='')
            self.assertEqual(result.returncode,0,result.stderr); self.assertFalse(original.exists())
            self.assertEqual(collision.read_text(),'preserve this existing file')
            marker=re.findall(r'^session: (.*)$',result.stdout,re.M)[-1]
            renamed=Path(marker); self.assertTrue(renamed.exists()); self.assertIn('-001.log',renamed.name)
            self.assertIn('session-id: '+ident,renamed.read_text()); self.assertIn('session-title: '+title,renamed.read_text())
            resumed=self.program(v,['--demo','--resume',renamed.name,'--prompt','next'],input='')
            self.assertEqual(resumed.returncode,0,resumed.stderr)
            renamed.unlink(); collision.unlink()

    def test_resume_multiline_and_omit_metadata(self):
        for v in self.libs:
            load='rebuild_context_from_session' if v=='python' else 'load_context'
            code='''SESSION="$SESSION_DIR/context.log"
cat > "$SESSION" <<'LOG'
[01:00:00] system: CONTEXT_BEGIN
[01:00:00] system: CONTEXT_END
[01:00:00] human: alpha
second line
[01:00:01] AI: beta
second reply line
[01:00:02] system: openrouter-response: {"usage":{"total_tokens":3}}
LOG
'''+load+'''\nprintf '%s\\n' "${CONTEXT_BUFFER[@]}"'''
            result=self.shell(v,code); self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('User: alpha\nsecond line',result.stdout); self.assertIn('Assistant: beta\nsecond reply line',result.stdout)
            self.assertNotIn('stale',result.stdout); self.assertNotIn('usage',result.stdout)

    def test_bash_fallback_json_controls_and_large_prompt(self):
        result=self.shell('bash',r"""
command() { if [ "$1" = -v ] && [ "$2" = jq ]; then return 1; fi; builtin command "$@"; }
build_openrouter_payload test/model $'tab\tquote"slash\\carriage\rback\bform\f'
""")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['messages'][0]['content'],'tab\tquote"slash\\carriage\rback\bform\f')
        big=self.base/'big.txt'; big.write_text('x'*200000)
        result=self.shell('bash','prompt="$(cat '+shlex.quote(str(big))+')"; build_openrouter_payload test/model "$prompt"')
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(len(json.loads(result.stdout)['messages'][0]['content']),200000)

    def test_web_cli_configuration_and_http_failure(self):
        for v in self.libs:
            result=self.program(v,['--web','--web-engine','exa','--web-max-results','7','--web-max-uses','2','--config'],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            for token in ('WEB_SEARCH=1','WEB_SEARCH_ENGINE=exa','WEB_SEARCH_MAX_RESULTS=7','WEB_SEARCH_MAX_USES=2'):
                self.assertIn(token,result.stdout)
            target=self.base/(v+'-http.log')
            result=self.program(v,['--output',str(target),'--prompt','hello'],input='',env={'TC_HTTP':'429'})
            self.assertNotEqual(result.returncode,0); self.assertNotIn('] AI:',target.read_text())

    def interact(self,args,env,respond):
        pid,master=pty.fork()
        if pid==0:
            os.execve('/bin/bash',['bash',*args],env)
        original=termios.tcgetattr(master)
        output=b''; sent=False; status=None; end=time.monotonic()+12
        try:
            while time.monotonic()<end:
                if select.select([master],[],[],0.1)[0]:
                    try: block=os.read(master,65536)
                    except OSError: block=b''
                    if block: output+=block
                    elif not block:
                        child,status=os.waitpid(pid,0); break
                if not sent:
                    reply=respond(output.decode('utf-8','replace'))
                    if reply is not None: os.write(master,reply); sent=True
                child,wait_status=os.waitpid(pid,os.WNOHANG)
                if child: status=wait_status; break
            if status is None:
                os.killpg(pid,signal.SIGKILL); os.waitpid(pid,0)
                self.fail('PTY interaction timed out: '+output.decode('utf-8','replace'))
            # A signal may kill the session leader before request cleanup.
            # Wait briefly for its descendants to close their terminal handles.
            drain_end=time.monotonic()+2.0
            while time.monotonic()<drain_end:
                if not select.select([master],[],[],0.05)[0]: continue
                try: block=os.read(master,65536)
                except OSError: break
                if not block: break
                output+=block
            restored=termios.tcgetattr(master)
            return os.waitstatus_to_exitcode(status),output.decode('utf-8','replace'),original,restored
        finally: os.close(master)

    def test_interactive_naming_prompt_and_stream_escape(self):
        cfg=self.base/'config/ttychatter/openrouter'; cfg.mkdir(parents=True)
        (cfg/'config').write_text('STARTUP_NOTICE=0\nSEND_INPUT=enter\n')
        for v in self.libs:
            args=[str(ROOT/'nonc'/('ttychatter.'+v)),'--demo','--new']
            code,out,_,_=self.interact(args,self.env,lambda output: b'Named from terminal\n:quit\n' if 'Name this conversation' in output else None)
            self.assertEqual(code,0,out); self.assertIn('Named from terminal',out)
            files=list((self.base/'data/ttychatter/openrouter/sessions').glob('Named_from_terminal--*.log'))
            self.assertTrue(files,out)
            call='call_openrouter_model_stream test/model Hello 10' if v=='python' else 'call_model_stream test/model Hello'
            args=['-c','source '+shlex.quote(str(self.libs[v]))+'\n'+call]
            env=dict(self.env,PYTHONPATH=str(self.site),TC_MOCK_URLLIB='1',TC_DELAY='0.2')
            code,out,old,new=self.interact(args,env,lambda output: b'\x1b' if 'one' in output else None)
            self.assertEqual(code,0,out); self.assertIn('[assistant stream interrupted by user]',out)
            self.assertEqual(old[3]&(termios.ECHO|termios.ICANON),new[3]&(termios.ECHO|termios.ICANON))

    def test_stream_ctrl_c_restores_terminal_and_removes_request_files(self):
        for v in self.libs:
            tmpdir=self.base/('stream-temp-'+v); tmpdir.mkdir()
            pidfile=self.base/('curl-pid-'+v)
            call='call_openrouter_model_stream test/model Hello 10' if v=='python' else 'call_model_stream test/model Hello'
            args=['-c','source '+shlex.quote(str(self.libs[v]))+'\n'+call]
            env=dict(self.env,PYTHONPATH=str(self.site),TC_MOCK_URLLIB='1',TC_DELAY='0.3',
                     TC_CURL_PID=str(pidfile),TMPDIR=str(tmpdir))
            code,out,old,new=self.interact(args,env,lambda output: b'\x03' if 'one' in output else None)
            self.assertIn(code,(-signal.SIGINT,130),out)
            self.assertEqual(old[3]&(termios.ECHO|termios.ICANON),new[3]&(termios.ECHO|termios.ICANON),out)
            self.assertEqual(list(tmpdir.iterdir()),[],'aborted request left temporary files')
            if pidfile.exists():
                with self.assertRaises(ProcessLookupError): os.kill(int(pidfile.read_text()),0)

    def test_stream_hangup_and_term_restore_terminal(self):
        for v in self.libs:
            for signum in (signal.SIGHUP,signal.SIGTERM):
                with self.subTest(variant=v,signal=signum):
                    tmpdir=self.base/('stream-signal-'+v+'-'+str(signum)); tmpdir.mkdir()
                    pidfile=self.base/('curl-signal-pid-'+v+'-'+str(signum))
                    call='call_openrouter_model_stream test/model Hello 10' if v=='python' else 'call_model_stream test/model Hello'
                    code='source '+shlex.quote(str(self.libs[v]))+'\nprintf "TEST_PID=%s\\n" "$$"\n'+call
                    env=dict(self.env,PYTHONPATH=str(self.site),TC_MOCK_URLLIB='1',TC_DELAY='0.3',
                             TC_CURL_PID=str(pidfile),TMPDIR=str(tmpdir))
                    def stop(output):
                        if 'one' not in output: return None
                        os.killpg(int(re.search(r'TEST_PID=(\d+)',output).group(1)),signum)
                        return b''
                    code,out,old,new=self.interact(['-c',code],env,stop)
                    self.assertIn(code,(-signum,128+signum),out)
                    self.assertEqual(old[3]&(termios.ECHO|termios.ICANON),new[3]&(termios.ECHO|termios.ICANON),out)
                    # The session leader can exit before the request subshell
                    # finishes its own signal cleanup. Allow that bounded exit.
                    until=time.monotonic()+1.0
                    while list(tmpdir.iterdir()) and time.monotonic()<until: time.sleep(0.01)
                    self.assertEqual(list(tmpdir.iterdir()),[])
                    if pidfile.exists():
                        with self.assertRaises(ProcessLookupError): os.kill(int(pidfile.read_text()),0)

    def test_catalog_failure_keeps_cache_and_model_test_exit_status(self):
        for v in self.libs:
            fetch='fetch_models_live_json' if v=='python' else 'fetch_models_json'
            code='printf stable-cache > "$MODEL_CACHE_FILE"\n'+fetch+'() { printf \'%s\' \'{"error":{"message":"catalog unavailable"}}\'; }\nif update_models_cache; then exit 9; fi\ncat "$MODEL_CACHE_FILE"'
            result=self.shell(v,code)
            self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout,'stable-cache')
            result=self.program(v,['--demo','--test-model','demo/auto'],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            self.response.write_text(json.dumps({'error':{'message':'fixture failure','code':400}}))
            result=self.program(v,['--test-model','test/model','--save'],input='')
            self.assertNotEqual(result.returncode,0,result.stderr)
            cfg=self.base/'config/ttychatter/openrouter/config'
            self.assertFalse(cfg.exists() and 'MODEL=test/model' in cfg.read_text())

    def test_branch_has_fresh_id_named_file_and_no_parent_snapshot(self):
        logs=self.base/'data/ttychatter/openrouter/sessions'; logs.mkdir(parents=True)
        for v in self.libs:
            parent=logs/'parent.log'
            parent.write_text('[01:00:00] system: session-id: '+('a'*32)+'\n[01:00:00] system: session-title: Parent topic\n[01:00:01] human: Question\nsecond line\n[01:00:02] AI: Answer\n[01:00:03] system: CONTEXT_BEGIN\nUser: stale\nAssistant: stale\n[01:00:04] system: CONTEXT_END\n')
            result=self.program(v,['--branch',str(parent),'2'],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            branch=Path(result.stdout.strip()); self.assertTrue(branch.exists(),result.stdout)
            self.assertTrue(branch.name.startswith('Branch_of_Parent_topic--'))
            data=branch.read_text(); self.assertIn('Question\nsecond line',data); self.assertIn('AI: Answer',data)
            self.assertNotIn('stale',data); ident=re.search(r'session-id: ([a-f0-9]{32})',data).group(1); self.assertNotEqual(ident,'a'*32)

    def test_newest_memory_snapshot_and_later_turns(self):
        for v in self.libs:
            load='rebuild_context_from_session' if v=='python' else 'load_context'
            cases=[
                ('[01:00:00] system: CONTEXT_BEGIN\nUser: old\n[01:00:01] system: CONTEXT_END\n[01:00:02] system: CONTEXT_SNAPSHOT_JSON ["User: edited\\nsecond line"]\n[01:00:03] AI: new answer\n','User: edited\nsecond line\nAssistant: new answer\n'),
                ('[01:00:00] system: CONTEXT_SNAPSHOT_JSON ["User: old"]\n[01:00:01] system: CONTEXT_BEGIN\nUser: edited\nsecond line\n[01:00:02] system: CONTEXT_END\n[01:00:03] AI: new answer\n','User: edited\nsecond line\nAssistant: new answer\n'),
                ('[01:00:00] human: forgotten\n[01:00:01] system: CONTEXT_SNAPSHOT_JSON []\n[01:00:02] human: after clear\n','User: after clear\n'),
                ('[01:00:00] human: retained\n[01:00:01] system: CONTEXT_SNAPSHOT_JSON malformed\n[01:00:02] system: CONTEXT_BEGIN\nUser: unfinished\n','User: retained\n'),
            ]
            for log,want in cases:
                fixture=self.base/'memory-fixture.log'; fixture.write_text(log)
                result=self.shell(v,'SESSION='+shlex.quote(str(fixture))+'\n'+load+'\nprintf \'%s\\n\' "${CONTEXT_BUFFER[@]}"')
                self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout,want)
            result=self.shell(v,'SESSION="$SESSION_DIR/new-snapshot.log"; CONTEXT_BUFFER=($\'User: a\\nAssistant: this is still user text\'); write_context_snapshot 01:00:00; '+load+'; printf \'%s\\n\' "${CONTEXT_BUFFER[@]}"')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,'User: a\nAssistant: this is still user text\n')

    def test_export_preserves_multiline_real_user_and_reports_write_failure(self):
        fixture=self.base/'export-fixture.log'
        notice='Everyone is encouraged to get the cruelty-free vegetarian alternatives and remember the bloodguilt curse from the Bible... http://bloodguiltcurse.net'
        fixture.write_text('[01:00:00] remfan1994: '+notice+'\n[01:00:01] system: session-title: Export test\n[01:00:02] remfan1994: CONTEXT_is actual user text\nsecond user line\n\n  indented line\n[01:00:03] AI: first reply line\n```text\ncode line\n```\n[01:00:04] system: openrouter-response: {"usage":{"total_tokens":3}}\n')
        for v in self.libs:
            for fmt in ('text','markdown'):
                result=self.program(v,['--export',str(fixture),'--format',fmt],input='')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('CONTEXT_is actual user text\nsecond user line\n\n  indented line',result.stdout)
                self.assertIn('first reply line\n```text\ncode line\n```',result.stdout)
                self.assertNotIn(notice,result.stdout); self.assertNotIn('total_tokens',result.stdout)
            result=self.program(v,['--export',str(fixture),'--format','text','--output',str(self.base)],input='')
            self.assertNotEqual(result.returncode,0)
            result=self.program(v,['--turns',str(fixture)],input='')
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn('CONTEXT_is actual user text',result.stdout); self.assertNotIn(notice,result.stdout)
            result=self.program(v,['--branch',str(fixture),'1'],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            branch=Path(result.stdout.strip()).read_text()
            self.assertIn('remfan1994: CONTEXT_is actual user text\nsecond user line',branch)
            self.assertNotIn('AI: first reply line',branch)

    def test_session_aliases_survive_renames_and_ambiguity_is_rejected(self):
        for v in self.libs:
            original='alias-origin-'+v
            first='First human title '+v
            final='Final human title '+v
            result=self.program(v,['--demo','--session',original,'--prompt','remember this'],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            result=self.program(v,['--rename-session',original,first],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            result=self.program(v,['--rename-session',first,final],input='')
            self.assertEqual(result.returncode,0,result.stderr)
            current=Path(re.findall(r'^session: (.*)$',result.stdout,re.M)[-1])
            self.assertIn('session-alias: '+original,current.read_text())
            self.assertIn('session-alias: '+first,current.read_text())
            for alias in (original,original+'.log',first,final):
                result=self.program(v,['--export',alias,'--format','text'],input='')
                self.assertEqual(result.returncode,0,result.stderr); self.assertIn('remember this',result.stdout)
                result=self.program(v,['--demo','--resume',alias,'--prompt','next'],input='')
                self.assertEqual(result.returncode,0,result.stderr); self.assertIn('session: '+str(current),result.stderr)
            # A symlink is not a second eligible session for alias matching.
            link=current.parent/('alias-link-'+v+'.log'); link.symlink_to(current)
            result=self.program(v,['--export',final,'--format','text'],input='')
            self.assertEqual(result.returncode,0,result.stderr); link.unlink()
            conflicting=current.parent/('conflicting-'+v+'.log')
            conflicting.write_text('[01:00:00] system: session-title: '+final+'\n[01:00:01] human: other session\n')
            result=self.program(v,['--demo','--resume',final,'--prompt','must not send'],input='')
            self.assertNotEqual(result.returncode,0); self.assertIn('ambiguous session name',result.stderr)
            self.assertNotIn('must not send',current.read_text()+conflicting.read_text())
            result=self.program(v,['--export',current.name,'--format','text'],input='')
            self.assertEqual(result.returncode,0,result.stderr); self.assertIn('remember this',result.stdout)

    def test_code_attachments_never_overwrite_earlier_reply(self):
        for v in self.libs:
            fn='extract_attachments' if v=='python' else 'extract_code_blocks'
            setup='SESSION_NAME='+shlex.quote('code-test-'+v)+'; CODE_ATTACHMENT_MIN_LINES=2\n'
            first=self.shell(v,setup+fn,input='before\n```python\nfirst line\nsecond line\n```\nafter\n')
            self.assertEqual(first.returncode,0,first.stderr)
            second=self.shell(v,setup+fn,input='before\n```python\nnew first\nnew second\n```\nafter\n')
            self.assertEqual(second.returncode,0,second.stderr)
            paths=sorted((self.base/'data/ttychatter/openrouter/attachments').glob('code-test-'+v+'-python-*.py'))
            self.assertEqual(len(paths),2)
            self.assertEqual(paths[0].read_text(),'first line\nsecond line\n')
            self.assertEqual(paths[1].read_text(),'new first\nnew second\n')
            short='before\n```python\nshort\n```\n```python\nunfinished\n'
            result=self.shell(v,setup+fn,input=short)
            self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout.rstrip('\n'),short.rstrip('\n'))

    def test_reply_survives_storage_and_extraction_failures(self):
        for v in self.libs:
            call='call_openrouter_model' if v=='python' else 'call_model'
            extract='extract_attachments' if v=='python' else 'extract_code_blocks'
            setup='SESSION="$SESSION_DIR/failure-'+v+'.log"; SESSION_NAME=failure; ensure_prechat_line "$SESSION"; SESSION_AUTO_TITLE=0\n'
            result=self.shell(v,setup+call+'() { rm -f "$SESSION"; mkdir "$SESSION"; printf valuable-reply; }\nsend_one_message prompt')
            self.assertNotEqual(result.returncode,0); self.assertIn('valuable-reply',result.stdout)
            self.assertIn('log write failed',result.stderr)
            # A failed extraction must not replace a successful paid reply with partial output.
            setup='SESSION="$SESSION_DIR/extraction-'+v+'.log"; SESSION_NAME=extraction; ensure_prechat_line "$SESSION"; SESSION_AUTO_TITLE=0\n'
            result=self.shell(v,setup+call+'() { printf complete-raw-reply; }\n'+extract+'() { printf broken-partial; return 1; }\nsend_one_message prompt')
            self.assertNotEqual(result.returncode,0); self.assertIn('complete-raw-reply',result.stdout); self.assertNotIn('broken-partial',result.stdout)
            result=self.shell(v,'SESSION="$SESSION_DIR/unwritable-'+v+'.log"; mkdir "$SESSION"\n'+call+'() { printf request-was-sent; }\nsend_one_message prompt')
            self.assertNotEqual(result.returncode,0); self.assertNotIn('request-was-sent',result.stdout+result.stderr)

    def test_syntax(self):
        for v in self.libs:
            result=subprocess.run(['bash','-n',str(ROOT/'nonc'/('ttychatter.'+v))],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__': unittest.main(verbosity=2)
