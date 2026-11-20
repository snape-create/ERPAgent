"""路径安全工具：拒绝路径穿越与逃逸出下载目录。

所有来自模型/请求的文件名都不可信：可能包含 ``../``、绝对路径、Windows
盘符或符号链接，最终把读写落到宿主机任意位置。本模块提供两个纯函数：

- :func:`safe_filename` —— 只允许"单纯的文件名"（单段、无分隔符、非绝对）
- :func:`safe_relative_path` —— 允许 ``a/b.png`` 这类相对子路径，但拒绝
  绝对路径、``..``、Windows 分隔符与盘符
- :func:`resolve_under` / :func:`resolve_relative_under` —— 在 ``base`` 下解析
  路径，并确认结果仍在 ``base`` 内（``resolve()`` 会展开符号链接，因此也能
  挡住链接逃逸）
"""
from pathlib import Path, PurePosixPath, PureWindowsPath

__all__ = [
    "UnsafePathError",
    "resolve_relative_under",
    "resolve_under",
    "safe_filename",
    "safe_relative_path",
]


class UnsafePathError(ValueError):
    """文件名或路径不安全（穿越、绝对路径、盘符、NUL 等）。"""


def safe_filename(filename: str) -> str:
    """校验并返回一个安全的单段文件名。

    Args:
        filename: 待校验的文件名（不含目录）。

    Returns:
        原样返回的文件名。

    Raises:
        UnsafePathError: 输入为 None/非字符串、空串、``.``/``..``、含 NUL、
            含 ``/`` 或 ``\\``、POSIX 绝对路径、Windows 绝对路径或盘符前缀。
    """
    if not isinstance(filename, str):
        raise UnsafePathError("文件名必须是字符串")
    if not filename:
        raise UnsafePathError("文件名不能为空")
    if "\x00" in filename:
        raise UnsafePathError("文件名不能包含 NUL 字符")
    if filename in (".", ".."):
        raise UnsafePathError(f"文件名不能是 {filename!r}")
    if "/" in filename or "\\" in filename:
        raise UnsafePathError("文件名不能包含路径分隔符")
    if PurePosixPath(filename).is_absolute():
        raise UnsafePathError("文件名不能是绝对路径")
    if PureWindowsPath(filename).is_absolute() or PureWindowsPath(filename).drive:
        raise UnsafePathError("文件名不能是 Windows 绝对路径或盘符路径")
    return filename


def safe_relative_path(path: str) -> str:
    """校验并返回安全的 POSIX 相对子路径，例如 ``charts/a.png``。

    与 :func:`safe_filename` 的区别是允许 ``/`` 分段，但每一段都必须
    是普通名称；绝对路径、``..``、反斜杠、NUL 和 Windows 盘符全部拒绝。
    """
    if not isinstance(path, str):
        raise UnsafePathError("路径必须是字符串")
    if not path:
        raise UnsafePathError("路径不能为空")
    if "\x00" in path:
        raise UnsafePathError("路径不能包含 NUL 字符")
    if "\\" in path:
        raise UnsafePathError("路径不能包含 Windows 路径分隔符")
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise UnsafePathError("路径不能是绝对路径")
    if PureWindowsPath(path).is_absolute() or PureWindowsPath(path).drive:
        raise UnsafePathError("路径不能是 Windows 绝对路径或盘符路径")
    raw_parts = path.split("/")
    parts = pure.parts
    if (
        not parts
        or any(part in ("", ".", "..") for part in raw_parts)
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise UnsafePathError(f"路径包含非法片段: {path!r}")
    return pure.as_posix()


def resolve_under(base: Path, filename: str) -> Path:
    """把 ``filename`` 解析为 ``base`` 下的绝对路径。

    ``resolve()`` 会展开 ``base`` 与目标路径中的符号链接，随后用
    ``relative_to`` 确认结果仍在 ``base`` 内；任何越界（包括符号链接
    指向目录外）都会抛 :class:`UnsafePathError`。

    Args:
        base: 允许写入/读取的根目录。
        filename: 单段文件名。

    Returns:
        位于 ``base`` 内的绝对路径。

    Raises:
        UnsafePathError: 文件名不合法，或解析结果逃逸出 ``base``。
    """
    safe_name = safe_filename(filename)
    base_resolved = Path(base).resolve()
    target = (base_resolved / safe_name).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"路径越界，不在 {base_resolved} 内: {safe_name}") from exc
    return target


def resolve_relative_under(base: Path, relative_path: str) -> Path:
    """把安全的相对子路径解析到 ``base`` 下，并阻止符号链接逃逸。"""
    safe_path = safe_relative_path(relative_path)
    base_resolved = Path(base).resolve()
    target = (base_resolved / safe_path).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"路径越界，不在 {base_resolved} 内: {safe_path}") from exc
    return target
