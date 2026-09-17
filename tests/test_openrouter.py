import importlib.util
import json
from io import StringIO
from unittest.mock import MagicMock, patch

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from pydantic import BaseModel, ConfigDict

from aria.health.management.commands.verify_openrouter import OpenRouterProbeOutput
from aria.knowledge.embedding_services import EmbeddingProjectionSummary
from aria.knowledge.embeddings import EmbeddingError, OpenRouterEmbeddingProvider
from aria.openrouter import (
    OpenRouterClient,
    OpenRouterEmbeddingResult,
    OpenRouterError,
    OpenRouterStructuredResult,
    RetryableOpenRouterError,
)


@override_settings(
    EMBEDDING_DIMENSIONS=384,
    OPENROUTER_API_KEY="test-key",
    OPENROUTER_EMBEDDING_MODEL="openai/text-embedding-3-small",
)
class OpenRouterEmbeddingProviderTestCase(SimpleTestCase):
    def test_batches_inputs_with_dimensions(self) -> None:
        first = [0.0] * 384
        first[0] = 1.0
        second = [0.0] * 384
        second[1] = 1.0
        client = MagicMock()
        client.create_embeddings.return_value = OpenRouterEmbeddingResult(
            vectors=(tuple(first), tuple(second)),
            prompt_tokens=7,
        )

        result = OpenRouterEmbeddingProvider(client=client).embed_texts(["first", "second"])

        client.create_embeddings.assert_called_once_with(
            model="openai/text-embedding-3-small",
            inputs=["first", "second"],
            dimensions=384,
        )
        self.assertEqual(result.vectors[0][0], 1.0)
        self.assertEqual(result.vectors[1][1], 1.0)
        self.assertEqual(result.prompt_tokens, 7)

    def test_rejects_wrong_vector_dimensions(self) -> None:
        client = MagicMock()
        client.create_embeddings.return_value = OpenRouterEmbeddingResult(
            vectors=(tuple([0.0] * 10),),
            prompt_tokens=1,
        )

        with self.assertRaisesMessage(EmbeddingError, "10 dimensions; expected 384"):
            OpenRouterEmbeddingProvider(client=client).embed_texts(["invalid"])


class OpenAISDKRemovalTestCase(SimpleTestCase):
    def test_openai_sdk_is_not_installed(self) -> None:
        self.assertIsNone(importlib.util.find_spec("openai"))


class ExampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: str


@override_settings(
    OPENROUTER_API_KEY="test-key",
    OPENROUTER_BASE_URL="https://openrouter.test/api/v1",
    OPENROUTER_HTTP_REFERER="https://aria.example",
    OPENROUTER_APP_TITLE="ARIA Test",
    OPENROUTER_ZDR=True,
    OPENROUTER_DATA_COLLECTION="deny",
    OPENROUTER_ALLOW_PROVIDER_FALLBACKS=True,
    OPENROUTER_TIMEOUT_SECONDS=5,
    OPENROUTER_MAX_RETRIES=0,
)
class OpenRouterClientTestCase(SimpleTestCase):
    def test_structured_request_enforces_schema_privacy_and_attribution(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "generation-1",
                    "choices": [{"message": {"content": '{"result":"bounded"}'}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4},
                },
            )

        with httpx.Client(
            base_url="https://openrouter.test/api/v1/",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            result = OpenRouterClient(client=http_client).generate_structured(
                model="openai/gpt-5.6-sol",
                system_prompt="Use supplied evidence only.",
                input_payload={"evidence": "official text"},
                output_model=ExampleOutput,
                schema_name="aria_test_output",
                reasoning_effort="low",
                max_output_tokens=100,
            )

        self.assertEqual(result.output.result, "bounded")
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 4)
        self.assertEqual(captured["headers"]["authorization"], "Bearer test-key")
        self.assertEqual(captured["headers"]["http-referer"], "https://aria.example")
        self.assertEqual(captured["headers"]["x-openrouter-title"], "ARIA Test")
        payload = captured["payload"]
        self.assertEqual(payload["provider"]["zdr"], True)
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertEqual(payload["provider"]["require_parameters"], True)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(
            json.loads(payload["messages"][1]["content"]),
            {"evidence": "official text"},
        )

    def test_embedding_response_is_reordered_by_index(self) -> None:
        first = [1.0, 0.0]
        second = [0.0, 1.0]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": second},
                        {"index": 0, "embedding": first},
                    ],
                    "usage": {"prompt_tokens": 3},
                },
            )

        with httpx.Client(
            base_url="https://openrouter.test/api/v1/",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            result = OpenRouterClient(client=http_client).create_embeddings(
                model="openai/text-embedding-3-small",
                inputs=["first", "second"],
                dimensions=2,
            )

        self.assertEqual(result.vectors, (tuple(first), tuple(second)))
        self.assertEqual(result.prompt_tokens, 3)

    def test_retryable_http_failure_is_classified(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})

        with httpx.Client(
            base_url="https://openrouter.test/api/v1/",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            with self.assertRaisesMessage(RetryableOpenRouterError, "rate limited"):
                OpenRouterClient(client=http_client).create_embeddings(
                    model="openai/text-embedding-3-small",
                    inputs=["text"],
                    dimensions=2,
                )


@override_settings(
    OPENROUTER_API_KEY="test-key",
    OPENROUTER_SUMMARY_MODEL="openai/gpt-5.6-sol",
    OPENROUTER_SUMMARY_REASONING_EFFORT="low",
    OPENROUTER_EMBEDDING_MODEL="openai/text-embedding-3-small",
    EMBEDDING_DIMENSIONS=384,
    OPENROUTER_ZDR=True,
    OPENROUTER_DATA_COLLECTION="deny",
)
class VerifyOpenRouterCommandTestCase(SimpleTestCase):
    @patch("aria.health.management.commands.verify_openrouter.OpenRouterClient")
    def test_command_verifies_both_capabilities_without_printing_content(self, client_class):
        client = client_class.return_value
        client.generate_structured.return_value = OpenRouterStructuredResult(
            response_id="generation-1",
            output=OpenRouterProbeOutput(status="ok"),
            input_tokens=3,
            output_tokens=1,
        )
        client.create_embeddings.return_value = OpenRouterEmbeddingResult(
            vectors=(tuple([0.0] * 384),),
            prompt_tokens=4,
        )
        output = StringIO()

        call_command("verify_openrouter", "--json", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["embedding_dimensions"], 384)
        self.assertTrue(payload["structured_response_id_present"])

    @patch("aria.health.management.commands.verify_openrouter.OpenRouterClient")
    def test_command_fails_closed_on_provider_error(self, client_class):
        client_class.side_effect = OpenRouterError("authentication failed")

        with self.assertRaisesMessage(CommandError, "authentication failed"):
            call_command("verify_openrouter")


class EmbedAllCurrentSectionsCommandTestCase(SimpleTestCase):
    @patch(
        "aria.knowledge.management.commands.embed_sections.project_section_embeddings"
    )
    @patch("aria.knowledge.management.commands.embed_sections.current_sections_queryset")
    def test_all_scope_projects_every_current_section_synchronously(
        self,
        current_sections_queryset,
        project_section_embeddings,
    ):
        ordered_sections = MagicMock()
        current_sections_queryset.return_value.order_by.return_value = ordered_sections
        project_section_embeddings.return_value = EmbeddingProjectionSummary(
            provider="openrouter",
            model="openai/text-embedding-3-small",
            dimensions=384,
            candidate_count=3,
            created_count=3,
            skipped_count=0,
            prompt_tokens=9,
        )
        output = StringIO()

        call_command(
            "embed_sections",
            "--provider",
            "openrouter",
            "--all",
            "--sync",
            "--json",
            stdout=output,
        )

        current_sections_queryset.assert_called_once_with(None)
        project_section_embeddings.assert_called_once_with(
            ordered_sections,
            provider_name="openrouter",
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["scope"], "all_current_sections")
        self.assertEqual(payload["created_count"], 3)

    def test_all_scope_requires_explicit_synchronous_execution(self):
        with self.assertRaisesMessage(CommandError, "--all requires --sync"):
            call_command("embed_sections", "--all")
