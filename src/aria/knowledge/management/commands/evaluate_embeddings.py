import json

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from aria.collections.models import PublicationCollection
from aria.knowledge.embeddings import SUPPORTED_PROVIDERS, EmbeddingError
from aria.knowledge.evaluation import evaluate_embedding_provider


class Command(BaseCommand):
    help = "Evaluate vector retrieval providers against the versioned JPDP benchmark."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--authority",
            default="personal-data-protection-commissioner-malaysia",
        )
        parser.add_argument(
            "--collection",
            default="act-709-regulatory-publications",
        )
        parser.add_argument(
            "--providers",
            nargs="+",
            choices=sorted(SUPPORTED_PROVIDERS),
            default=["local_hash", "openai"],
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            collection = PublicationCollection.objects.select_related("authority").get(
                authority__slug=options["authority"],
                slug=options["collection"],
            )
        except PublicationCollection.DoesNotExist as error:
            raise CommandError("The requested authority collection does not exist.") from error

        evaluations = []
        for provider_name in options["providers"]:
            try:
                evaluations.append(
                    evaluate_embedding_provider(collection, provider_name=provider_name)
                )
            except (EmbeddingError, ImproperlyConfigured) as error:
                raise CommandError(f"Embedding evaluation failed: {error}") from error

        payload = {"benchmark": "jpdp-retrieval-v1", "evaluations": evaluations}
        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return
        for evaluation in evaluations:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{evaluation['provider']}/{evaluation['model']}: "
                    f"MRR={evaluation['mean_reciprocal_rank']:.3f} "
                    f"hit@1={evaluation['hit_at_1']:.3f} "
                    f"hit@3={evaluation['hit_at_3']:.3f} "
                    f"hit@5={evaluation['hit_at_5']:.3f}"
                )
            )
