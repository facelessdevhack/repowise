"""C# classes a plugin host or container finds by type are not dead.

Shapes from jellyfin: ``ApplicationHost.GetExports<IScheduledTask>()``
instantiates every loaded type assignable to the interface, so no file names
``DeleteLogFileTask``. Its metadata providers implement a generic interface
(``IRemoteMetadataProvider<Episode, EpisodeInfo>``) whose name also has generic
and plain declarations, which the heritage resolver leaves unresolved. Plus the
Scrutor, reflection-loop and MEF spellings, and the controls that keep the
rescue from hiding a class nothing discovers.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "Model/IScheduledTask.cs": "namespace Model;\npublic interface IScheduledTask { void Run(); }\n",
    "Model/IMetadataProvider.cs": (
        "namespace Model;\n"
        "public interface IMetadataProvider { string Name { get; } }\n"
        "public interface IMetadataProvider<TItem> : IMetadataProvider {}\n"
    ),
    "Model/IRemoteMetadataProvider.cs": (
        "namespace Model;\n"
        "public interface IRemoteMetadataProvider : IMetadataProvider {}\n"
        "public interface IRemoteMetadataProvider<TItem, TInfo> : IMetadataProvider<TItem>, "
        "IRemoteMetadataProvider {}\n"
    ),
    "Model/IUnscanned.cs": "namespace Model;\npublic interface IUnscanned { void Go(); }\n",
    "Host/ApplicationHost.cs": (
        "namespace Host;\nusing Model;\n"
        "public class ApplicationHost {\n"
        "    public void Start() {\n"
        "        var tasks = GetExports<IScheduledTask>(false);\n"
        "        var providers = GetExports<Model.IMetadataProvider>();\n"
        "    }\n"
        "    public T[] GetExports<T>(bool manage = true) => new T[0];\n"
        "}\n"
    ),
    "Tasks/DeleteLogFileTask.cs": (
        "namespace Tasks;\nusing Model;\n"
        "public class DeleteLogFileTask : IScheduledTask { public void Run() {} }\n"
    ),
    "Tasks/BaseTask.cs": (
        "namespace Tasks;\nusing Model;\n"
        "public abstract class BaseTask : IScheduledTask { public abstract void Run(); }\n"
    ),
    "Tasks/OptimizeTask.cs": (
        "namespace Tasks;\npublic class OptimizeTask : BaseTask { public override void Run() {} }\n"
    ),
    "Providers/TmdbEpisodeProvider.cs": (
        "namespace Providers;\nusing Model;\n"
        "public class TmdbEpisodeProvider : IRemoteMetadataProvider<Episode, EpisodeInfo>\n"
        "{\n    public string Name => \"Tmdb\";\n}\n"
    ),
    "Plugins/Scan.cs": (
        "namespace Plugins;\n"
        "public interface IHandler {}\npublic interface IPlugin {}\n"
        "public static class Scan {\n"
        "    public static void Wire(object services, System.Type[] types) {\n"
        "        services.Scan(s => s.AddClasses(c => c.AssignableTo<IHandler>()));\n"
        "        foreach (var t in types) { if (typeof(IPlugin).IsAssignableFrom(t)) {} }\n"
        "    }\n"
        "}\n"
    ),
    "Plugins/OrderHandler.cs": "namespace Plugins;\npublic class OrderHandler : IHandler {}\n",
    "Plugins/AudioPlugin.cs": "namespace Plugins;\npublic class AudioPlugin : IPlugin {}\n",
    "Plugins/ExportedPart.cs": (
        "namespace Plugins;\n[System.ComponentModel.Composition.Export(typeof(object))]\n"
        "public class ExportedPart {}\n"
    ),
    "Unused/OrphanTask.cs": (
        "namespace Unused;\nusing Model;\npublic class OrphanTask : IUnscanned { public void Go() {} }\n"
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


def _flagged(report) -> set[str]:
    return {
        f.symbol_name if f.kind is DeadCodeKind.UNUSED_EXPORT else Path(f.file_path).name
        for f in report.findings
        if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
    }


def test_classes_the_runtime_discovers_by_type_are_not_flagged(tmp_path: Path) -> None:
    flagged = _flagged(_report(tmp_path))
    discovered = {
        "DeleteLogFileTask",  # GetExports<IScheduledTask>
        "OptimizeTask",  # through an abstract base
        "TmdbEpisodeProvider",  # through an unresolved generic interface chain
        "OrderHandler",  # Scrutor AssignableTo<IHandler>()
        "AudioPlugin",  # typeof(IPlugin).IsAssignableFrom
        "ExportedPart",  # MEF [Export]
    }
    files = {f"{name}.cs" for name in discovered}
    assert flagged.isdisjoint(discovered | files), flagged & (discovered | files)


def test_a_class_no_scanning_call_reaches_is_still_flagged(tmp_path: Path) -> None:
    """``IUnscanned`` is never passed to a scanning call, so its implementation stays dead."""
    flagged = _flagged(_report(tmp_path))
    assert "OrphanTask" in flagged or "OrphanTask.cs" in flagged
