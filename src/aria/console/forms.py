from django import forms

from aria.comparisons.models import ComparisonReview


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


class AdmissionAssessmentForm(forms.Form):
    required_captures = forms.TypedChoiceField(
        choices=((2, "2 captures"), (3, "3 captures"), (4, "4 captures"), (5, "5 captures")),
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
