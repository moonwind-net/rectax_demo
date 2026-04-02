from sqlalchemy import BigInteger, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base, TimestampMixin


class AccountingFirm(Base, TimestampMixin):
    __tablename__ = "accounting_firms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="JP")

    client_companies: Mapped[list["ClientCompany"]] = relationship(back_populates="accounting_firm")


class ClientCompany(Base, TimestampMixin):
    __tablename__ = "client_companies"
    __table_args__ = (UniqueConstraint("accounting_firm_id", "code", name="uq_company_code_per_firm"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(14), nullable=True)
    industry_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="JPY")
    jpy_rounding_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tax_rounding_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    accounting_firm: Mapped[AccountingFirm] = relationship(back_populates="client_companies")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "accounting_firm_id",
            "client_company_id",
            "role",
            name="uq_membership",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
