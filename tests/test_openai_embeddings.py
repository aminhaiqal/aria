from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase, override_settings

from aria.knowledge.embeddings import EmbeddingError, OpenAIEmbeddingProvider


@override_settings(
    EMBEDDING_DIMENSIONS=384,
    OPENAI_API_KEY="test-key",
    OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
)
class OpenAIEmbeddingProviderTestCase(SimpleTestCase):
    def test_batches_inputs_with_dimensions_and_preserves_response_order(self) -> None:
        first = [0.0] * 384
        first[0] = 1.0
        second = [0.0] * 384
        second[1] = 1.0
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=second),
                SimpleNamespace(index=0, embedding=first),
            ],
            usage=SimpleNamespace(prompt_tokens=7),
        )

        result = OpenAIEmbeddingProvider(client=client).embed_texts(["first", "second"])

        client.embeddings.create.assert_called_once_with(
            model="text-embedding-3-small",
            input=["first", "second"],
            dimensions=384,
            encoding_format="float",
        )
        self.assertEqual(result.vectors[0][0], 1.0)
        self.assertEqual(result.vectors[1][1], 1.0)
        self.assertEqual(result.prompt_tokens, 7)

    def test_rejects_wrong_vector_dimensions(self) -> None:
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.0] * 10)],
            usage=SimpleNamespace(prompt_tokens=1),
        )

        with self.assertRaisesMessage(EmbeddingError, "10 dimensions; expected 384"):
            OpenAIEmbeddingProvider(client=client).embed_texts(["invalid"])

    def test_rejects_invalid_response_indexes(self) -> None:
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(index=0, embedding=[0.0] * 384),
                SimpleNamespace(index=0, embedding=[0.0] * 384),
            ],
            usage=SimpleNamespace(prompt_tokens=2),
        )

        with self.assertRaisesMessage(EmbeddingError, "invalid response indexes"):
            OpenAIEmbeddingProvider(client=client).embed_texts(["first", "second"])
