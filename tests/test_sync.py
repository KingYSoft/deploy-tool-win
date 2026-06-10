from pathlib import Path

from webhook_deployer.sync import sync_tree_preserving


def test_sync_tree_preserving_does_not_overwrite_preserved_files_or_dirs(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    (source / "index.html").write_text("new", encoding="utf-8")
    (source / "config.js").write_text("new-config", encoding="utf-8")
    (source / "uploads").mkdir()
    (source / "uploads" / "new.txt").write_text("new-upload", encoding="utf-8")

    (target / "index.html").write_text("old", encoding="utf-8")
    (target / "config.js").write_text("old-config", encoding="utf-8")
    (target / "stale.txt").write_text("stale", encoding="utf-8")
    (target / "uploads").mkdir()
    (target / "uploads" / "old.txt").write_text("old-upload", encoding="utf-8")

    result = sync_tree_preserving(
        source,
        target,
        preserve_files=["config.js"],
        preserve_dirs=["uploads"],
    )

    assert (target / "index.html").read_text(encoding="utf-8") == "new"
    assert (target / "config.js").read_text(encoding="utf-8") == "old-config"
    assert (target / "uploads" / "old.txt").read_text(encoding="utf-8") == "old-upload"
    assert not (target / "uploads" / "new.txt").exists()
    assert not (target / "stale.txt").exists()
    assert "config.js" in result.skipped_preserved
    assert "uploads" in result.skipped_preserved


def test_sync_tree_preserving_rejects_paths_that_escape_target(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    try:
        sync_tree_preserving(source, target, preserve_files=["../secret.txt"])
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("expected ValueError")

