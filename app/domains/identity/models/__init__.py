"""Identity domain ORM exports.

``User`` is the relationship hub for the whole app — importing this package
ensures ``User``, ``FamilyMember``, and ``LinkedAccount`` are all registered
together so SQLAlchemy can resolve relationship strings.
"""

from app.domains.identity.models.family_member import FamilyMember  # noqa: F401
from app.domains.identity.models.linked_account import LinkedAccount  # noqa: F401
from app.domains.identity.models.user import User  # noqa: F401

__all__ = ["FamilyMember", "LinkedAccount", "User"]
