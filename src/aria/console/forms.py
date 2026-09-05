from django import forms

from aria.comparisons.models import ComparisonReview
from aria.impacts.models import ApplicabilityTerm, ImpactReview


class ComparisonReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(("", "Choose a decision"), *ComparisonReview.Decision.choices),
        widget=forms.Select(attrs={"class": "field-control"}),
    )
    rationale = forms.CharField(
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "class": "field-control",
                "rows": 3,
                "placeholder": "Record the evidence basis for this decision.",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get("decision")
        rationale = (cleaned.get("rationale") or "").strip()
        if (
            decision
            in (
                ComparisonReview.Decision.REJECTED,
                ComparisonReview.Decision.NEEDS_CONTEXT,
            )
            and len(rationale) < 10
        ):
            self.add_error(
                "rationale",
                "Provide at least 10 characters of rationale for this decision.",
            )
        cleaned["rationale"] = rationale
        return cleaned


class PublicationConfirmationForm(forms.Form):
    confirmation = forms.CharField(max_length=16)

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip()
        if value != "PUBLISH":
            raise forms.ValidationError("Type PUBLISH to confirm this action.")
        return value


class ResourceRetirementForm(forms.Form):
    reason = forms.CharField(
        min_length=10,
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "field-control",
                "rows": 3,
                "placeholder": (
                    "Explain why this endpoint is no longer an active official resource."
                ),
            }
        ),
    )
    confirmation = forms.CharField(max_length=16)

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip()
        if value != "RETIRE":
            raise forms.ValidationError("Type RETIRE to confirm this action.")
        return value


class ImpactReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(("", "Choose a decision"), *ImpactReview.Decision.choices),
        widget=forms.Select(attrs={"class": "field-control"}),
    )
    title = forms.CharField(
        max_length=512,
        widget=forms.TextInput(attrs={"class": "field-control"}),
    )
    statement = forms.CharField(
        max_length=4000,
        widget=forms.Textarea(attrs={"class": "field-control", "rows": 4}),
    )
    effective_date_text = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "field-control",
                "placeholder": "Only when explicit in the cited evidence",
            }
        ),
    )
    included_terms = forms.ModelMultipleChoiceField(
        queryset=ApplicabilityTerm.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field-control", "size": 7}),
    )
    excluded_terms = forms.ModelMultipleChoiceField(
        queryset=ApplicabilityTerm.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "field-control", "size": 5}),
    )
    rationale = forms.CharField(
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "class": "field-control",
                "rows": 3,
                "placeholder": "Record why this decision follows from the cited evidence.",
            }
        ),
    )

    def __init__(self, *args, impact, **kwargs):
        super().__init__(*args, **kwargs)
        taxonomy = impact.generation.taxonomy if impact.generation_id else None
        if taxonomy is None:
            target = impact.targets.select_related("term__taxonomy").first()
            taxonomy = target.term.taxonomy if target else None
        terms = ApplicabilityTerm.objects.none()
        if taxonomy:
            terms = taxonomy.terms.order_by("dimension", "label")
        self.fields["included_terms"].queryset = terms
        self.fields["excluded_terms"].queryset = terms

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get("decision")
        rationale = (cleaned.get("rationale") or "").strip()
        included = cleaned.get("included_terms")
        excluded = cleaned.get("excluded_terms")
        included_ids = set(included.values_list("id", flat=True)) if included is not None else set()
        excluded_ids = set(excluded.values_list("id", flat=True)) if excluded is not None else set()
        if included_ids & excluded_ids:
            self.add_error(
                "excluded_terms",
                "A term cannot be both included and excluded.",
            )
        if decision == ImpactReview.Decision.AMENDED:
            if not included_ids and not excluded_ids:
                self.add_error(
                    "included_terms",
                    "Select at least one controlled target for an amended approval.",
                )
            if len(rationale) < 10:
                self.add_error(
                    "rationale",
                    "Provide at least 10 characters explaining the amendment.",
                )
        elif decision in (ImpactReview.Decision.REJECTED, ImpactReview.Decision.NEEDS_CONTEXT):
            if len(rationale) < 10:
                self.add_error(
                    "rationale",
                    "Provide at least 10 characters explaining this decision.",
                )
        cleaned["rationale"] = rationale
        return cleaned


class AdmissionAssessmentForm(forms.Form):
    required_captures = forms.TypedChoiceField(
        choices=((2, "2 captures"), (3, "3 captures"), (4, "4 captures"), (5, "5 captures")),
        coerce=int,
        initial=2,
    )


class StaticAdmissionAssessmentForm(forms.Form):
    required_runs = forms.TypedChoiceField(
        choices=((2, "2 runs"), (3, "3 runs"), (4, "4 runs"), (5, "5 runs")),
        coerce=int,
        initial=2,
    )


class AdmissionPromotionForm(forms.Form):
    assessment_id = forms.UUIDField(widget=forms.HiddenInput())
    confirmation = forms.CharField(max_length=16)

    def clean_confirmation(self):
        value = self.cleaned_data["confirmation"].strip()
        if value != "PROMOTE":
            raise forms.ValidationError("Type PROMOTE to confirm scheduled retrieval.")
        return value
