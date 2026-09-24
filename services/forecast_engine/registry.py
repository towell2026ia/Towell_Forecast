"""Published Champion state, independent of the storage implementation."""

from __future__ import annotations

from typing import Any

from services.assistant_api.persistence import PersistenceProvider


class ChampionRegistry:
    def __init__(self, persistence: PersistenceProvider):
        self.persistence = persistence

    @staticmethod
    def key(chain_id: str, objective: str, scope: str) -> str:
        if not all((chain_id, objective, scope)):
            raise ValueError("invalid_champion_scope")
        return f"{chain_id}|{objective}|{scope}"

    def current(self, chain_id: str, objective: str, scope: str) -> dict[str, Any] | None:
        return self.persistence.get("champion_registry", self.key(chain_id, objective, scope))

    def publish(self, chain_id: str, objective: str, scope: str, candidate: dict[str, Any],
                *, actor: str | None, authorized: bool = False) -> dict[str, Any]:
        if not authorized or not actor:
            raise PermissionError("champion_publication_requires_authorization")
        if candidate.get("certification_status") != "CERTIFIED":
            raise ValueError("uncertified_champion")
        previous = self.current(chain_id, objective, scope)
        result = {"chain_id": chain_id, "objective": objective, "scope": scope,
                  "strategy": candidate["strategy"],
                  "statistical_weight": candidate["statistical_weight"],
                  "version": candidate["version"],
                  "certified_wape": candidate["certified_wape"], "published_by": actor,
                  "previous_version": previous.get("version") if previous else None}
        self.persistence.put("champion_registry", self.key(chain_id, objective, scope), result)
        return result
