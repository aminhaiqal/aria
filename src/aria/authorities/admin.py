from django.contrib import admin

from aria.authorities.models import Authority


@admin.register(Authority)
class AuthorityAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "country_code",
        "authority_type",
        "trust_classification",
        "is_enabled",
    )
    list_filter = ("country_code", "authority_type", "trust_classification", "is_enabled")
    search_fields = ("name", "slug", "jurisdiction")
    prepopulated_fields = {"slug": ("name",)}
