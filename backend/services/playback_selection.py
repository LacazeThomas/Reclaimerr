"""Configuration-based selection of one durable history source per server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import ServiceConfig
from backend.enums import Service

MEDIA_SERVICES = {Service.PLEX, Service.JELLYFIN, Service.EMBY}


@dataclass(frozen=True, slots=True)
class HistorySource:
    server_id: int
    server_service: Service
    server_name: str
    preference: str
    provider: Service | None
    provider_config_id: int | None
    remote_server_id: str | None = None
    error: str | None = None

    @property
    def provider_key(self) -> str | None:
        if self.provider in {Service.JELLYFIN, Service.EMBY}:
            return "playback_reporting"
        return self.provider.value if self.provider is not None else None

    def event_matches(self, event: Any) -> bool:
        return (
            self.error is None
            and event.source_service == self.provider
            and event.source_service_config_id == self.provider_config_id
            and event.observed_service in {None, self.server_service}
            and event.observed_service_config_id in {None, self.server_id}
            and (
                self.remote_server_id is None
                or event.source_event_key.startswith(f"{self.remote_server_id}:")
            )
        )


def tautulli_server_id(
    config: ServiceConfig, configs: list[ServiceConfig]
) -> int | None:
    settings = config.extra_settings or {}
    if "plex_service_config_id" in settings:
        value = settings["plex_service_config_id"]
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    # Compatibility for pre-migration clients and fixtures; an explicit null
    # remains unbound rather than being silently reassigned.
    plex = [c for c in configs if c.enabled and c.service_type == Service.PLEX]
    main = next((c for c in plex if c.is_main), None)
    return main.id if main else plex[0].id if len(plex) == 1 else None


def resolve_history_sources(configs: list[ServiceConfig]) -> dict[int, HistorySource]:
    """Resolve by configuration, never by refresh success or matching events."""
    enabled = [config for config in configs if config.enabled]
    result: dict[int, HistorySource] = {}
    for server in enabled:
        if server.service_type not in MEDIA_SERVICES:
            continue
        preference = (server.extra_settings or {}).get(
            "playback_history_provider", "automatic"
        )
        candidates: dict[str, tuple[Service, int, str | None]] = {}
        for config in enabled:
            if config.service_type == Service.TRACEARR:
                bindings = (config.extra_settings or {}).get("server_bindings", [])
                for binding in bindings if isinstance(bindings, list) else []:
                    if not isinstance(binding, dict):
                        continue
                    if (
                        binding.get("service_config_id") == server.id
                        and binding.get("server_type") == server.service_type.value
                        and binding.get("tracearr_server_id")
                    ):
                        candidates["tracearr"] = (
                            Service.TRACEARR,
                            config.id,
                            str(binding["tracearr_server_id"]),
                        )
            elif (
                server.service_type == Service.PLEX
                and config.service_type == Service.TAUTULLI
                and tautulli_server_id(config, configs) == server.id
            ):
                candidates["tautulli"] = (Service.TAUTULLI, config.id, None)
        if server.service_type in {Service.JELLYFIN, Service.EMBY}:
            candidates["playback_reporting"] = (server.service_type, server.id, None)
        chosen = preference if isinstance(preference, str) else "invalid"
        if chosen == "automatic":
            chosen = (
                "tracearr"
                if "tracearr" in candidates
                else (
                    "tautulli"
                    if server.service_type == Service.PLEX
                    else "playback_reporting"
                )
            )
        provider, config_id, remote_id = candidates.get(chosen, (None, None, None))
        result[server.id] = HistorySource(
            server.id,
            server.service_type,
            server.name,
            preference,
            provider,
            config_id,
            remote_id,
            None
            if provider is not None
            else f"{chosen} history is not configured for {server.name}",
        )
    return result


async def load_history_sources(db: AsyncSession) -> dict[int, HistorySource]:
    configs = list((await db.scalars(select(ServiceConfig))).all())
    return resolve_history_sources(configs)


class ActivePlaybackSources(set[tuple[int, Service, str]]):
    """All bound Tracearr completion evidence plus authoritative activity sources.

    Retaining the set interface keeps completion-evidence consumers compatible;
    activity consumers use selections to enforce server-specific precedence.
    """

    def __init__(
        self,
        bindings: set[tuple[int, Service, str]],
        selections: dict[int, HistorySource],
    ) -> None:
        super().__init__(bindings)
        self.selections = selections
