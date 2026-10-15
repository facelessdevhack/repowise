"""Swift file reachability: a file is live when another file uses, names or extends its types.

Shapes from IceCubesApp, where none of 30 judged "unreachable" files was dead:
a view constructed from another package (``TagRowView(tag:)``), a model named
only in a type annotation (``[StatusHistory]``), an ``extension Color`` of a
platform type and an extension of a live in-repo type. Plus the controls that
keep the rescue from hiding a file nothing uses.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.swift_reachability import build_swift_named_files
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_DS = "Packages/DesignSystem/Sources/DesignSystem"
_MODELS = "Packages/Models/Sources/Models"
_ACCOUNT = "Packages/Account/Sources/Account"


def _package(name: str) -> str:
    return (
        "// swift-tools-version:5.9\nimport PackageDescription\n\n"
        f'let package = Package(name: "{name}", targets: [.target(name: "{name}")])\n'
    )


_FILES = {
    "Packages/DesignSystem/Package.swift": _package("DesignSystem"),
    "Packages/Models/Package.swift": _package("Models"),
    "Packages/Account/Package.swift": _package("Account"),
    f"{_DS}/Views/TagRowView.swift": (
        "import SwiftUI\n\npublic struct TagRowView: View {\n"
        "  let tag: String\n  public init(tag: String) { self.tag = tag }\n"
        "  public var body: some View { Text(tag) }\n}\n"
    ),
    f"{_DS}/Resources/Colors.swift": (
        "import SwiftUI\n\nextension Color {\n  public static let brand = Color(.blue)\n}\n"
    ),
    f"{_MODELS}/StatusHistory.swift": (
        "import Foundation\n\npublic struct StatusHistory: Decodable {\n"
        "  public let content: String\n}\n\nextension StatusHistory: Sendable {}\n"
    ),
    f"{_MODELS}/Editor/EditorStore.swift": (
        "import Foundation\n\npublic enum StatusEditor {\n"
        "  public final class EditorStore {}\n}\n"
    ),
    f"{_MODELS}/Editor/EditorMode.swift": (
        "import Foundation\n\nextension StatusEditor.EditorStore {\n"
        "  public enum Mode { case new, edit }\n}\n"
    ),
    f"{_MODELS}/PartialAccountWithAvatar.swift": (
        "import Foundation\n\n// Not named by StatusHistory or TagRowView.\n"
        "public struct PartialAccountWithAvatar: Codable {\n  public let id: String\n}\n"
    ),
    f"{_MODELS}/Legacy.swift": (
        "import Foundation\n\npublic struct LegacyThing {}\n\n"
        "extension LegacyThing {\n  func old() {}\n}\n"
    ),
    f"{_ACCOUNT}/Tags/FollowedTagsListView.swift": (
        "import SwiftUI\nimport DesignSystem\nimport Models\n\n"
        "public struct FollowedTagsListView: View {\n"
        "  @State private var history: [StatusHistory]?\n"
        "  let store = StatusEditor.EditorStore()\n"
        "  public var body: some View {\n    TagRowView(tag: \"swift\")\n  }\n}\n"
    ),
}


def _report(root: Path):
    source_map = {rel: text.encode() for rel, text in _FILES.items()}
    for rel, source in source_map.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    for fi in FileTraverser(root).traverse():
        builder.add_file(parser.parse_file(fi, source_map[fi.path]))
    builder.set_source_map(source_map)
    graph = builder.build()
    analyzer = DeadCodeAnalyzer(
        graph,
        git_meta_map={},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
    )
    return analyzer.analyze({"min_confidence": 0.0})


def _unreachable(report) -> set[str]:
    return {
        Path(f.file_path).name
        for f in report.findings
        if f.kind is DeadCodeKind.UNREACHABLE_FILE
    }


def test_files_used_named_or_extended_from_elsewhere_are_reachable(tmp_path: Path) -> None:
    unreachable = _unreachable(_report(tmp_path))
    assert unreachable.isdisjoint(
        {
            "TagRowView.swift",  # constructed from another package
            "StatusHistory.swift",  # named in a type annotation
            "Colors.swift",  # extends a platform type
            "EditorMode.swift",  # extends a type declared in a live file
            "EditorStore.swift",
        }
    ), unreachable


def test_a_type_nothing_names_is_still_unreachable(tmp_path: Path) -> None:
    """A comment mention is not a use, and an extension in the type's own file is not one either."""
    unreachable = _unreachable(_report(tmp_path))
    assert {"PartialAccountWithAvatar.swift", "Legacy.swift"} <= unreachable


def test_no_source_means_unchecked_and_reachable() -> None:
    graph = nx.DiGraph()
    graph.add_node("A.swift", node_type="file", language="swift")
    assert build_swift_named_files(graph, {}) == frozenset({"A.swift"})


def _graph(decls: dict[str, list[tuple[str, str]]]) -> nx.DiGraph:
    """Files with top-level ``(name, kind)`` declarations and no edge between them."""
    graph = nx.DiGraph()
    for path, symbols in decls.items():
        graph.add_node(path, node_type="file", language="swift")
        for name, kind in symbols:
            sym = f"{path}::{name}#{kind}"
            graph.add_node(sym, node_type="symbol", name=name, kind=kind, file_path=path)
            graph.add_edge(path, sym, edge_type="defines")
    return graph


def test_name_and_extension_rules_without_any_edge() -> None:
    graph = _graph(
        {
            "Models/StatusHistory.swift": [("StatusHistory", "class")],
            "Views/HistoryView.swift": [("HistoryView", "class")],
            "Views/Colors.swift": [("Color", "impl")],
            "Models/Dead.swift": [("Dead", "class")],
            "Models/Dead+Ext.swift": [("Dead", "impl")],
            "Models/Store.swift": [("Store", "class")],
            "Models/Store+Mode.swift": [("Store", "impl")],
        }
    )
    source = {
        "Models/StatusHistory.swift": b"public struct StatusHistory {}\n",
        "Views/HistoryView.swift": b"struct HistoryView { var h: [StatusHistory]; let s = Store() }\n",
        "Views/Colors.swift": b"extension Color { static let brand = Color.blue }\n",
        "Models/Dead.swift": b"struct Dead {}\n",
        "Models/Dead+Ext.swift": b"extension Dead { func f() {} } // Dead\n",
        "Models/Store.swift": b"final class Store {}\n",
        "Models/Store+Mode.swift": b"extension Store { enum Mode {} }\n",
    }
    named = build_swift_named_files(graph, source)
    assert {
        "Models/StatusHistory.swift",  # named in a type annotation
        "Views/Colors.swift",  # extends a type the repo does not declare
        "Models/Store.swift",
        "Models/Store+Mode.swift",  # extends a type declared in a reachable file
    } <= named
    # Named only by its own extension (and a comment); the extension extends a dead type.
    assert not {"Models/Dead.swift", "Models/Dead+Ext.swift", "Views/HistoryView.swift"} & named
