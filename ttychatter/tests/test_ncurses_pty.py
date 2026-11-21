#!/usr/bin/env python3
"""Actual terminal regression. Optional test dependency: pyte (not a runtime dependency)."""
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time
import unittest
try:
    import pyte
except ImportError:
    pyte = None

SOURCE = Path(__file__).resolve().parents[1] / 'nonc' / 'ttychatter.ncurses'


@unittest.skipIf(pyte is None, 'Install pyte to run real terminal-screen assertions.')
class TerminalTest(unittest.TestCase):
    def exercise(self, rows, cols):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            conf=root/'config'/'ttychatter'/'openrouter'
            conf.mkdir(parents=True)
            marker=root/'editor-ran'
            editor=root/'editor.py'
            editor.write_text('from pathlib import Path\nimport sys\nPath('+repr(str(marker))+').write_text("yes")\nPath(sys.argv[1]).write_text("editor draft")\n')
            (conf/'config').write_text('PROMPT_SESSION_NAME=1\nSTARTUP_NOTICE=0\nEDITOR='+sys.executable+' '+str(editor)+'\n')
            pid,fd=pty.fork()
            if pid==0:
                fcntl.ioctl(1,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
                env=dict(os.environ,TERM='xterm-256color',XDG_CONFIG_HOME=str(root/'config'),
                         XDG_DATA_HOME=str(root/'data'),XDG_CACHE_HOME=str(root/'cache'))
                for key in ('OPENROUTER_API_KEY','TTYCHATTER_API_KEY'):
                    env.pop(key,None)
                os.execve(sys.executable,[sys.executable,str(SOURCE),'--demo'],env)
            screen=pyte.Screen(cols,rows)
            stream=pyte.ByteStream(screen)
            raw=[]
            def view(): return '\n'.join(screen.display)
            def wait_for(text,seconds=4):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    if select.select([fd],[],[],0.03)[0]:
                        try: chunk=os.read(fd,65536)
                        except OSError: break
                        if not chunk: break
                        raw.append(chunk)
                        stream.feed(chunk)
                    if text in view(): return
                self.fail('Missing '+repr(text)+' at '+str((rows,cols))+'\n'+view()+'\n'+repr(b''.join(raw)[-1500:]))
            def send(data): os.write(fd,data)
            try:
                wait_for('NAME THIS SESSION')
                send(b'Terminal maintenance\r')
                wait_for('Terminal maintenance')
                wait_for('Draft |')
                send(b'abc\x1bOD')
                wait_for('L1 C3')
                self.assertEqual(screen.cursor.x,4)
                self.assertEqual(screen.buffer[1][0].fg,'default')
                self.assertEqual(screen.buffer[1][0].bg,'default')
                send(b'\x1b[23~')  # F11, the current draft/read binding.
                wait_for('READ W:off')
                self.assertFalse(any(line.startswith('Draft |') for line in screen.display))
                send(b'\x1bOA\x1b[5~\x1b[6~\x1b[23~')
                wait_for('L1 C3')
                self.assertIn('abc',view())
                send(b'\x1b')
                wait_for('COMMAND MENU')
                send(b'\x1b[19~')
                wait_for('EXTERNAL EDITOR')
                self.assertFalse(marker.exists())
                send(b'\x1b[18~')
                wait_for('FILES / ATTACHMENTS')
                send(b'\x1b[20~')
                wait_for('CREDITS')
                send(b'\x1b[19~')
                wait_for('EXTERNAL EDITOR')
                self.assertFalse(marker.exists())
                send(b'\r')
                wait_for('editor draft')
                self.assertTrue(marker.exists())
                send(b'\x1b')
                wait_for('COMMAND MENU')
                send(b'j' * 8 + b'\r')  # Name current session.
                wait_for('NAME THIS SESSION')
                send(b'\x15Retitled Notes\r')
                wait_for('Retitled Notes')
                wait_for('Draft |')
                logs=list((root/'data'/'ttychatter'/'openrouter'/'sessions').glob('*.log'))
                self.assertEqual(len(logs),1)
                self.assertTrue(logs[0].name.startswith('Retitled_Notes--'))
                send(b'\x1b[15~')
                wait_for('OPTIONS')
                send(b'\x1bOB ')
                wait_for('[x] Web search')
                send(b'\x1b')
                wait_for('W:on')
                send(b'hello from terminal\r')
                wait_for('READ W:on')
                send(b'g')
                wait_for('Demo mode is active')
                send(b'\x1b[21~')
                wait_for('CLOSE SESSION')
                send(b'n')
                wait_for('READ W:on')
                send(b'\x1b[21~')
                wait_for('CLOSE SESSION')
                send(b'y')
                deadline=time.monotonic()+4
                status=None
                while time.monotonic()<deadline:
                    child,status=os.waitpid(pid,os.WNOHANG)
                    if child: break
                    if select.select([fd],[],[],0.05)[0]:
                        try: os.read(fd,65536)
                        except OSError: pass
                self.assertEqual(child,pid)
                self.assertEqual(os.waitstatus_to_exitcode(status),0)
                pid=None
            finally:
                if pid:
                    try: os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: os.waitpid(pid,0)
                    except ChildProcessError: pass
                os.close(fd)

    def test_80_by_24(self): self.exercise(24,80)
    def test_40_by_12(self): self.exercise(12,40)


if __name__=='__main__': unittest.main(verbosity=2)
