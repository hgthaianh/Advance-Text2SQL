import pytest
from fastapi import HTTPException

from src.api.dependencies import require_observability_admin
from src.shared.models import UserContext


def test_observability_requires_an_administrator_role():
    with pytest.raises(HTTPException) as exc_info:
        require_observability_admin(UserContext(user_id='viewer', role='viewer', tenant_id='T1'))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['error_code'] == 'PERMISSION_DENIED'


def test_observability_allows_an_admin():
    user = UserContext(user_id='admin', role='admin', tenant_id='T1')

    assert require_observability_admin(user) is user
