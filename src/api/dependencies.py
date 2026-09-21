from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.config import Settings, get_settings
from src.database.session import get_session
from src.shared.models import UserContext

SettingsDependency = Annotated[Settings, Depends(get_settings)]
SessionDependency = Annotated[Session, Depends(get_session)]


def get_authenticated_user(
    x_user_id: Annotated[str | None, Header()] = None,
    x_role: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_employee_id: Annotated[str | None, Header()] = None,
    x_department_id: Annotated[str | None, Header()] = None,
    x_branch_id: Annotated[str | None, Header()] = None,
    x_team_id: Annotated[str | None, Header()] = None,
) -> UserContext:
    """Build identity from trusted auth middleware headers, never request JSON.

    Production authentication middleware must verify its credential and set
    these headers before this dependency runs. Tests can override the
    dependency directly without fabricating a request-body identity.
    """
    if not x_user_id or not x_role or not x_tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={
            'error_code': 'AUTHENTICATION_REQUIRED',
            'message': 'Authenticated user, role, and tenant identity are required.',
        })
    return UserContext(
        user_id=x_user_id, role=x_role, tenant_id=x_tenant_id,
        employee_id=x_employee_id, department_id=x_department_id, branch_id=x_branch_id,
        team_id=x_team_id,
    )


AuthenticatedUser = Annotated[UserContext, Depends(get_authenticated_user)]
