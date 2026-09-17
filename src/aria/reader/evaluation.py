from dataclasses import dataclass
from urllib.parse import unquote

from aria.reader.services import search_reader_documents


@dataclass(frozen=True)
class ReaderRetrievalCase:
    name: str
    query: str
    authority_slug: str
    expected_url_fragment: str


READER_RETRIEVAL_CASES = (
    ReaderRetrievalCase(
        name="jpdp_data_protection_officer",
        query="When is an organization required to appoint a data protection officer?",
        authority_slug="personal-data-protection-commissioner-malaysia",
        expected_url_fragment="appointment-of-data-protection-officer/",
    ),
    ReaderRetrievalCase(
        name="agc_government_procurement",
        query="Which Malaysian Act governs government procurement?",
        authority_slug="attorney-generals-chambers-malaysia",
        expected_url_fragment="GOVERNMENT PROCUREMENT ACT 2026.pdf",
    ),
    ReaderRetrievalCase(
        name="parliament_cybercrimes_bill",
        query="What does the Cybercrimes Bill 2026 provide?",
        authority_slug="parliament-of-malaysia",
        expected_url_fragment="Cybercrimes%20Bill%202026.pdf",
    ),
)


def _normalized_url_text(value: str) -> str:
    return unquote(value).casefold()


def evaluate_reader_retrieval(
    *,
    mode: str = "hybrid",
    provider_name: str = "",
    cases: tuple[ReaderRetrievalCase, ...] = READER_RETRIEVAL_CASES,
    result_limit: int = 5,
) -> dict:
    evaluations = []
    reciprocal_rank_total = 0.0
    hit_at_1 = hit_at_3 = hit_at_5 = 0
    for case in cases:
        result = search_reader_documents(
            case.query,
            mode=mode,
            provider_name=provider_name,
            authority_slug=case.authority_slug,
            page_size=result_limit,
        )
        documents = result["results"]
        rank = next(
            (
                index
                for index, document in enumerate(documents, start=1)
                if _normalized_url_text(case.expected_url_fragment)
                in _normalized_url_text(document["canonical_url"])
            ),
            None,
        )
        if rank is not None:
            reciprocal_rank_total += 1 / rank
            hit_at_1 += rank <= 1
            hit_at_3 += rank <= 3
            hit_at_5 += rank <= 5
        evaluations.append(
            {
                "name": case.name,
                "query": case.query,
                "authority_slug": case.authority_slug,
                "expected_url_fragment": case.expected_url_fragment,
                "rank": rank,
                "top_documents": [
                    {
                        "identity_id": document["identity_id"],
                        "title": document["title"],
                        "canonical_url": document["canonical_url"],
                    }
                    for document in documents
                ],
            }
        )
    case_count = len(cases)
    return {
        "benchmark": "aria-reader-multisource-v1",
        "mode": mode,
        "provider": provider_name,
        "case_count": case_count,
        "mean_reciprocal_rank": reciprocal_rank_total / case_count if case_count else 0.0,
        "hit_at_1": hit_at_1 / case_count if case_count else 0.0,
        "hit_at_3": hit_at_3 / case_count if case_count else 0.0,
        "hit_at_5": hit_at_5 / case_count if case_count else 0.0,
        "cases": evaluations,
    }
