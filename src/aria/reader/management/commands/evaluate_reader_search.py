import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from aria.reader.evaluation import evaluate_reader_retrieval


class Command(BaseCommand):
    help = "Evaluate reader retrieval across the versioned JPDP, AGC, and Parliament cases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=("hybrid", "full_text", "vector"),
            default="hybrid",
        )
        parser.add_argument(
            "--provider",
            choices=("local_hash", "openrouter"),
            default=settings.READER_EMBEDDING_PROVIDER,
        )
        parser.add_argument("--require-hit-at-3", type=float, default=None)

    def handle(self, *args, **options):
        report = evaluate_reader_retrieval(
            mode=options["mode"],
            provider_name=options["provider"],
        )
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        minimum = options["require_hit_at_3"]
        if minimum is not None and report["hit_at_3"] < minimum:
            raise CommandError(
                f"Reader hit@3 {report['hit_at_3']:.3f} is below required {minimum:.3f}."
            )
