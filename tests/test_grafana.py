import json
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "config" / "grafana" / "dashboards" / "aria-pipeline-performance.json"
SOURCE_DASHBOARD = (
    ROOT / "config" / "grafana" / "dashboards" / "aria-source-reliability.json"
)
RELEASE_DASHBOARD = (
    ROOT
    / "config"
    / "grafana"
    / "dashboards"
    / "aria-review-publication-delivery.json"
)
DATASOURCE = (
    ROOT / "config" / "grafana" / "provisioning" / "datasources" / "aria-prometheus.yml"
)
ALERTS = ROOT / "config" / "prometheus" / "aria-alerts.yml"
CADDYFILE = ROOT / "deploy" / "caddy" / "aria.Caddyfile"
COMPOSE = ROOT / "compose.yaml"


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

    def test_source_reliability_dashboard_is_versioned_and_filterable(self) -> None:
        dashboard = json.loads(SOURCE_DASHBOARD.read_text(encoding="utf-8"))
        expressions = {
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        }
        panel_titles = {panel["title"] for panel in dashboard["panels"]}
        variables = {variable["name"]: variable for variable in dashboard["templating"]["list"]}

        self.assertEqual(dashboard["uid"], "aria-source-reliability")
        self.assertEqual(dashboard["refresh"], "30s")
        self.assertEqual(dashboard["time"]["from"], "now-7d")
        self.assertFalse(dashboard["editable"])
        self.assertTrue(variables["source"]["includeAll"])
        self.assertEqual(
            variables["source"]["definition"],
            "label_values(aria_source_reliability_info, source)",
        )
        self.assertIn(
            'count(aria_source_freshness_headroom_seconds{source=~"$source"} < 0) '
            "or vector(0)",
            expressions,
        )
        self.assertIn(
            'min by (source, stage) (aria_source_coverage_ratio{source=~"$source"})',
            expressions,
        )
        self.assertIn(
            'sum by (source, status) (aria_source_scheduled_runs_window{source=~"$source",'
            'window="7d"})',
            expressions,
        )
        self.assertIn("Current reliability findings", panel_titles)
        self.assertIn("Current HTTP monitoring health", panel_titles)
        self.assertIn("Freshness headroom", panel_titles)
        self.assertIn(
            'max by (source) (aria_source_poll_overdue_seconds{source=~"$source"})',
            expressions,
        )

    def test_source_reliability_alerts_cover_freshness_polling_and_coverage(self) -> None:
        alerts = ALERTS.read_text(encoding="utf-8")

        self.assertIn("alert: AriaSourceReliabilityNeedsAttention", alerts)
        self.assertIn("alert: AriaSourceFreshnessExpired", alerts)
        self.assertIn("alert: AriaSourcePollOverdue", alerts)
        self.assertIn("alert: AriaSourceCoverageIncomplete", alerts)
        self.assertIn("aria_source_freshness_headroom_seconds < 0", alerts)
        self.assertIn("aria_source_poll_overdue_seconds > 900", alerts)

    def test_review_publication_delivery_dashboard_covers_governed_outputs(self) -> None:
        dashboard = json.loads(RELEASE_DASHBOARD.read_text(encoding="utf-8"))
        expressions = {
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        }
        panel_titles = {panel["title"] for panel in dashboard["panels"]}

        self.assertEqual(dashboard["uid"], "aria-review-publication-delivery")
        self.assertEqual(dashboard["refresh"], "30s")
        self.assertEqual(dashboard["time"]["from"], "now-7d")
        self.assertFalse(dashboard["editable"])
        self.assertIn("sum(aria_review_queue) or vector(0)", expressions)
        self.assertIn("max(aria_review_oldest_age_seconds)", expressions)
        self.assertIn("aria_publication_ready", expressions)
        self.assertIn(
            'aria_publication_duration_quantile_seconds{window="7d"}', expressions
        )
        self.assertIn('aria_impact_delivery_events_window{window="7d"}', expressions)
        self.assertIn("aria_impact_delivery_last_success_age_seconds", expressions)
        self.assertIn(
            'aria_profile_impact_matches_window{window="7d"}', expressions
        )
        self.assertIn("Blocked impact publications", panel_titles)
        self.assertIn("Review decisions · rolling 7d", panel_titles)
        self.assertIn("Delivery outcomes · rolling 7d", panel_titles)

    def test_review_publication_delivery_alerts_cover_stale_work(self) -> None:
        alerts = ALERTS.read_text(encoding="utf-8")

        self.assertIn("alert: AriaReviewQueueStale", alerts)
        self.assertIn("alert: AriaPublicationReadyStale", alerts)
        self.assertIn("alert: AriaOutboxDeliveryPending", alerts)
        self.assertIn("aria_review_oldest_age_seconds > 259200", alerts)
        self.assertIn("aria_publication_ready_oldest_age_seconds > 86400", alerts)
        self.assertIn("aria_impact_delivery_oldest_pending_age_seconds > 900", alerts)
        self.assertIn("aria_impact_delivery_configured == 1", alerts)

    def test_pipeline_dashboard_is_the_server_home_page(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")

        self.assertIn(
            "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: "
            "/etc/grafana/dashboards/aria-pipeline-performance.json",
            compose,
        )

    def test_public_metrics_route_exposes_only_grafana(self) -> None:
        caddyfile = CADDYFILE.read_text(encoding="utf-8")

        self.assertIn("metrics.aria.axelyn.com {", caddyfile)
        self.assertIn("reverse_proxy aria-grafana:3000", caddyfile)
        self.assertIn('X-Robots-Tag "noindex, nofollow, noarchive"', caddyfile)
        metrics_block = caddyfile.split("metrics.aria.axelyn.com {", 1)[1]
        self.assertNotIn("prometheus", metrics_block)
        self.assertNotIn(":9090", metrics_block)
