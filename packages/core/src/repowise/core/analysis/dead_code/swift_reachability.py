"""Name-granular reachability for Swift.

Swift imports name a module, never a file. Files of one module see each other
with no import at all, and ``import DesignSystem`` from another package
resolves to a single file of that target. So ``in_degree == 0`` on a ``.swift``
file is mostly a property of the language: on IceCubesApp none of 30 judged
"unreachable" files was dead.

A Swift file is reachable here when one of these holds:

* a symbol it declares carries a use edge from another file (``TagRowView(tag:)``
  lands a ``calls`` edge on the type, not on its file);
* a type it declares is written in another Swift file (``[StatusHistory]``,
  ``catch let error as ServerError``), which is how Swift names a type from
  anywhere in its module or an importing one, and which the graph carries no
  edge for;
* it extends a type (``extension Color``, ``extension StatusEditor.EditorStore``)
  that is declared outside the repository or in a file that is itself
  reachable. An extension is part of the type it extends; whether its members
  are used is a member-level question.

Like the C# pass it only rescues, so every approximation errs toward
"reachable": a comment mention does not count, but a same-named type anywhere
in the repository does. Ceiling: a principal class named only by an
``Info.plist`` or a storyboard, and a file listed only in ``project.pbxproj``,
are not seen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.ids import file_path_of, is_external

from ...entry_candidacy import is_reachability_root
from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES
from .csharp_reachability import _code_only
from .file_reachability import has_dependency_importer

SWIFT_SUFFIX = ".swift"

#: Top-level declarations: ``struct``/``class``/``enum``/``actor`` are emitted
#: as ``class``, a ``protocol`` as ``interface``, an ``extension`` as ``impl``.
_TYPE_KINDS: frozenset[str] = frozenset({"class", "interface"})
_EXTENSION_KIND = "impl"

_IDENT = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")


def defined_symbol_used_elsewhere(graph: Any, path: str) -> bool:
    """Whether a symbol *path* defines carries a use edge from another file.

    A use from the file itself does not count, or every file would reach
    itself.
    """
    for _path, symbol, edge in graph.out_edges(path, data=True):
        if edge.get("edge_type") != "defines":
            continue
        for user, _symbol, use in graph.in_edges(symbol, data=True):
            if (
                use.get("edge_type") in REACHABILITY_USE_EDGE_TYPES
                and not is_external(user)
                and file_path_of(user) != path
            ):
                return True
    return False


def _top_level(graph: Any, path: str) -> Iterable[dict]:
    for _path, symbol, edge in graph.out_edges(path, data=True):
        if edge.get("edge_type") == "defines":
            data = graph.nodes.get(symbol, {})
            if not data.get("parent_name") and data.get("name"):
                yield data


def _swift_files(graph: Any) -> list[str]:
    return [
        node
        for node, data in graph.nodes(data=True)
        if isinstance(node, str)
        and node.endswith(SWIFT_SUFFIX)
        and data.get("node_type", "file") == "file"
    ]


def build_swift_named_files(graph: Any, source_map: Mapping[str, bytes]) -> frozenset[str]:
    """The Swift files a use edge, a type name or an extended type reaches.

    Only files with no importer are judged; the others are reachable anyway.
    One pass over the Swift source collects the identifiers some candidate's
    types are called. With no source at all, every candidate is returned:
    unchecked is reachable, the answer ``ReachabilityRescues`` gives a caller
    that cannot check.
    """
    candidates = [
        path
        for path in _swift_files(graph)
        if not is_reachability_root(graph.nodes[path])
        and not has_dependency_importer(graph, path)
    ]
    if not source_map:
        return frozenset(candidates)
    reached = {path for path in candidates if defined_symbol_used_elsewhere(graph, path)}
    declares: dict[str, set[str]] = {}  # type name -> files declaring it
    names_of: dict[str, set[bytes]] = {}  # file -> names it declares or extends
    extends: dict[str, set[str]] = {}  # file -> types it extends
    for path in _swift_files(graph):
        for data in _top_level(graph, path):
            name = data["name"]
            if data.get("kind") in _TYPE_KINDS:
                declares.setdefault(name, set()).add(path)
                names_of.setdefault(path, set()).add(name.encode("ascii", "ignore"))
            elif data.get("kind") == _EXTENSION_KIND:
                extends.setdefault(path, set()).add(name)
                names_of.setdefault(path, set()).add(name.encode("ascii", "ignore"))

    wanted: dict[bytes, set[str]] = {}
    for path in candidates:
        if path in reached:
            continue
        for data in _top_level(graph, path):
            if data.get("kind") in _TYPE_KINDS:
                wanted.setdefault(data["name"].encode("ascii", "ignore"), set()).add(path)
    if wanted:
        for path, blob in source_map.items():
            if not path.endswith(SWIFT_SUFFIX):
                continue
            own = names_of.get(path, set())
            for token in set(_IDENT.findall(_code_only(blob))) & wanted.keys():
                # A file declaring or extending the name is not using it.
                if token not in own:
                    reached.update(wanted[token])

    unreached = set(candidates) - reached
    for path in candidates:
        if path in reached:
            continue
        for name in extends.get(path, ()):
            owners = declares.get(name, set())
            if not owners or any(owner not in unreached for owner in owners):
                reached.add(path)
                break
    return frozenset(reached)
