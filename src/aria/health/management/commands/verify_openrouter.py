import json
from typing import Literal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pydantic import BaseModel, ConfigDict

from aria.openrouter import OpenRouterClient, OpenRouterError


class OpenRouterProbeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class Command(BaseCommand):
    help = "Verify OpenRouter structured output and embeddings without exposing generated content."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            client = OpenRouterClient()
            structured = client.generate_structured(
                model=settings.OPENROUTER_SUMMARY_MODEL,
                system_prompt="Return the supplied status exactly. Do not add any other fields.",
                input_payload={"status": "ok"},
                output_model=OpenRouterProbeOutput,
                schema_name="aria_openrouter_probe",
                reasoning_effort=settings.OPENROUTER_SUMMARY_REASONING_EFFORT,
                max_output_tokens=256,
            )
            embedding = client.create_embeddings(
                model=settings.OPENROUTER_EMBEDDING_MODEL,
                inputs=["ARIA OpenRouter connectivity probe"],
                dimensions=settings.EMBEDDING_DIMENSIONS,
            )
        except OpenRouterError as error:
            raise CommandError(f"OpenRouter verification failed: {error}") from error

        observed_dimensions = len(embedding.vectors[0]) if embedding.vectors else 0
        if observed_dimensions != settings.EMBEDDING_DIMENSIONS:
            raise CommandError(
                "OpenRouter verification failed: embedding dimensions "
                f"were {observed_dimensions}, expected {settings.EMBEDDING_DIMENSIONS}."
            )

        payload = {
            "status": structured.output.status,
            "structured_model": settings.OPENROUTER_SUMMARY_MODEL,
            "structured_response_id_present": bool(structured.response_id),
            "structured_input_tokens": structured.input_tokens,
            "structured_output_tokens": structured.output_tokens,
            "embedding_model": settings.OPENROUTER_EMBEDDING_MODEL,
            "embedding_dimensions": observed_dimensions,
            "embedding_prompt_tokens": embedding.prompt_tokens,
            "zdr_required": settings.OPENROUTER_ZDR,
            "data_collection": settings.OPENROUTER_DATA_COLLECTION,
        }
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        details = " ".join(f"{key}={value}" for key, value in payload.items())
        self.stdout.write(self.style.SUCCESS(f"OpenRouter verification passed: {details}"))
