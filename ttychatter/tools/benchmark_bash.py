#!/usr/bin/env python3
# Offline 2,000-event fixture used for the recorded before/after comparison.
# Run from the package root: python3 tools/benchmark_bash.py --baseline PATH
import argparse,importlib.util,json,re,subprocess,time
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--baseline', type=Path, required=True)
args=parser.parse_args()
spec=importlib.util.spec_from_file_location('shelltests', 'tests/test_shell_clients.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
t=m.ShellClients(); t.setUp()
try:
    baseline=args.baseline.read_text()
    prefix=baseline[:baseline.index('\nwhile [ "$#" -gt 0 ]; do')]
    orig=t.base/'baseline.sh'; orig.write_text(prefix)
    changed=t.libs['bash']
    event='data: '+json.dumps({'choices':[{'delta':{'content':'sample token '}}]})+'\n\n'
    t.events.write_text(event*2000+'data: [DONE]\n\n')
    for label,path in [('baseline',orig),('updated',changed)]:
        t.libs['bash']=path
        start=time.monotonic(); result=t.shell('bash','call_model_stream test/model Hello'); elapsed=time.monotonic()-start
        print(label, 'seconds=%.4f'%elapsed, 'returncode=',result.returncode,'reply_bytes=',len(result.stdout.encode()), 'expected_reply=',result.stdout=='sample token '*2000+'\n')
finally: t.doCleanups()
