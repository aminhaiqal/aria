#!/usr/bin/env python3
"""Fail closed when ARIA's production supply-chain controls regress."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")
PIN_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?)"
    r"==(?P<version>[^\s;]+)"
    r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)"
)

HARDENED_APP_SERVICES = {
    "api",
    "backup",
    "beat",
    "browser-worker",
    "migrate",
    "ocr-worker",
    "worker",
}
ARTIFACT_READ_ONLY = {"api", "backup", "beat", "migrate"}
ARTIFACT_WRITERS = {"browser-worker", "ocr-worker", "worker"}
PRODUCTION_ENVIRONMENT = {
    "ARIA_DEBUG": "false",
    "ARIA_ENFORCE_OPERATOR_ROLES": "true",
    "ARIA_REQUIRE_SEPARATE_PUBLISHER": "true",
    "ARIA_TRUST_X_FORWARDED_PROTO": "true",
    "ARIA_SSL_REDIRECT": "true",
    "ARIA_SECURE_COOKIES": "true",
    "ARIA_SECURE_HSTS_SECONDS": "3600",
    "ARIA_OBJECT_STORAGE_BACKEND": "s3",
    "ARIA_BACKUP_UPLOAD_TO_R2": "true",
}


class SupplyChainError(RuntimeError):
    """Raised when an immutable-build or runtime-isolation invariant is absent."""


def _logical_requirements(text: str) -> list[str]:
    requirements: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip() if pending else line
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        requirements.append(pending)
        pending = ""
    if pending:
        raise SupplyChainError("requirement lock ends with an incomplete continuation")
    return requirements


def validate_lock(path: Path) -> None:
    requirements = _logical_requirements(path.read_text(encoding="utf-8"))
    if not requirements:
        raise SupplyChainError(f"{path.name} contains no requirements")

    seen: set[str] = set()
    for requirement in requirements:
        match = PIN_PATTERN.fullmatch(requirement)
        if not match:
            raise SupplyChainError(
                f"{path.name} has a non-exact or unhashed requirement: {requirement[:100]}"
            )
        canonical_name = re.sub(r"[-_.]+", "-", match.group("name").lower())
        if canonical_name in seen:
            raise SupplyChainError(f"{path.name} repeats requirement {canonical_name}")
        seen.add(canonical_name)


def validate_dockerfile(path: Path) -> None:
    stages: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(
            r"^\s*FROM(?:\s+--[^\s]+)*\s+(?P<image>[^\s]+)(?:\s+AS\s+(?P<stage>[^\s]+))?",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        image = match.group("image")
        if image.lower() not in stages and not DIGEST_PATTERN.search(image):
            raise SupplyChainError(
                f"{path.name}:{line_number} external image is not pinned by digest: {image}"
            )
        if stage := match.group("stage"):
            stages.add(stage.lower())

    dockerfile = path.read_text(encoding="utf-8")
    for lock_name in (
        "requirements.lock",
        "requirements-browser.lock",
        "requirements-dev.lock",
    ):
        install = f"pip install --require-hashes --requirement {lock_name}"
        if install not in dockerfile:
            raise SupplyChainError(f"Dockerfile does not enforce hashes for {lock_name}")


def validate_compose_images(path: Path) -> None:
    images = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"^\s+image:\s*[\"']?(?P<image>[^\s\"']+)", line)
        if match:
            images.append((line_number, match.group("image")))
    if not images:
        raise SupplyChainError(f"{path.name} declares no external images")
    for line_number, image in images:
        if not DIGEST_PATTERN.search(image):
            raise SupplyChainError(
                f"{path.name}:{line_number} image is not pinned by digest: {image}"
            )


def validate_repository(root: Path = ROOT) -> None:
    lock_paths = sorted(root.glob("requirements*.lock"))
    expected_locks = {
        "requirements.lock",
        "requirements-browser.lock",
        "requirements-dev.lock",
    }
    if {path.name for path in lock_paths} != expected_locks:
        raise SupplyChainError("the production, browser, and development locks are all required")
    for lock_path in lock_paths:
        validate_lock(lock_path)
    validate_dockerfile(root / "Dockerfile")
    validate_compose_images(root / "compose.yaml")
    validate_compose_images(root / "compose.production.yaml")
    for compose_name in ("compose.production.yaml", "compose.vps.yaml"):
        if not (root / compose_name).is_file():
            raise SupplyChainError(f"{compose_name} is required")


def _require_positive(service_name: str, service: dict[str, Any], field: str) -> None:
    try:
        positive = float(service.get(field, 0)) > 0
    except (TypeError, ValueError):
        positive = False
    if not positive:
        raise SupplyChainError(f"{service_name} has no positive {field}")


def _volume(service: dict[str, Any], target: str) -> dict[str, Any] | None:
    return next(
        (volume for volume in service.get("volumes", []) if volume.get("target") == target),
        None,
    )


def _validate_local_port(service_name: str, service: dict[str, Any]) -> None:
    ports = service.get("ports", [])
    if not ports or any(port.get("host_ip") != "127.0.0.1" for port in ports):
        raise SupplyChainError(f"{service_name} must publish only on 127.0.0.1")


def validate_runtime_config(config: dict[str, Any]) -> None:
    services = config.get("services", {})
    missing = HARDENED_APP_SERVICES - services.keys()
    if missing:
        raise SupplyChainError(f"production config omits services: {', '.join(sorted(missing))}")

    for service_name in sorted(HARDENED_APP_SERVICES):
        service = services[service_name]
        if service.get("user") != "aria":
            raise SupplyChainError(f"{service_name} must run as the aria user")
        if service.get("read_only") is not True:
            raise SupplyChainError(f"{service_name} must use a read-only root filesystem")
        if "ALL" not in service.get("cap_drop", []):
            raise SupplyChainError(f"{service_name} must drop all Linux capabilities")
        if "no-new-privileges:true" not in service.get("security_opt", []):
            raise SupplyChainError(f"{service_name} must disable privilege escalation")
        for field in ("pids_limit", "mem_limit", "cpus"):
            _require_positive(service_name, service, field)
        environment = service.get("environment", {})
        for name, expected in PRODUCTION_ENVIRONMENT.items():
            if environment.get(name) != expected:
                raise SupplyChainError(
                    f"{service_name} does not enforce production setting {name}={expected}"
                )
        if "ARIA_RELEASE_REVISION" not in environment:
            raise SupplyChainError(f"{service_name} has no immutable release identity input")
        app_mount = _volume(service, "/app")
        if not app_mount or app_mount.get("read_only") is not True:
            raise SupplyChainError(f"{service_name} must mount application code read-only")

    for service_name in sorted(ARTIFACT_READ_ONLY):
        artifact_mount = _volume(services[service_name], "/var/lib/aria/artifacts")
        if not artifact_mount or artifact_mount.get("read_only") is not True:
            raise SupplyChainError(f"{service_name} must not write the evidence artifact volume")
    for service_name in sorted(ARTIFACT_WRITERS):
        artifact_mount = _volume(services[service_name], "/var/lib/aria/artifacts")
        if not artifact_mount or artifact_mount.get("read_only") is True:
            raise SupplyChainError(f"{service_name} requires the evidence artifact write boundary")

    for service_name in ("frontend-assets", "redis", "redis-init", "prometheus"):
        service = services.get(service_name, {})
        if service.get("read_only") is not True:
            raise SupplyChainError(f"{service_name} must use a read-only root filesystem")
        if "ALL" not in service.get("cap_drop", []):
            raise SupplyChainError(f"{service_name} must drop all Linux capabilities")
        if "no-new-privileges:true" not in service.get("security_opt", []):
            raise SupplyChainError(f"{service_name} must disable privilege escalation")
        for field in ("pids_limit", "mem_limit", "cpus"):
            _require_positive(service_name, service, field)

    redis_init = services["redis-init"]
    if redis_init.get("user") != "root" or redis_init.get("cap_add") != ["CHOWN"]:
        raise SupplyChainError("redis-init must have only the CHOWN initialization capability")
    if services["redis"].get("user") != "redis":
        raise SupplyChainError("Redis must run directly as its unprivileged image user")
    if services["redis"].get("depends_on", {}).get("redis-init", {}).get("condition") != (
        "service_completed_successfully"
    ):
        raise SupplyChainError("Redis must wait for its volume ownership initializer")

    for service_name in ("postgres", "redis"):
        for field in ("pids_limit", "mem_limit", "cpus"):
            _require_positive(service_name, services.get(service_name, {}), field)

    _validate_local_port("api", services["api"])
    _validate_local_port("prometheus", services["prometheus"])


def validate_vps_edge_config(config: dict[str, Any]) -> None:
    services = config.get("services", {})
    edge_consumers = {
        service_name
        for service_name, service in services.items()
        if "edge" in service.get("networks", {})
    }
    if edge_consumers != {"api"}:
        raise SupplyChainError("only the API may join the VPS edge network")
    api_edge = services["api"]["networks"]["edge"]
    if "aria-api" not in api_edge.get("aliases", []):
        raise SupplyChainError("the VPS edge network must expose only the aria-api alias")
    edge = config.get("networks", {}).get("edge", {})
    if edge.get("external") is not True:
        raise SupplyChainError("the VPS edge network must be externally managed")


def _resolved_compose_config(root: Path, *, include_vps: bool) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--profile",
        "operations",
        "--profile",
        "observability",
        "-f",
        "compose.yaml",
        "-f",
        "compose.production.yaml",
    ]
    if include_vps:
        command.extend(("-f", "compose.vps.yaml"))
    command.extend(("config", "--no-env-resolution", "--format", "json"))
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise SupplyChainError(
            "Docker Compose is required for the production config check"
        ) from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "Docker Compose rejected the production config"
        raise SupplyChainError(detail) from error
    return json.loads(result.stdout)


def resolved_production_config(root: Path = ROOT) -> dict[str, Any]:
    return _resolved_compose_config(root, include_vps=False)


def resolved_vps_config(root: Path = ROOT) -> dict[str, Any]:
    return _resolved_compose_config(root, include_vps=True)


def main() -> int:
    try:
        validate_repository()
        validate_runtime_config(resolved_production_config())
        vps_config = resolved_vps_config()
        validate_runtime_config(vps_config)
        validate_vps_edge_config(vps_config)
    except (SupplyChainError, json.JSONDecodeError) as error:
        print(f"Supply-chain check failed: {error}", file=sys.stderr)
        return 1
    print(
        "Supply-chain check passed: locks, images, write boundaries, "
        "and resource ceilings verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
