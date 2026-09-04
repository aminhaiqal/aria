import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from aria.events.services import record_audit_event
from aria.impacts.models import ApplicabilityTaxonomy, ApplicabilityTerm

TAXONOMY_ROOT = Path(__file__).with_name("taxonomies")
DEFAULT_TAXONOMY_PATH = TAXONOMY_ROOT / "aria-my-business-applicability-v1.json"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TaxonomyDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class TaxonomyDefinition:
    slug: str
    schema_version: int
    version: int
    checksum: str
    definition: dict
    path: Path


@dataclass(frozen=True)
class TaxonomyPlan:
    slug: str
    version: int
    checksum: str
    term_count: int
    action: str

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "version": self.version,
            "checksum": self.checksum,
            "term_count": self.term_count,
            "action": self.action,
        }


def _canonical_bytes(definition: dict) -> bytes:
    return json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def validate_taxonomy_definition(definition: dict) -> None:
    required = {
        "schema_version",
        "slug",
        "version",
        "name",
        "description",
        "jurisdiction",
        "disclaimer",
        "terms",
    }
    if not isinstance(definition, dict):
        raise TaxonomyDefinitionError("Taxonomy definition must be a JSON object.")
    missing = sorted(required - set(definition))
    if missing:
        raise TaxonomyDefinitionError(f"Taxonomy definition is missing: {', '.join(missing)}.")
    if definition["schema_version"] != 1:
        raise TaxonomyDefinitionError("Only taxonomy schema_version 1 is supported.")
    if not SLUG_PATTERN.fullmatch(str(definition["slug"])):
        raise TaxonomyDefinitionError("Taxonomy slug is invalid.")
    if not isinstance(definition["version"], int) or definition["version"] < 1:
        raise TaxonomyDefinitionError("Taxonomy version must be a positive integer.")
    for key in ("name", "description", "jurisdiction", "disclaimer"):
        if not isinstance(definition[key], str) or not definition[key].strip():
            raise TaxonomyDefinitionError(f"Taxonomy {key} must be a non-empty string.")
    terms = definition["terms"]
    if not isinstance(terms, list) or not terms or len(terms) > 500:
        raise TaxonomyDefinitionError("Taxonomy terms must contain between 1 and 500 entries.")

    seen = set()
    for index, term in enumerate(terms):
        location = f"terms[{index}]"
        if not isinstance(term, dict):
            raise TaxonomyDefinitionError(f"{location} must be an object.")
        missing = {"dimension", "code", "label", "description", "aliases"} - set(term)
        if missing:
            raise TaxonomyDefinitionError(f"{location} is missing: {', '.join(sorted(missing))}.")
        dimension = str(term["dimension"])
        code = str(term["code"])
        if dimension not in ApplicabilityTerm.Dimension.values:
            raise TaxonomyDefinitionError(f"{location}.dimension is unsupported.")
        if not SLUG_PATTERN.fullmatch(code):
            raise TaxonomyDefinitionError(f"{location}.code is invalid.")
        key = (dimension, code)
        if key in seen:
            raise TaxonomyDefinitionError(f"{location} duplicates {dimension}:{code}.")
        for field in ("label", "description"):
            if not isinstance(term[field], str) or not term[field].strip():
                raise TaxonomyDefinitionError(f"{location}.{field} must be non-empty.")
        aliases = term["aliases"]
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise TaxonomyDefinitionError(f"{location}.aliases must contain strings.")
        parent_code = term.get("parent_code")
        if parent_code and (dimension, parent_code) not in seen:
            raise TaxonomyDefinitionError(
                f"{location}.parent_code must reference an earlier term in the same dimension."
            )
        seen.add(key)


def load_taxonomy_definition(path: Path = DEFAULT_TAXONOMY_PATH) -> TaxonomyDefinition:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TaxonomyDefinitionError(f"Could not read taxonomy definition: {error}") from error
    validate_taxonomy_definition(definition)
    return TaxonomyDefinition(
        slug=definition["slug"],
        schema_version=definition["schema_version"],
        version=definition["version"],
        checksum=hashlib.sha256(_canonical_bytes(definition)).hexdigest(),
        definition=definition,
        path=path,
    )


def build_taxonomy_plan(definition: TaxonomyDefinition) -> TaxonomyPlan:
    existing = ApplicabilityTaxonomy.objects.filter(
        slug=definition.slug,
        version=definition.version,
    ).first()
    if existing and existing.checksum != definition.checksum:
        raise TaxonomyDefinitionError(
            "The installed taxonomy version has different bytes; publish a new version instead."
        )
    return TaxonomyPlan(
        slug=definition.slug,
        version=definition.version,
        checksum=definition.checksum,
        term_count=len(definition.definition["terms"]),
        action="reuse" if existing else "create",
    )


@transaction.atomic
def apply_taxonomy_definition(
    definition: TaxonomyDefinition,
    *,
    actor_type: str = "system",
    actor_identifier: str = "",
) -> tuple[ApplicabilityTaxonomy, bool]:
    plan = build_taxonomy_plan(definition)
    if plan.action == "reuse":
        return ApplicabilityTaxonomy.objects.get(
            slug=definition.slug,
            version=definition.version,
        ), False
    snapshot = definition.definition
    taxonomy = ApplicabilityTaxonomy(
        slug=definition.slug,
        schema_version=definition.schema_version,
        version=definition.version,
        name=snapshot["name"].strip(),
        description=snapshot["description"].strip(),
        jurisdiction=snapshot["jurisdiction"].strip(),
        disclaimer=snapshot["disclaimer"].strip(),
        checksum=definition.checksum,
        definition=snapshot,
    )
    taxonomy.full_clean()
    taxonomy.save()
    terms_by_key = {}
    for spec in snapshot["terms"]:
        key = (spec["dimension"], spec["code"])
        parent = terms_by_key.get((spec["dimension"], spec.get("parent_code")))
        term = ApplicabilityTerm(
            taxonomy=taxonomy,
            dimension=spec["dimension"],
            code=spec["code"],
            label=spec["label"].strip(),
            description=spec["description"].strip(),
            parent=parent,
            aliases=spec["aliases"],
            metadata=spec.get("metadata", {}),
        )
        term.full_clean()
        term.save()
        terms_by_key[key] = term
    record_audit_event(
        action="impact.taxonomy_applied",
        target_type="applicability_taxonomy",
        target_id=taxonomy.id,
        actor_type=actor_type,
        actor_identifier=actor_identifier[:255],
        details={**plan.as_dict(), "path": str(definition.path)},
    )
    return taxonomy, True


def resolve_taxonomy_term(
    taxonomy: ApplicabilityTaxonomy,
    *,
    dimension: str,
    code: str,
) -> ApplicabilityTerm:
    try:
        return taxonomy.terms.get(dimension=dimension, code=code)
    except ApplicabilityTerm.DoesNotExist as error:
        raise ValidationError(f"Unknown applicability term {dimension}:{code}.") from error
