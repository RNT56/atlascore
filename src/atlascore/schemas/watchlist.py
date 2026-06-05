"""The alert watchlist — git-tracked governed config (B18; code-not-data, ADR 0013/0029).

Gates the `cve_watch` and `sec_filing` rules so they fire only on *watched* items instead of every
item. Lives in a committed `watchlist.yaml` (no secrets, no endpoints) rather than a DB table or
`source.config.params` (which is `dict[str,str]` and can't hold lists/floats, and a watchlist is
cross-source policy, not per-source). An absent/inactive watchlist preserves the pre-B18
fire-on-everything behavior (backward compatible).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_zeros(cik: str) -> str:
    """Normalize a CIK so '0000320193' and '320193' compare equal."""
    return cik.strip().lstrip("0") or "0"


class CveWatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_cvss: float | None = Field(default=None, ge=0, le=10)
    severities: list[str] = Field(default_factory=list)
    cve_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("severities")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]

    @property
    def is_active(self) -> bool:
        return bool(self.min_cvss is not None or self.severities or self.cve_ids or self.keywords)


class SecWatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ciks: list[str] = Field(default_factory=list)
    forms: list[str] = Field(default_factory=list)

    @field_validator("ciks")
    @classmethod
    def _norm_ciks(cls, v: list[str]) -> list[str]:
        return [_strip_zeros(c) for c in v]

    @field_validator("forms")
    @classmethod
    def _upper_forms(cls, v: list[str]) -> list[str]:
        return [f.strip().upper() for f in v]

    @property
    def is_active(self) -> bool:
        return bool(self.ciks or self.forms)

    def cik_matches(self, cik: str | None) -> bool:
        return cik is not None and _strip_zeros(str(cik)) in self.ciks


class Watchlist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    cve: CveWatch = Field(default_factory=CveWatch)
    sec: SecWatch = Field(default_factory=SecWatch)

    @classmethod
    def empty(cls) -> Watchlist:
        return cls(enabled=True, cve=CveWatch(), sec=SecWatch())

    @property
    def is_active(self) -> bool:
        """True only when enabled AND at least one criterion is set (else rules fire on everything)."""
        return self.enabled and (self.cve.is_active or self.sec.is_active)
