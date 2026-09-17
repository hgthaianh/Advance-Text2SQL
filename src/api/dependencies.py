from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from src.config import Settings, get_settings
from src.database.session import get_session

SettingsDependency = Annotated[Settings, Depends(get_settings)]
SessionDependency = Annotated[Session, Depends(get_session)]

# TODO: resolve UserContext from authenticated identity, not request claims.
