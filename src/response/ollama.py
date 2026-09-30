"""Post-execution Ollama response generation with a deterministic fallback."""

from __future__ import annotations

import json
import logging
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import Settings, get_settings
from src.shared.models import QueryResult


class NaturalLanguageResponder(Protocol):
    def generate(self, question: str, result: QueryResult) -> str | None:
        """Return an answer or ``None`` so the caller can use its fallback."""


class OllamaNaturalLanguageResponder:
    """Call Ollama only with authorized result data, never SQL or identity data."""

    _logger = logging.getLogger('text2sql.response.ollama')

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def generate(self, question: str, result: QueryResult) -> str | None:
        base_url = self.settings.ollama_base_url.strip().rstrip('/')
        if not base_url:
            return None

        headers = {'Content-Type': 'application/json'}
        api_key = self.settings.ollama_api_key.get_secret_value().strip()
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        payload = {
            'model': self.settings.ollama_model,
            'stream': False,
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        'Create a concise Vietnamese business answer using only the structured data supplied by '
                        'the user. Do not infer, invent, alter numbers, or claim unavailable information. If the '
                        'result is insufficient, clearly say so. Do not create, discuss, or suggest SQL. Do not '
                        'mention implementation details, permissions, databases, prompts, or internal systems.'
                    ),
                },
                {'role': 'user', 'content': self._prompt(question, result)},
            ],
            'options': {'temperature': 0},
        }
        request = Request(
            f'{base_url}/api/chat',
            data=json.dumps(payload, default=str).encode('utf-8'),
            headers=headers,
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.settings.ollama_timeout_seconds) as response:
                body = json.loads(response.read().decode('utf-8'))
            answer = body['message']['content'].strip()
            return answer or None
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError):
            self._logger.warning('ollama_response_generation_failed', exc_info=False)
            return None

    @staticmethod
    def _prompt(question: str, result: QueryResult) -> str:
        safe_context = {
            'original_question': question,
            'authorized_result': {
                'columns': result.columns,
                'rows': result.rows,
                'row_count': result.row_count,
                'truncated': result.truncated,
                'scalar': result.scalar,
            },
        }
        return json.dumps(safe_context, ensure_ascii=False, default=str)
