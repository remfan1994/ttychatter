#!/usr/bin/env python3
"""Compare two C binaries against an identical offline 10,000-turn log.

Example from the package root:
  python3 c/tests/benchmark_context.py /path/to/old/ttychatter c/ttychatter

This is an optional measurement tool, not a timing-based test gate. It reports
individual samples and checks exact output equality. RSS is in KiB on Linux;
other systems report their native wait4 ru_maxrss units. No API calls are made.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def measure(binary, log, directory, label):
    env = dict(os.environ, XDG_CONFIG_HOME=str(directory / "config"),
               XDG_DATA_HOME=str(directory / "data"),
               XDG_CACHE_HOME=str(directory / "cache"))
    for key in ("OPENROUTER_API_KEY", "TTYCHATTER_API_KEY"):
        env.pop(key, None)
    output, errors = directory / (label + ".out"), directory / (label + ".err")
    with output.open("wb") as out, errors.open("wb") as err:
        start = time.perf_counter()
        process = subprocess.Popen([str(binary), "--show-memory", str(log)],
                                   env=env, stdout=out, stderr=err)
        _, status, usage = os.wait4(process.pid, 0)
        seconds = time.perf_counter() - start
        process.returncode = os.waitstatus_to_exitcode(status)
    if process.returncode:
        raise RuntimeError(errors.read_text(errors="replace"))
    return {"elapsed_seconds": round(seconds, 6),
            "peak_rss": usage.ru_maxrss,
            "output_sha256": digest(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("updated", type=Path)
    parser.add_argument("--turns", type=int, default=10000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.turns < 1 or args.runs < 1:
        parser.error("--turns and --runs must be positive")
    results = {"platform": platform.platform(), "turns": args.turns,
               "rss_unit": "KiB" if sys.platform.startswith("linux") else "OS-native",
               "samples": {}}
    with tempfile.TemporaryDirectory(prefix="ttychatter-context-benchmark-") as root:
        directory = Path(root)
        log = directory / "history.log"
        history = []
        with log.open("w", encoding="ascii", newline="\n") as output:
            for i in range(args.turns):
                question = f"question {i} " + "q" * 90
                answer = f"answer {i} " + "a" * 90
                history = (history + ["User: " + question, "Assistant: " + answer])[-24:]
                output.write("[01:02:03] User: " + question + "\n[01:02:03] AI: " + answer + "\n")
                output.write("[01:02:03] system: CONTEXT_SNAPSHOT_JSON " + json.dumps(history) + "\n\n")
        results.update(fixture_bytes=log.stat().st_size, fixture_sha256=digest(log))
        for label, path in (("baseline", args.baseline), ("updated", args.updated)):
            binary = path.resolve(strict=True)
            samples = [measure(binary, log, directory, f"{label}-{i}") for i in range(args.runs)]
            results["samples"][label] = {"binary_sha256": digest(binary), "runs": samples,
                                          "median_seconds": statistics.median(s["elapsed_seconds"] for s in samples),
                                          "median_peak_rss": statistics.median(s["peak_rss"] for s in samples)}
        hashes = {sample["output_sha256"] for group in results["samples"].values()
                  for sample in group["runs"]}
        results["outputs_identical"] = len(hashes) == 1
        if not results["outputs_identical"]:
            raise RuntimeError("context outputs differ between binaries or runs")
    print(json.dumps(results, indent=2, sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    main()
