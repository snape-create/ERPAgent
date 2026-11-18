"""沙箱引导：把「Agent 运行真正需要」的文件放进容器。

只上传两块：技能数据（`backend/skills`）与 erpflow 源码。
历史版本会把整个仓库打包（含 `frontend/`、`docs/`、`result/`），首次建图要多传
几千个文件；现在清单固定，指纹也只覆盖这两棵子树。

这个模块只使用稳定 Proxy 暴露的公开能力，因此健康检查重建容器后，
同一个 Agent / CompositeBackend / 工具对象都能继续复用。
"""
import fnmatch
import hashlib
import io
import tarfile

from ..config import BACKEND_ROOT
from ..log_utils import agent_logger

# 沙箱内落点统一在 /workspace/src 下
REMOTE_ROOT = "/workspace/src"

# 上传清单：(沙箱内相对路径, 宿主机目录)
UPLOAD_ENTRIES = (
    ("skills", BACKEND_ROOT / "skills"),
    ("erpflow", BACKEND_ROOT / "packages" / "harness" / "erpflow"),
)

# 源码目录里没必要进容器的产物
SKIP_PATTERNS = (
    "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".ruff_cache",
    "*.png", "*.jpg", "*.jpeg", "*.gif",
)


def _should_skip(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return any(
        fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(part, pattern)
        for pattern in SKIP_PATTERNS
        for part in parts
    )


def _collect_files() -> list[tuple[object, str]]:
    """列出待上传文件（宿主机路径, 沙箱内相对路径）。"""
    files: list[tuple[object, str]] = []
    for arc_root, host_dir in UPLOAD_ENTRIES:
        if not host_dir.is_dir():
            agent_logger.warning(f"Skip missing upload entry: {host_dir}")
            continue
        for file_path in sorted(host_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = str(file_path.relative_to(host_dir)).replace("\\", "/")
            if _should_skip(rel):
                continue
            files.append((file_path, f"{arc_root}/{rel}"))
    return files


def _fingerprint(files: list[tuple[object, str]]) -> str:
    """用「路径 + 大小 + mtime」算指纹，避免每次重启都重传。"""
    digest = hashlib.sha1()
    for file_path, rel in files:
        try:
            stat = file_path.stat()
        except OSError:
            continue
        digest.update(f"{rel}:{stat.st_size}:{int(stat.st_mtime)}".encode())
    return digest.hexdigest()[:16]


def _upload_files(sandbox, files: list[tuple[object, str]], fingerprint: str) -> bool:
    """把文件打成 tar 上传并解包；返回是否真的传了（命中标记则跳过）。"""
    marker_path = "/workspace/.erp-bootstrap-fingerprint"
    current = sandbox.execute(f"cat '{marker_path}' 2>/dev/null")
    if current.exit_code == 0 and current.output.strip() == fingerprint:
        agent_logger.info(
            f"Project already in sandbox (fingerprint {fingerprint}), skip upload"
        )
        return False

    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w:gz") as tar:
        for file_path, rel in files:
            tar.add(str(file_path), arcname=rel)

    tar_stream.seek(0)
    data = tar_stream.read()

    sandbox.execute(f"mkdir -p '{REMOTE_ROOT}'")

    # 仅使用 BaseSandbox 的公开协议上传；这样 SandboxBackendProxy 热替换
    # 底层容器后，项目上传仍能工作，且不会依赖 Docker 私有 _container。
    #
    # 必须落在 /workspace（显式挂载的 tmpfs）而不是 /tmp：
    # 容器以 read_only 启动，Docker 的 put_archive 在只读 rootfs 上会直接
    # 拒绝请求（"container rootfs is marked read-only"），即使 /tmp 本身
    # 是 tmpfs 可写也无法绕过，因为这是 daemon 层的检查。
    archive_path = "/workspace/.erp-agent-project.tar.gz"
    responses = sandbox.upload_files([(archive_path, data)])
    errors = [getattr(item, "error", None) for item in responses]
    if not responses or any(errors):
        raise RuntimeError(f"archive upload failed: {errors or 'no response'}")

    # --no-same-owner/--no-same-permissions 不能省：
    # 容器以 root 运行时 tar 默认按归档还原权限并 chmod，而 /workspace 是
    # 挂载了 nosuid,nodev 的 tmpfs，chmod 会被拒（"Cannot change mode"），
    # tar 因此以 exit 2 结束，尽管文件其实都解压成功了。
    unpack = sandbox.execute(
        f"tar -xzf '{archive_path}' -C '{REMOTE_ROOT}' "
        f"--no-same-owner --no-same-permissions && rm -f '{archive_path}'"
    )
    if unpack.exit_code != 0:
        raise RuntimeError(f"archive extraction failed: {unpack.output}")

    sandbox.execute(f"printf '%s' '{fingerprint}' > '{marker_path}'")
    return True


def _upload_project_to_sandbox(sandbox) -> bool:
    """把 UPLOAD_ENTRIES 同步进沙箱；失败只告警（本地回退仍可工作）。"""
    try:
        files = _collect_files()
        fingerprint = _fingerprint(files)
        uploaded = _upload_files(sandbox, files, fingerprint)

        count_result = sandbox.execute(f"find {REMOTE_ROOT} -type f 2>/dev/null | wc -l")
        size_result = sandbox.execute(f"du -sh {REMOTE_ROOT} 2>/dev/null | cut -f1")
        file_count = count_result.output.strip() if count_result.exit_code == 0 else "?"
        total_size = size_result.output.strip() if size_result.exit_code == 0 else "?"
        agent_logger.info(
            f"Sandbox project files {'uploaded' if uploaded else 'up-to-date'}: "
            f"{REMOTE_ROOT} ({file_count} files, {total_size})"
        )
        return True
    except Exception as e:
        agent_logger.warning(f"Project upload to sandbox failed (non-fatal): {e}")
        return False


def bootstrap_sandbox(sandbox) -> None:
    """填充一个新领取或重建后的沙箱，使其可立即继续 Agent 工作。"""
    setup = sandbox.execute(
        "mkdir -p /workspace/charts /workspace/output /workspace/data "
        "/workspace/python-packages /skills"
    )
    if setup.exit_code != 0:
        raise RuntimeError(f"sandbox directory setup failed: {setup.output}")

    if not _upload_project_to_sandbox(sandbox):
        raise RuntimeError("project bootstrap upload failed")

    skills_copy = sandbox.execute(
        "mkdir -p /skills && "
        f"cp -r {REMOTE_ROOT}/skills/. /skills/ 2>/dev/null || true"
    )
    if skills_copy.exit_code != 0:
        raise RuntimeError(f"core Skills bootstrap failed: {skills_copy.output}")

    agent_logger.info("Sandbox bootstrap complete: project + core Skills restored")
