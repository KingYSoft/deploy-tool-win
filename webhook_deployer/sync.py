from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyncResult:
    copied: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped_preserved: list[str] = field(default_factory=list)


def sync_tree_preserving(
    source: str | Path,
    target: str | Path,
    *,
    preserve_files: list[str] | None = None,
    preserve_dirs: list[str] | None = None,
) -> SyncResult:
    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    if not source_path.is_dir():
        raise ValueError(f"source directory does not exist: {source_path}")
    target_path.mkdir(parents=True, exist_ok=True)

    # 保护路径必须是发布目录内的相对路径，避免误删或覆盖生产配置。
    file_preserves = [_safe_relative(path) for path in preserve_files or []]
    dir_preserves = [_safe_relative(path) for path in preserve_dirs or []]
    result = SyncResult()

    # 先删除目标目录中源目录不存在的旧文件；按路径深度倒序可以先删子项再删空目录。
    for existing in sorted(target_path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        relative = existing.relative_to(target_path)
        if _is_preserved(relative, file_preserves, dir_preserves):
            _record_skip(result, relative)
            continue
        source_equivalent = source_path / relative
        if not source_equivalent.exists():
            if existing.is_dir():
                existing.rmdir()
            else:
                existing.unlink()
            result.deleted.append(_to_posix(relative))

    # 再复制新构建产物；保护路径即使在源目录存在也不会覆盖目标目录内容。
    for item in source_path.rglob("*"):
        relative = item.relative_to(source_path)
        if _is_preserved(relative, file_preserves, dir_preserves):
            _record_skip(result, relative)
            continue

        destination = target_path / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        result.copied.append(_to_posix(relative))

    result.skipped_preserved = sorted(set(result.skipped_preserved))
    return result


def _safe_relative(raw: str) -> Path:
    # 拒绝绝对路径和 ..，防止配置错误影响发布目录之外的文件。
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"preserved path is outside publish target: {raw}")
    return path


def _is_preserved(relative: Path, files: list[Path], dirs: list[Path]) -> bool:
    # 文件精确匹配；目录保护会覆盖目录本身及其所有子路径。
    if relative in files:
        return True
    return any(relative == directory or directory in relative.parents for directory in dirs)


def _record_skip(result: SyncResult, relative: Path) -> None:
    result.skipped_preserved.append(_to_posix(relative))


def _to_posix(path: Path) -> str:
    return path.as_posix()
