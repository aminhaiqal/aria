from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from scripts.verify_supply_chain import (
    PRODUCTION_ENVIRONMENT,
    SupplyChainError,
    validate_dockerfile,
    validate_lock,
    validate_repository,
    validate_runtime_config,
    validate_vps_edge_config,
)

ROOT = Path(__file__).resolve().parents[1]


def hardened_service(*, artifact_read_only: bool) -> dict:
    return {
        "user": "aria",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 128,
        "mem_limit": "536870912",
        "cpus": 1,
        "environment": {**PRODUCTION_ENVIRONMENT, "ARIA_RELEASE_REVISION": "unknown"},
        "volumes": [
            {
                "target": "/var/lib/aria/artifacts",
                "read_only": artifact_read_only,
            },
        ],
    }


def runtime_config() -> dict:
    services = {
        name: hardened_service(artifact_read_only=name in {"api", "backup", "beat", "migrate"})
        for name in (
            "api",
            "backup",
            "beat",
            "browser-worker",
            "migrate",
            "ocr-worker",
            "worker",
        )
    }
    services["api"].update(
        {
            "ports": [{"host_ip": "127.0.0.1"}],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "probe ARIA_ALLOWED_HOSTS at /health/live/",
                ]
            },
        }
    )
    for name in ("frontend-assets", "redis", "redis-init", "prometheus"):
        services[name] = {
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 128,
            "mem_limit": "536870912",
            "cpus": 1,
        }
    services["prometheus"]["ports"] = [{"host_ip": "127.0.0.1"}]
    services["redis-init"].update(
        {
            "user": "root",
            "cap_add": ["CHOWN", "DAC_READ_SEARCH"],
            "network_mode": "none",
        }
    )
    services["redis"]["user"] = "redis"
    services["redis"]["depends_on"] = {
        "redis-init": {"condition": "service_completed_successfully"}
    }
    services["postgres"] = {
        "pids_limit": 128,
        "mem_limit": "536870912",
        "cpus": 1,
    }
    return {"services": services}


class SupplyChainLockTests(SimpleTestCase):
    def test_current_repository_has_immutable_images_and_hashed_locks(self) -> None:
        validate_repository(ROOT)

    def test_lock_rejects_unhashed_or_ranged_requirement(self) -> None:
        with TemporaryDirectory() as temporary:
            lock = Path(temporary) / "requirements.lock"
            lock.write_text("Django>=5.2,<5.3\n", encoding="utf-8")
            with self.assertRaisesMessage(SupplyChainError, "non-exact or unhashed"):
                validate_lock(lock)

    def test_dockerfile_rejects_mutable_external_image(self) -> None:
        with TemporaryDirectory() as temporary:
            dockerfile = Path(temporary) / "Dockerfile"
            dockerfile.write_text(
                "FROM python:3.13 AS base\n"
                "RUN pip install --require-hashes --requirement requirements.lock\n"
                "RUN pip install --require-hashes --requirement requirements-browser.lock\n"
                "RUN pip install --require-hashes --requirement requirements-dev.lock\n",
                encoding="utf-8",
            )
            with self.assertRaisesMessage(SupplyChainError, "not pinned by digest"):
                validate_dockerfile(dockerfile)


class ProductionIsolationTests(SimpleTestCase):
    def test_hardened_runtime_contract_is_accepted(self) -> None:
        validate_runtime_config(runtime_config())

    def test_writable_api_root_is_rejected(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["api"]["read_only"] = False

        with self.assertRaisesMessage(SupplyChainError, "read-only root"):
            validate_runtime_config(config)

    def test_production_source_bind_is_rejected(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["api"]["volumes"].append(
            {"target": "/app", "read_only": True}
        )

        with self.assertRaisesMessage(SupplyChainError, "immutable image"):
            validate_runtime_config(config)

    def test_public_api_port_is_rejected(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["api"]["ports"][0]["host_ip"] = "0.0.0.0"

        with self.assertRaisesMessage(SupplyChainError, "127.0.0.1"):
            validate_runtime_config(config)

    def test_api_healthcheck_must_use_configured_allowed_host(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["api"]["healthcheck"]["test"][-1] = (
            "probe 127.0.0.1 at /health/live/"
        )

        with self.assertRaisesMessage(SupplyChainError, "configured allowed host"):
            validate_runtime_config(config)

    def test_redis_initializer_requires_minimal_volume_capabilities(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["redis-init"]["cap_add"].append("DAC_OVERRIDE")

        with self.assertRaisesMessage(SupplyChainError, "initialization capabilities"):
            validate_runtime_config(config)

    def test_redis_initializer_cannot_access_network(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["redis-init"]["network_mode"] = "default"

        with self.assertRaisesMessage(SupplyChainError, "without network access"):
            validate_runtime_config(config)

    def test_reader_cannot_mutate_evidence_artifacts(self) -> None:
        config = deepcopy(runtime_config())
        config["services"]["api"]["volumes"][0]["read_only"] = False

        with self.assertRaisesMessage(SupplyChainError, "must not write"):
            validate_runtime_config(config)

    def test_only_api_may_join_external_vps_edge(self) -> None:
        config = runtime_config()
        config["networks"] = {"edge": {"external": True}}
        config["services"]["api"]["networks"] = {
            "backend": {},
            "edge": {"aliases": ["aria-api"]},
        }

        validate_vps_edge_config(config)

        config["services"]["postgres"]["networks"] = {"edge": {}}
        with self.assertRaisesMessage(SupplyChainError, "only the API"):
            validate_vps_edge_config(config)
