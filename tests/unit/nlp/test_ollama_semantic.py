import json
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from src.config import Settings
from src.nlp.ollama_semantic import OllamaSemanticReviewer
from src.query.ir import QueryIR
from src.shared.exceptions import AmbiguousQueryError
from src.shared.models import PreprocessedQuery


def _settings(base_url: str = 'https://ollama.example') -> Settings:
    return Settings(
        auth_secret_key=SecretStr('x' * 32),
        ollama_base_url=base_url,
        ollama_model='gemma4:31b-cloud',
        ollama_api_key=SecretStr('cloud-secret'),
    )


def _input() -> tuple[PreprocessedQuery, QueryIR]:
    preprocessed = PreprocessedQuery(
        original_text='doanh thu tổng của quý này',
        normalized_text='doanh thu tổng của quý này',
        accent_stripped_text='doanh thu tong cua quy nay',
    )
    query = QueryIR(
        intent='SUM', tables=['sales.orders'],
        select=[{'column': 'sales.orders.total_amount', 'aggregation': 'SUM'}],
    )
    return preprocessed, query


class _Response:
    def __init__(self, review: dict) -> None:
        self.body = json.dumps({'message': {'content': json.dumps(review)}}).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_semantic_reviewer_rejects_high_confidence_mismatch() -> None:
    preprocessed, query = _input()
    response = _Response({'consistent': False, 'confidence': 0.96, 'reason_code': 'time_mismatch'})

    with patch('src.nlp.ollama_semantic.urlopen', return_value=response) as call:
        with pytest.raises(AmbiguousQueryError, match='time mismatch'):
            OllamaSemanticReviewer(_settings()).validate(preprocessed.original_text, preprocessed, query)

    request = call.call_args.args[0]
    payload = json.loads(request.data)
    assert payload['format'] == 'json'
    assert request.get_header('Authorization') == 'Bearer cloud-secret'
    assert 'nearest_covered_examples' in payload['messages'][1]['content']
    assert 'sql' not in payload['messages'][1]['content'].lower()


def test_semantic_reviewer_fails_open_when_ollama_is_disabled() -> None:
    preprocessed, query = _input()
    with patch('src.nlp.ollama_semantic.urlopen') as call:
        OllamaSemanticReviewer(_settings('')).validate(preprocessed.original_text, preprocessed, query)
    call.assert_not_called()
