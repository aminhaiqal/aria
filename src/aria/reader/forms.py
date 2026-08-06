from django import forms

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection


class ReaderSearchForm(forms.Form):
    q = forms.CharField(
        label="Search official material",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Search Acts, regulations, bills, circulars, and guidelines",
            }
        ),
    )
    mode = forms.ChoiceField(
        required=False,
        choices=(
            ("hybrid", "Hybrid"),
            ("full_text", "Exact text"),
            ("vector", "Semantic"),
        ),
        initial="hybrid",
    )
    embedding_provider = forms.ChoiceField(
        required=False,
        choices=(
            ("", "Configured default"),
            ("openai", "OpenAI semantic"),
            ("local_hash", "Local lexical vector"),
        ),
    )
    authority = forms.ChoiceField(required=False)
    collection = forms.ChoiceField(required=False)
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        authorities = Authority.objects.filter(is_enabled=True).order_by("name")
        collections = PublicationCollection.objects.filter(
            is_enabled=True,
            is_evidence_eligible=True,
            authority__is_enabled=True,
        ).select_related("authority")
        self.fields["authority"].choices = [("", "All authorities"), *[
            (authority.slug, authority.name) for authority in authorities
        ]]
        self.fields["collection"].choices = [("", "All collections"), *[
            (
                str(collection.id),
                f"{collection.authority.name} — {collection.name}",
            )
            for collection in collections
        ]]

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "The end date must be on or after the start date.")
        return cleaned
