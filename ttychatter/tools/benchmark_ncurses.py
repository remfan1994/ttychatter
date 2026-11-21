#!/usr/bin/env python3
"""Offline synthetic comparison; no API key, live request or personal log used."""
import argparse
import gc
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def measure(module, root, log, text):
    cfg = module.Config(session_dir=root, context_turns=12, startup_notice=False,
                        prompt_session_name=False, config_file=root / 'config')
    app = module.AICursesApp(cfg, log.stem, log)
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    app.load_session_into_ui()
    replay_seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        rows = module.wrap_for_width(text, 80)
        timings.append(time.perf_counter() - started)
    return {
        'version': module.VERSION,
        'replay_seconds_with_tracemalloc': replay_seconds,
        'replay_peak_python_bytes': peak,
        'context_entries': len(app.context_buffer),
        'visible_entries': len(app.transcript),
        'wrap_seconds_median_of_3': statistics.median(timings),
        'wrapped_rows': len(rows),
    }, (app.context_buffer, [(x.speaker, x.text, x.timestamp) for x in app.transcript], rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--current', type=Path, default=Path(__file__).resolve().parents[1] / 'nonc' / 'ttychatter.ncurses')
    args = parser.parse_args()
    old = load(args.baseline, 'benchmark_ncurses_old')
    new = load(args.current, 'benchmark_ncurses_new')
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        log = root / 'history.log'
        with log.open('w') as fh:
            fh.write('[00:00:00] system: session-title: Synthetic performance fixture\n')
            for index in range(4096):
                role = 'AI' if index % 2 else 'user'
                fh.write('[00:00:01] ' + role + ': ' + str(index) + ' ' + 'x' * 4096 + '\n')
        text = 'longword' * 32768
        before, before_output = measure(old, root, log, text)
        after, after_output = measure(new, root, log, text)
        if before_output != after_output:
            raise SystemExit('ERROR: benchmark outputs differ')
        print(json.dumps({'history_bytes': log.stat().st_size, 'wrap_characters': len(text),
                          'outputs_identical': True, 'before': before, 'after': after}, indent=2))


if __name__ == '__main__':
    main()
