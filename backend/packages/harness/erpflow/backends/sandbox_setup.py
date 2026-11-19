"""
沙箱创建 + 安全防护 + 多语言运行时初始化

Harness 核心思想：
1. 文件系统只读 (--read-only) + tmpfs 可写区域
2. 内存/CPU 资源限制
3. 网络隔离 (--network none 或受限网络)
4. Linux Capability 全部移除
5. seccomp 系统调用白名单
6. 可扩展多语言运行时（Python / Go / Node.js）

沙箱由 SandboxManager 统一管理，不直接暴露给 Agent。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import docker

from ..config import (
    SANDBOX_CONTAINER,
    SANDBOX_IMAGE,
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_PYTHON_PACKAGES_DIR,
    SANDBOX_UPLOADS_DIR,
    SANDBOX_USER_DATA_ROOT,
    SANDBOX_WORK_DIR,
)
from ..log_utils import sandbox_logger
from .custom_opensandbox import CustomOpenSandbox

# ============================================================
# 沙箱配置数据类
# ============================================================

@dataclass
class SandboxConfig:
    """沙箱创建配置（可扩展）"""

    # --- 基础 ---
    image: str = SANDBOX_IMAGE
    name: str = "erp-sandbox"
    work_dir: str = SANDBOX_WORK_DIR

    # --- 安全防护（默认值来自 config/sandbox_config.toml [sandbox.container]）---
    read_only: bool = SANDBOX_CONTAINER["read_only"]
    memory_limit: str = SANDBOX_CONTAINER["memory_limit"]
    cpu_limit: float = SANDBOX_CONTAINER["cpu_limit"]
    network_mode: str = SANDBOX_CONTAINER["network_mode"]
    tmpfs_size: str = SANDBOX_CONTAINER["tmpfs_size"]
    workspace_size: str = SANDBOX_CONTAINER["workspace_size"]
    skills_size: str = SANDBOX_CONTAINER["skills_size"]
    pydeps_size: str = SANDBOX_CONTAINER["pydeps_size"]
    uploads_size: str = SANDBOX_CONTAINER["uploads_size"]
    outputs_size: str = SANDBOX_CONTAINER["outputs_size"]
    drop_all_caps: bool = SANDBOX_CONTAINER["drop_all_caps"]
    use_seccomp: bool = SANDBOX_CONTAINER["use_seccomp"]
    seccomp_path: str = SANDBOX_CONTAINER["seccomp_path"]
    # 安全容器创建失败时是否允许退回无防护容器；默认 fail closed。
    allow_insecure_fallback: bool = SANDBOX_CONTAINER["allow_insecure_fallback"]

    # --- 多语言运行时 ---
    # 指定需要安装的运行时列表: ["python", "go", "node"]
    runtimes: list[str] = field(default_factory=lambda: list(SANDBOX_CONTAINER["runtimes"]))

    # --- 环境变量注入 ---
    env_vars: dict[str, str] = field(default_factory=dict)

    # --- 宿主机 bind mount：host_path -> container_path ---
    # 目前只挂 uploads/outputs；workspace 是 tmpfs，不持久化。
    volumes: dict[str, str] = field(default_factory=dict)


# ============================================================
# 内置 seccomp 路径
# ============================================================

_BUILTIN_SECCOMP = str(Path(__file__).parent / "seccomp.json")

# 只允许明确安全的网络模式。host / container:<id> 会突破网络隔离，
# 自定义 Docker 网络也不在白名单内，需要时先扩白名单并评审。
_ALLOWED_NETWORK_MODES = frozenset({"none", "bridge"})


def validate_network_mode(network_mode: str) -> str:
    """校验并返回安全的 Docker 网络模式；非法模式直接拒绝。"""
    mode = (network_mode or "none").strip()
    if mode not in _ALLOWED_NETWORK_MODES:
        raise ValueError(
            f"Unsafe sandbox network_mode '{mode}'. "
            f"Allowed: {', '.join(sorted(_ALLOWED_NETWORK_MODES))}"
        )
    return mode


# ============================================================
# 安全沙箱创建（Docker SDK）
# ============================================================

def create_secure_sandbox(config: SandboxConfig | None = None) -> CustomOpenSandbox:
    """
    创建安全加固的 Docker 沙箱容器

    七层安全防护：
    1. --read-only           文件系统只读
    2. --tmpfs /tmp          可写区域限制大小
    3. --memory / --cpus     资源上限
    4. --network none        网络隔离（或受限）
    5. --cap-drop ALL        移除所有 Linux Capability
    6. --security-opt seccomp  系统调用白名单
    7. --pids-limit          进程数上限

    Returns:
        CustomOpenSandbox 实例（已连接到新建容器）
    """
    if config is None:
        config = SandboxConfig()

    network_mode = validate_network_mode(config.network_mode)

    seccomp_path = config.seccomp_path or _BUILTIN_SECCOMP
    seccomp_profile = None
    if config.use_seccomp and Path(seccomp_path).exists():
        with open(seccomp_path) as f:
            seccomp_profile = f.read()
    elif config.use_seccomp:
        sandbox_logger.warning(
            f"seccomp enabled but profile not found: {seccomp_path}; "
            "container will start WITHOUT seccomp"
        )
    else:
        sandbox_logger.warning("seccomp is disabled by configuration")

    if not config.read_only:
        sandbox_logger.warning("sandbox read_only is disabled by configuration")
    if not config.drop_all_caps:
        sandbox_logger.warning("sandbox drop_all_caps is disabled by configuration")
    if network_mode == "bridge":
        sandbox_logger.warning(
            "sandbox network_mode=bridge allows network egress; use 'none' for isolation"
        )

    client = docker.from_env()

    # 检查容器是否已存在，存在则先移除
    try:
        existing = client.containers.get(config.name)
        sandbox_logger.info(f"Removing existing container: {config.name}")
        existing.stop(timeout=3)
        existing.remove(force=True)
    except docker.errors.NotFound:
        pass

    # 构建安全参数
    tmpfs = {
        "/tmp": f"rw,noexec,nosuid,size={config.tmpfs_size}",
        # 兼容旧路径；新工具统一使用 /mnt/user-data。
        "/workspace": "rw,noexec,nosuid,size=256m",
        # workspace 是临时的（Q3=A）；uploads/outputs 由 bind mount 覆盖。
        f"{SANDBOX_USER_DATA_ROOT}/workspace": "rw,noexec,nosuid,size=256m",
        # Python 依赖必须放在可执行挂载点：workspace 是 noexec，
        # 从那里加载 C 扩展会 "failed to map segment from shared object"。
        SANDBOX_PYTHON_PACKAGES_DIR: "rw,exec,nosuid,nodev,size=512m",
        # ``--read-only`` 会让根目录下的 /skills 不可写；Skills 同步、
        # 用户 Skills 恢复都必须在独立的可写挂载点完成。
        "/skills": "rw,nosuid,nodev,size=128m",
    }

    # 宿主机 bind mount（uploads/outputs）。Docker SDK 要求 host path 为绝对路径。
    bind_targets = set(config.volumes.values())
    if config.volumes:
        host_config_kwargs = {
            "read_only": config.read_only,
            "mem_limit": config.memory_limit,
            "nano_cpus": int(config.cpu_limit * 1e9),
            "pids_limit": 256,
            "volumes": {
                str(host_path): {"bind": container_path, "mode": "rw"}
                for host_path, container_path in config.volumes.items()
            },
        }
    else:
        host_config_kwargs = {
            "read_only": config.read_only,
            "mem_limit": config.memory_limit,
            "nano_cpus": int(config.cpu_limit * 1e9),
            "pids_limit": 256,
        }

    # 没有 bind mount 时，uploads/outputs 仍是可写 tmpfs（如 warm pool 或
    # user_data_mounts=false）；避免在只读 rootfs 上 mkdir 失败。
    if SANDBOX_UPLOADS_DIR not in bind_targets:
        tmpfs[SANDBOX_UPLOADS_DIR] = (
            f"rw,noexec,nosuid,size={config.uploads_size}"
        )
    if SANDBOX_OUTPUTS_DIR not in bind_targets:
        tmpfs[SANDBOX_OUTPUTS_DIR] = (
            f"rw,noexec,nosuid,size={config.outputs_size}"
        )
    host_config_kwargs["tmpfs"] = tmpfs

    # 网络模式（已通过白名单校验）
    host_config_kwargs["network_mode"] = network_mode

    # Capability 安全
    if config.drop_all_caps:
        host_config_kwargs["cap_drop"] = ["ALL"]
        # 仅添加运行必需的最小 Capability。
        # DAC_OVERRIDE 用于写宿主机 bind mount（宿主 UID 创建的目录，容器 root
        # 不是 owner）。若改为以宿主 UID 运行容器，可移除它。
        host_config_kwargs["cap_add"] = ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"]

    # seccomp 安全策略
    # 内联策略内容而非传路径：Docker Desktop 的 daemon 跑在独立的
    # docker-desktop 发行版里，读不到宿主机 /home 下的文件，传路径会被当成
    # JSON 解析并以 "Decoding seccomp profile failed" 失败，整层防护静默降级。
    security_opts = []
    if seccomp_profile:
        try:
            compact = json.dumps(json.loads(seccomp_profile), separators=(",", ":"))
            security_opts.append(f"seccomp={compact}")
        except json.JSONDecodeError as e:
            sandbox_logger.warning(f"Invalid seccomp profile, skipping: {e}")
    security_opts.append("no-new-privileges:true")
    host_config_kwargs["security_opt"] = security_opts

    # 环境变量
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        # 根文件系统保持只读时，第三方依赖安装到独立可执行挂载点，
        # 而不是 noexec 的 workspace。
        "PIP_TARGET": SANDBOX_PYTHON_PACKAGES_DIR,
        "PYTHONPATH": SANDBOX_PYTHON_PACKAGES_DIR,
    }
    environment.update(config.env_vars)

    try:
        sandbox_logger.info(
            f"Creating secure sandbox: {config.name} "
            f"(image={config.image}, memory={config.memory_limit}, "
            f"cpu={config.cpu_limit}, network={network_mode})"
        )

        container = client.containers.run(
            image=config.image,
            name=config.name,
            command="sleep infinity",
            detach=True,
            working_dir=config.work_dir,
            environment=environment,
            **host_config_kwargs,
        )

        sandbox_logger.info(
            f"Secure sandbox created: {config.name} ({container.id[:12]})"
        )

    except Exception as e:
        # 安全创建失败时默认 fail closed，绝不能静默降级成无防护容器。
        sandbox_logger.error(
            f"Secure sandbox creation failed for {config.name}: {e}"
        )
        try:
            existing = client.containers.get(config.name)
            existing.stop(timeout=3)
            existing.remove(force=True)
        except docker.errors.NotFound:
            pass

        if not config.allow_insecure_fallback:
            client.close()
            raise RuntimeError(
                f"Refusing insecure sandbox fallback for {config.name}: {e}"
            ) from e

        # 仅开发调试显式开启；即使回退也保留网络白名单设置。
        sandbox_logger.warning(
            "allow_insecure_fallback=true: creating sandbox WITHOUT read-only "
            "rootfs, memory/CPU/pids limits, capability drop, or seccomp"
        )
        container = client.containers.run(
            image=config.image,
            name=config.name,
            command="sleep infinity",
            detach=True,
            working_dir=config.work_dir,
            environment=environment,
            volumes={
                str(host_path): {"bind": container_path, "mode": "rw"}
                for host_path, container_path in config.volumes.items()
            } or None,
            network_mode=network_mode,
        )
        sandbox_logger.info(
            f"Basic sandbox created (INSECURE fallback): "
            f"{config.name} ({container.id[:12]})"
        )

    client.close()

    # 创建 CustomOpenSandbox 实例连接到新容器
    sandbox = CustomOpenSandbox(container_name=config.name)

    # 初始化运行时环境
    _init_runtimes(sandbox, config.runtimes)

    return sandbox


# ============================================================
# 多语言运行时初始化
# ============================================================

def _init_runtimes(sandbox: CustomOpenSandbox, runtimes: list[str]):
    """
    根据配置初始化多语言运行时

    支持的运行时：
    - python: 预装在 python:3.11-slim 镜像中
    - go:     通过二进制包安装（轻量）
    - node:   通过 NodeSource 安装
    - java:   通过 OpenJDK 安装
    - rust:   通过 rustup 安装
    """
    for runtime in runtimes:
        try:
            if runtime == "python":
                _init_python_runtime(sandbox)
            elif runtime == "go":
                _init_go_runtime(sandbox)
            elif runtime == "node":
                _init_node_runtime(sandbox)
            elif runtime == "java":
                _init_java_runtime(sandbox)
            elif runtime == "rust":
                _init_rust_runtime(sandbox)
            else:
                sandbox_logger.warning(f"Unknown runtime: {runtime}")
        except Exception as e:
            sandbox_logger.error(f"Failed to init runtime '{runtime}': {e}")


def _init_python_runtime(sandbox: CustomOpenSandbox):
    """初始化 Python 运行时（预装常用包）"""
    resp = sandbox.execute("python3 --version")
    if resp.exit_code == 0:
        sandbox_logger.info(f"Python runtime: {resp.output.strip()}")
    else:
        sandbox_logger.warning("Python not available in sandbox")

    # 镜像已预装就跳过；只有缺失时才尝试安装（network none 时安装会失败）。
    check = sandbox.execute(
        "python3 -c 'import matplotlib, numpy, pandas' 2>/dev/null"
    )
    if check.exit_code == 0:
        sandbox_logger.info("Python packages already available in image")
        return

    resp = sandbox.execute(
        f"mkdir -p {SANDBOX_PYTHON_PACKAGES_DIR} && "
        "python3 -m pip install --no-cache-dir "
        f"--target {SANDBOX_PYTHON_PACKAGES_DIR} "
        "matplotlib pandas numpy -q 2>&1 | tail -5"
    )
    if resp.exit_code == 0:
        sandbox_logger.info("Python packages installed: matplotlib, pandas, numpy")
    else:
        sandbox_logger.warning(
            "Python packages missing and runtime install failed "
            f"(preinstall them in the sandbox image): {resp.output[-300:]}"
        )


def _init_go_runtime(sandbox: CustomOpenSandbox):
    """安装 Go 运行时"""
    resp = sandbox.execute("go version 2>/dev/null || echo NOT_INSTALLED")
    if "NOT_INSTALLED" not in resp.output:
        sandbox_logger.info(f"Go runtime: {resp.output.strip()}")
        return

    sandbox_logger.info("Installing Go runtime...")
    resp = sandbox.execute(
        "wget -q https://go.dev/dl/go1.22.4.linux-amd64.tar.gz -O /tmp/go.tar.gz "
        "&& tar -C /usr/local -xzf /tmp/go.tar.gz "
        "&& rm /tmp/go.tar.gz "
        "&& export PATH=$PATH:/usr/local/go/bin "
        "&& go version",
        timeout=120,
    )
    if resp.exit_code == 0:
        sandbox_logger.info(f"Go installed: {resp.output.strip()}")
    else:
        sandbox_logger.warning(f"Go install failed: {resp.output}")


def _init_node_runtime(sandbox: CustomOpenSandbox):
    """安装 Node.js 运行时"""
    resp = sandbox.execute("node --version 2>/dev/null || echo NOT_INSTALLED")
    if "NOT_INSTALLED" not in resp.output:
        sandbox_logger.info(f"Node runtime: {resp.output.strip()}")
        return

    sandbox_logger.info("Installing Node.js runtime...")
    resp = sandbox.execute(
        "apt-get update -qq && apt-get install -y -qq nodejs npm 2>&1 | tail -3 "
        "&& node --version",
        timeout=120,
    )
    if resp.exit_code == 0:
        sandbox_logger.info(f"Node installed: {resp.output.strip()}")
    else:
        sandbox_logger.warning(f"Node install failed: {resp.output}")


def _init_java_runtime(sandbox: CustomOpenSandbox):
    """安装 Java (OpenJDK) 运行时"""
    resp = sandbox.execute("java --version 2>/dev/null || echo NOT_INSTALLED")
    if "NOT_INSTALLED" not in resp.output:
        sandbox_logger.info(f"Java runtime: {resp.output.strip()}")
        return

    sandbox_logger.info("Installing Java runtime...")
    resp = sandbox.execute(
        "apt-get update -qq && apt-get install -y -qq default-jdk-headless 2>&1 | tail -3 "
        "&& java --version",
        timeout=180,
    )
    if resp.exit_code == 0:
        sandbox_logger.info(f"Java installed: {resp.output.strip()}")


def _init_rust_runtime(sandbox: CustomOpenSandbox):
    """安装 Rust 运行时"""
    resp = sandbox.execute("rustc --version 2>/dev/null || echo NOT_INSTALLED")
    if "NOT_INSTALLED" not in resp.output:
        sandbox_logger.info(f"Rust runtime: {resp.output.strip()}")
        return

    sandbox_logger.info("Installing Rust runtime...")
    resp = sandbox.execute(
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y 2>&1 | tail -5 "
        "&& source $HOME/.cargo/env && rustc --version",
        timeout=180,
    )
    if resp.exit_code == 0:
        sandbox_logger.info(f"Rust installed: {resp.output.strip()}")
