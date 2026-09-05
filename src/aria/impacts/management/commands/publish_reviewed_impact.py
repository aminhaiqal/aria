import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from aria.impacts.models import ImpactReview
from aria.impacts.publications import publish_reviewed_impact


class Command(BaseCommand):
    help = "Publish one exact approved impact review into the durable outbox."

    def add_arguments(self, parser) -> None:
        parser.add_argument("review_id")
        parser.add_argument("--publisher", required=True)
        parser.add_argument("--confirm", required=True)

    def handle(self, *args, **options) -> None:
        if options["confirm"] != "PUBLISH":
            raise CommandError("Publication requires --confirm PUBLISH.")
        user_model = get_user_model()
        try:
            review = ImpactReview.objects.get(pk=options["review_id"])
            publisher = user_model.objects.get(username=options["publisher"], is_active=True)
        except (ImpactReview.DoesNotExist, ValueError) as error:
            raise CommandError("The requested impact review does not exist.") from error
        except user_model.DoesNotExist as error:
            raise CommandError("The requested active publisher does not exist.") from error
        result = publish_reviewed_impact(review, publisher=publisher)
        self.stdout.write(
            json.dumps(
                {
                    "publication_id": str(result.publication.id),
                    "pipeline_event_id": str(result.publication.pipeline_event_id),
                    "created": result.created,
                },
                sort_keys=True,
            )
        )
