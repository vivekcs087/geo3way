"""Minimal statement-coverage tool (stdlib only): runs the unittest suite under sys.settrace
and reports executed vs executable lines for every module in src/geocvkit."""
import os
import sys
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "src", "geo3way")
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.join(ROOT, "tests"))
executed: dict[str, set[int]] = {}


def tracer(frame, event, arg):
    fn = frame.f_code.co_filename
    if fn.startswith(PKG):
        if event == "line":
            executed.setdefault(fn, set()).add(frame.f_lineno)
        return tracer
    return None


def executable_lines(path):
    src = open(path).read()
    code = compile(src, path, "exec")
    lines, stack = set(), [code]
    while stack:
        c = stack.pop()
        lines.update(ln for _, _, ln in c.co_lines() if ln)  # 0 = synthetic module line
        stack.extend(k for k in c.co_consts if isinstance(k, types.CodeType))
    # drop lines excluded with pragma and docstring-only/def lines that never fire 'line'
    text = src.splitlines()
    return {ln for ln in lines if "pragma: no cover" not in text[ln - 1]}


sys.settrace(tracer)
threading.settrace(tracer)
suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"))
# import package modules *after* tracing starts so module-level lines count
result = unittest.TextTestRunner(verbosity=0).run(suite)
sys.settrace(None)

tot_e = tot_x = 0
print(f"\n{'module':<16}{'stmts':>7}{'missed':>8}{'cover':>8}")
for f in sorted(os.listdir(PKG)):
    if not f.endswith(".py"):
        continue
    p = os.path.join(PKG, f)
    exe = executable_lines(p)
    hit = executed.get(p, set()) & exe
    miss = sorted(exe - hit)
    tot_e += len(exe)
    tot_x += len(hit)
    print(f"{f:<16}{len(exe):>7}{len(miss):>8}{100 * len(hit) / len(exe):>7.1f}%"
          + (f"   missed: {miss[:12]}" if miss else ""))
print(f"{'TOTAL':<16}{tot_e:>7}{tot_e - tot_x:>8}{100 * tot_x / tot_e:>7.1f}%")
sys.exit(0 if result.wasSuccessful() else 1)
