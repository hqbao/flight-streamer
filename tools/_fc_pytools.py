"""
Resolve the sibling flight-controller checkout that both dblink host tools need.

WHY THIS FILE EXISTS
--------------------
dblink is a standalone repository, but its two matplotlib dashboards reuse the
shared UI (user interface) toolkit `_ui.py` from the flight-controller repository
rather than forking a second copy of the theme. That is a HARD dependency on a
sibling checkout at a fixed relative path, and it is invisible to everything in
this repository: nothing here is built, imported or tested when flight-controller
moves a file, so the coupling can break without a single failing check.

It already did. flight-controller commit 8f504115 (2026-07-26, "tools: drop the
web-replaced Qt GUI, move kept Python to tools/pytools/") moved `tools/_ui.py`
to `tools/pytools/_ui.py`. Both tools here had hardcoded the old directory, so
both died at import with:

    ModuleNotFoundError: No module named '_ui'

— which names the module and nothing else: not the repository it comes from, not
the path that was tried, not the fix. The README still told the operator to run
them.

So the cross-repository path is declared ONCE, here, and a miss fails LOUDLY
before the tool touches a serial port: it prints the path tried, the file
expected there, and what to do. The two failure modes get different messages
because they need different fixes — a missing sibling checkout is the operator's
to clone, whereas a checkout whose layout moved is this file's constant to
update. In the moved case the message names the new location, so the next
recurrence diagnoses itself.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# The sibling-repository contract, relative to this file (<dblink>/tools/).
# `pytools` is a flat directory and NOT a Python package — it has no
# __init__.py — so it goes on sys.path and `_ui` is imported by bare name. That
# is the same convention flight-controller's own tools use internally (e.g.
# tools/pytools/tilt_comp_derive.py:37 `from _ui import find_serial_port`).
_FC_REPO_DIR = 'flight-controller'
_REL_PYTOOLS = ('..', '..', _FC_REPO_DIR, 'tools', 'pytools')
_MODULE = '_ui.py'


def add_flight_controller_pytools() -> str:
    """Put flight-controller/tools/pytools on sys.path and return that path.

    Exits with a specific, actionable message (never a bare ModuleNotFoundError)
    when the sibling checkout or the module is absent.

    APPENDS rather than inserts at the front: that directory holds ~30 modules,
    and giving it priority over the standard library and site-packages would let
    any of them shadow a same-named import. Nothing there needs priority — only
    `_ui` is imported from it.
    """
    pytools = os.path.normpath(os.path.join(_HERE, *_REL_PYTOOLS))
    module = os.path.join(pytools, _MODULE)

    if not os.path.isfile(module):
        raise SystemExit(_explain(pytools, module))

    if pytools not in sys.path:
        sys.path.append(pytools)
    return pytools


def _explain(pytools: str, module: str) -> str:
    """Build the operator-facing failure message."""
    repo = os.path.normpath(os.path.join(_HERE, '..', '..', _FC_REPO_DIR))
    parent = os.path.dirname(repo)
    out = [
        '',
        'dblink host tools: missing cross-repository dependency.',
        '',
        f'  needed    : {_MODULE} — the shared matplotlib dashboard primitives',
        '              (theme, panels, buttons) these dashboards are drawn with',
        f'  looked in : {pytools}',
        f'  expected  : {module}',
        '',
    ]

    if not os.path.isdir(repo):
        out += [
            'The flight-controller repository is not checked out at:',
            f'  {repo}',
            '',
            'These tools reuse its UI toolkit instead of carrying a second copy of the',
            'theme, so that checkout is required. Clone it as a sibling of this',
            'repository and re-run:',
            '',
            f'  cd {parent} && git clone <flight-controller remote> {_FC_REPO_DIR}',
            '',
        ]
        return '\n'.join(out)

    found = _locate_module(repo)
    if found:
        out += [
            'The flight-controller checkout is present, but the file has MOVED. It is',
            'now in:',
        ]
        out += [f'  {d}' for d in found]
        out += [
            '',
            f'Point _REL_PYTOOLS in {os.path.abspath(__file__)} at that directory,',
            'then re-run. (This is the same drift that broke these tools once already.)',
            '',
        ]
    else:
        out += [
            f'The flight-controller checkout at {repo} contains no {_MODULE} anywhere',
            'under tools/ — either it is a partial checkout, or the shared UI toolkit',
            'was removed upstream. In the latter case these two dashboards need their',
            'own copy of the theme; they cannot render without it.',
            '',
        ]
    return '\n'.join(out)


def _locate_module(repo: str) -> list[str]:
    """Directories under <flight-controller>/tools that contain the module.

    Error path only — never runs on a successful import. Exists so that the next
    upstream move reports its own answer instead of another blind hunt.
    """
    hits = []
    for root, dirs, files in os.walk(os.path.join(repo, 'tools')):
        dirs[:] = [d for d in dirs
                   if not d.startswith('.') and d not in ('__pycache__', 'node_modules')]
        if _MODULE in files:
            hits.append(root)
    return hits
