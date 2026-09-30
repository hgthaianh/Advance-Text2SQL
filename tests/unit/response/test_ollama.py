from pydantic import SecretStr
from unittest.mock import patch

from src.config import Settings
from src.response.ollama import OllamaNaturalLanguageResponder
from src.shared.models import QueryResult


def _settings() -> Settings:
    return Settings(
        auth_secret_key=SecretStr('x' * 32),
        ollama_base_url='http://host.docker.internal:11434',
        ollama_model='gemma4:31b-cloud',
        ollama_api_key=SecretStr('cloud-secret'),
    )


def test_ollama_prompt_contains_only_question_and_authorized_result() -> None:
    result = QueryResult(
        columns=['total'], rows=[{'total': 7}], row_count=1, scalar=7,
        sql='SELECT secret_column FROM payroll', params={'employee_id': 'private-value'},
    )
    prompt = OllamaNaturalLanguageResponder(_settings())._prompt('Có bao nhiêu nhân viên?', result)

    assert 'Có bao nhiêu nhân viên?' in prompt
    assert '"total": 7' in prompt
    assert 'SELECT secret_column' not in prompt
    assert 'private-value' not in prompt
    assert 'employee_id' not in prompt


def test_ollama_without_configured_url_uses_fallback_signal() -> None:
    settings = Settings(auth_secret_key=SecretStr('x' * 32), ollama_base_url='')
    assert OllamaNaturalLanguageResponder(settings).generate('test', QueryResult()) is None


def test_ollama_parses_a_valid_natural_language_response() -> None:
    class Response:
        def read(self) -> bytes:
            return b'{"message":{"content":"Co 7 ket qua."}}'

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    with patch('src.response.ollama.urlopen', return_value=Response()) as call:
        answer = OllamaNaturalLanguageResponder(_settings()).generate('bao nhieu?', QueryResult(row_count=7))
    assert answer == 'Co 7 ket qua.'
    assert call.call_args.kwargs['timeout'] == 10
    assert call.call_args.args[0].full_url == 'http://host.docker.internal:11434/api/chat'
    assert call.call_args.args[0].get_header('Authorization') == 'Bearer cloud-secret'


def test_pipeline_response_provider_failure_falls_back_without_failing_execution() -> None:
    from src.pipeline.orchestrator import Text2SQLPipeline

    class BrokenResponder:
        def generate(self, _question, _result):
            raise RuntimeError('provider unavailable')

    pipeline = Text2SQLPipeline(responder=BrokenResponder())
    assert pipeline._generate_response('bao nhieu?', QueryResult(row_count=1)) is None
