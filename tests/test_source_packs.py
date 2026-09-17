import copy
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from aria.events.models import AuditEvent, PipelineEvent
from aria.sources.models import SourceEndpoint, SourcePackSnapshot
from aria.sources.source_packs import (
    SourcePack,
    SourcePackError,
    apply_source_pack,
    available_source_packs,
    build_source_pack_plan,
    load_source_pack,
    validate_source_pack,
)


class SourcePackValidationTestCase(TestCase):
    def setUp(self) -> None:
        self.pack = load_source_pack("agc-updated-principal-acts")

    def definition(self) -> dict:
        return copy.deepcopy(self.pack.definition)

    def test_repository_packs_are_valid_and_discoverable(self) -> None:
        self.assertIn("agc-updated-principal-acts", available_source_packs())
        self.assertIn("jpdp-act-709", available_source_packs())
        self.assertIn("parliament-dewan-rakyat-bills", available_source_packs())
        self.assertEqual(len(self.pack.checksum), 64)

    def test_repository_packs_use_three_hour_monitoring_policy(self) -> None:
        for slug in available_source_packs():
            pack = load_source_pack(slug)
            self.assertEqual(pack.version, 4)
            self.assertEqual(pack.definition["endpoint"]["polling_interval_minutes"], 180)
            for resource in pack.definition["resources"]:
                self.assertEqual(resource.get("polling_interval_minutes", 180), 180)

        jpdp = load_source_pack("jpdp-act-709")
        self.assertEqual(
            jpdp.definition["detail_resource_backfill"]["polling_interval_minutes"],
            180,
        )

    def test_pack_rejects_credentials_unsafe_domains_and_side_effect_paths(self) -> None:
        credentials = self.definition()
        credentials["connector_configurations"][0]["configuration"]["api_token"] = "secret"
        with self.assertRaisesMessage(SourcePackError, "credential key"):
            validate_source_pack(credentials)

        private = self.definition()
        private["endpoint"]["allowed_domains"] = ["127.0.0.1"]
        with self.assertRaisesMessage(SourcePackError, "IP addresses"):
            validate_source_pack(private)

        side_effect = self.definition()
        side_effect["connector_configurations"][0]["configuration"][
            "browser_read_only_post_paths"
        ] = ["/api/list?mutate=1"]
        with self.assertRaisesMessage(SourcePackError, "exact safe paths"):
            validate_source_pack(side_effect)

        incomplete_extractor = self.definition()
        incomplete_extractor["connector_configurations"][0]["configuration"][
            "link_value_prefix"
        ] = "loadResult('"
        with self.assertRaisesMessage(SourcePackError, "declared together"):
            validate_source_pack(incomplete_extractor)

        invalid_bootstrap = self.definition()
        invalid_bootstrap["connector_configurations"][0]["configuration"][
            "bootstrap_candidate_session"
        ] = "yes"
        with self.assertRaisesMessage(SourcePackError, "must be a boolean"):
            validate_source_pack(invalid_bootstrap)

    def test_active_connector_must_match_endpoint_version(self) -> None:
        definition = self.definition()
        definition["connector_configurations"][-1]["is_active"] = False
        with self.assertRaisesMessage(SourcePackError, "Exactly"):
            validate_source_pack(definition)

    def test_detail_selector_contract_is_bounded_and_valid(self) -> None:
        definition = copy.deepcopy(load_source_pack("jpdp-act-709").definition)
        configuration = definition["connector_configurations"][-1]["configuration"]
        configuration["detail_content_selector"] = ".legacy"
        with self.assertRaisesMessage(SourcePackError, "either"):
            validate_source_pack(definition)

        malformed = copy.deepcopy(load_source_pack("jpdp-act-709").definition)
        malformed["connector_configurations"][-1]["configuration"][
            "detail_content_selectors"
        ] = ["["]
        with self.assertRaisesMessage(SourcePackError, "Invalid detail selector"):
            validate_source_pack(malformed)

        unbounded = copy.deepcopy(load_source_pack("jpdp-act-709").definition)
        unbounded["connector_configurations"][-1]["configuration"][
            "detail_content_selectors"
        ] = [f".selector-{number}" for number in range(6)]
        with self.assertRaisesMessage(SourcePackError, "at most five"):
            validate_source_pack(unbounded)

    def test_wrapped_link_contract_is_strictly_bounded(self) -> None:
        invalid_path = self.definition()
        invalid_path["connector_configurations"][-1]["configuration"][
            "wrapped_link_target"
        ]["path"] = "https://example.com/processFile.php"
        with self.assertRaisesMessage(SourcePackError, "exact safe paths"):
            validate_source_pack(invalid_path)

        unsupported_encoding = self.definition()
        unsupported_encoding["connector_configurations"][-1]["configuration"][
            "wrapped_link_target"
        ]["encoding"] = "eval_javascript"
        with self.assertRaisesMessage(SourcePackError, "encoding is unsupported"):
            validate_source_pack(unsupported_encoding)


class SourcePackApplicationTestCase(TestCase):
    def test_dry_run_is_read_only_and_apply_is_idempotent(self) -> None:
        pack = load_source_pack("agc-updated-principal-acts")
        plan = build_source_pack_plan(pack)
        self.assertIn("create_disabled_endpoint", plan.actions)
        self.assertEqual(SourceEndpoint.objects.count(), 0)

        first = apply_source_pack(pack)
        replay = apply_source_pack(pack)

        self.assertFalse(first.endpoint.is_enabled)
        self.assertEqual(first.endpoint.connector_configuration_version, 2)
        self.assertIsNone(first.endpoint.next_poll_at)
        self.assertTrue(first.snapshot_created)
        self.assertFalse(replay.snapshot_created)
        self.assertEqual(first.endpoint.id, replay.endpoint.id)
        self.assertEqual(SourcePackSnapshot.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="source.pack.applied").count(),
            1,
        )
        self.assertEqual(
            PipelineEvent.objects.filter(event_type="source.pack.applied").count(),
            1,
        )

    def test_sync_preserves_promoted_activation_and_schedule(self) -> None:
        first = apply_source_pack(load_source_pack("agc-updated-principal-acts"))
        endpoint = first.endpoint
        endpoint.is_enabled = True
        endpoint.health_state = SourceEndpoint.HealthState.HEALTHY
        endpoint.next_poll_at = endpoint.created_at
        endpoint.save(update_fields=("is_enabled", "health_state", "next_poll_at", "updated_at"))
        scheduled_at = endpoint.next_poll_at

        replay = apply_source_pack(load_source_pack("agc-updated-principal-acts"))

        self.assertTrue(replay.endpoint.is_enabled)
        self.assertEqual(replay.endpoint.health_state, SourceEndpoint.HealthState.HEALTHY)
        self.assertEqual(replay.endpoint.next_poll_at, scheduled_at)

    def test_changed_installed_connector_requires_version_bump(self) -> None:
        installed = apply_source_pack(load_source_pack("agc-updated-principal-acts"))
        definition = copy.deepcopy(installed.snapshot.definition)
        definition["connector_configurations"][0]["configuration"]["max_candidates"] = 21
        changed = SourcePack(
            slug=installed.snapshot.pack_slug,
            schema_version=1,
            version=1,
            checksum="f" * 64,
            definition=definition,
            path=load_source_pack("agc-updated-principal-acts").path,
        )

        with self.assertRaisesMessage(SourcePackError, "increment"):
            build_source_pack_plan(changed)

    def test_management_command_requires_dual_confirmation(self) -> None:
        output = StringIO()
        call_command("sync_source_pack", "agc-updated-principal-acts", stdout=output)
        self.assertIn('"mode": "dry_run"', output.getvalue())
        self.assertEqual(SourceEndpoint.objects.count(), 0)

        with self.assertRaisesMessage(CommandError, "both --apply and --confirm"):
            call_command(
                "sync_source_pack",
                "agc-updated-principal-acts",
                apply=True,
                stdout=StringIO(),
            )
        call_command(
            "sync_source_pack",
            "agc-updated-principal-acts",
            apply=True,
            confirm=True,
            stdout=StringIO(),
        )
        self.assertEqual(SourcePackSnapshot.objects.count(), 1)

    def test_parliament_pilot_installs_disabled_with_static_extractor(self) -> None:
        result = apply_source_pack(load_source_pack("parliament-dewan-rakyat-bills"))

        self.assertFalse(result.endpoint.is_enabled)
        self.assertFalse(result.endpoint.requires_javascript)
        self.assertIsNone(result.endpoint.next_poll_at)
        self.assertEqual(result.endpoint.connector_configuration_version, 2)
        configuration = result.endpoint.connector_configurations.get(version=2).configuration
        self.assertEqual(configuration["link_attribute"], "onclick")
        self.assertEqual(configuration["max_candidates"], 25)
        self.assertTrue(configuration["bootstrap_candidate_session"])
