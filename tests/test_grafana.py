import json
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "config" / "grafana" / "dashboards" / "aria-pipeline-performance.json"
DATASOURCE = (
    ROOT / "config" / "grafana" / "provisioning" / "datasources" / "aria-prometheus.yml"
)
CADDYFILE = ROOT / "deploy" / "caddy" / "aria.Caddyfile"


class GrafanaProvisioningTests(SimpleTestCase):
    def test_dashboard_is_versioned_and_covers_the_pipeline_scorecard(self) -> None:
        dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
        expressions = {
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        }

        self.assertEqual(dashboard["uid"], "aria-pipeline-performance")
        self.assertEqual(dashboard["refresh"], "30s")
        self.assertFalse(dashboard["editable"])
        self.assertIn('aria_source_run_success_ratio{window="24h"}', expressions)
        self.assertIn("min(aria_evidence_coverage_ratio)", expressions)
        self.assertIn("aria_pipeline_oldest_active_age_seconds", expressions)
        self.assertIn('aria_source_runs_window{window="24h"}', expressions)
        self.assertIn("aria_evidence_coverage_ratio", expressions)

    def test_datasource_is_private_provisioned_and_read_only(self) -> None:
        datasource = DATASOURCE.read_text(encoding="utf-8")

        self.assertIn("uid: aria-prometheus", datasource)
        self.assertIn("url: http://prometheus:9090", datasource)
        self.assertIn("editable: false", datasource)
        self.assertNotIn("localhost", datasource)

    def test_public_metrics_route_exposes_only_grafana(self) -> None:
        caddyfile = CADDYFILE.read_text(encoding="utf-8")

        self.assertIn("metrics.aria.axelyn.com {", caddyfile)
        self.assertIn("reverse_proxy aria-grafana:3000", caddyfile)
        self.assertIn('X-Robots-Tag "noindex, nofollow, noarchive"', caddyfile)
        metrics_block = caddyfile.split("metrics.aria.axelyn.com {", 1)[1]
        self.assertNotIn("prometheus", metrics_block)
        self.assertNotIn(":9090", metrics_block)
