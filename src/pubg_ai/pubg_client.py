from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


PUBG_API_BASE_URL = "https://api.pubg.com"
MAX_PLAYER_LOOKUP_NAMES = 10
MAX_PLAYER_LOOKUP_IDS = 10


class PubgApiError(RuntimeError):
    """Raised when the PUBG Open API returns an error or unexpected payload."""


@dataclass(frozen=True)
class PubgRateLimit:
    limit: int | None = None
    remaining: int | None = None
    reset_epoch: int | None = None

    def to_record(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(frozen=True)
class PubgPlayer:
    account_id: str
    name: str
    shard: str

    def to_record(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PubgPlayerSnapshot:
    account_id: str
    name: str
    shard: str
    match_ids: list[str]
    raw_payload: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "shard": self.shard,
            "match_ids": list(self.match_ids),
        }


@dataclass(frozen=True)
class PubgPlayerLookupResult:
    players: list[PubgPlayer]
    rate_limit: PubgRateLimit

    def single(self, requested_name: str) -> PubgPlayer:
        if not self.players:
            raise PubgApiError(f"PUBG player not found: {requested_name}")
        if len(self.players) > 1:
            exact = [
                player
                for player in self.players
                if player.name.lower() == requested_name.lower()
            ]
            if len(exact) == 1:
                return exact[0]
            raise PubgApiError(f"PUBG player lookup returned multiple players for: {requested_name}")
        return self.players[0]


@dataclass(frozen=True)
class PubgPlayerRefreshResult:
    snapshots: list[PubgPlayerSnapshot]
    rate_limit: PubgRateLimit
    raw_payload: Mapping[str, Any]


class PubgApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = PUBG_API_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key.strip():
            raise PubgApiError("PUBG_API_KEY is required.")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def lookup_players_by_names(self, shard: str, player_names: list[str]) -> PubgPlayerLookupResult:
        import httpx

        shard = _required_text(shard, "shard").lower()
        names = [_required_text(name, "player name") for name in player_names]
        if len(names) > MAX_PLAYER_LOOKUP_NAMES:
            raise PubgApiError("PUBG player lookup supports at most 10 names per request.")

        url = f"{self.base_url}/shards/{shard}/players"
        try:
            response = httpx.get(
                url,
                params={"filter[playerNames]": ",".join(names)},
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise PubgApiError(f"PUBG API request failed: {exc.__class__.__name__}") from exc

        rate_limit = _rate_limit_from_headers(response.headers)
        if response.status_code == 404:
            return PubgPlayerLookupResult(players=[], rate_limit=rate_limit)
        if response.status_code >= 400:
            raise PubgApiError(f"PUBG API returned HTTP {response.status_code} for player lookup.")

        payload = response.json()
        players = parse_player_lookup_payload(payload, shard=shard)
        return PubgPlayerLookupResult(players=players, rate_limit=rate_limit)

    def lookup_player_by_name(self, shard: str, player_name: str) -> PubgPlayer:
        return self.lookup_players_by_names(shard, [player_name]).single(player_name)

    def refresh_players_by_ids(self, shard: str, account_ids: list[str]) -> PubgPlayerRefreshResult:
        import httpx

        shard = _required_text(shard, "shard").lower()
        ids = [_required_text(account_id, "account id") for account_id in account_ids]
        if len(ids) > MAX_PLAYER_LOOKUP_IDS:
            raise PubgApiError("PUBG player lookup supports at most 10 account IDs per request.")

        url = f"{self.base_url}/shards/{shard}/players"
        try:
            response = httpx.get(
                url,
                params={"filter[playerIds]": ",".join(ids)},
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise PubgApiError(f"PUBG API request failed: {exc.__class__.__name__}") from exc

        rate_limit = _rate_limit_from_headers(response.headers)
        if response.status_code == 404:
            return PubgPlayerRefreshResult(snapshots=[], rate_limit=rate_limit, raw_payload={"data": []})
        if response.status_code >= 400:
            raise PubgApiError(f"PUBG API returned HTTP {response.status_code} for player refresh.")

        payload = response.json()
        snapshots = parse_player_snapshot_payload(payload, shard=shard)
        return PubgPlayerRefreshResult(snapshots=snapshots, rate_limit=rate_limit, raw_payload=payload)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/vnd.api+json",
            "Accept-Encoding": "gzip",
        }


def parse_player_lookup_payload(payload: Mapping[str, Any], *, shard: str) -> list[PubgPlayer]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise PubgApiError("PUBG player lookup response is missing data list.")

    players: list[PubgPlayer] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        account_id = _optional_text(item.get("id"))
        attributes = item.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = {}
        name = _optional_text(attributes.get("name"))
        if account_id and name:
            players.append(PubgPlayer(account_id=account_id, name=name, shard=shard))
    return players


def parse_player_snapshot_payload(payload: Mapping[str, Any], *, shard: str) -> list[PubgPlayerSnapshot]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise PubgApiError("PUBG player refresh response is missing data list.")

    snapshots: list[PubgPlayerSnapshot] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        account_id = _optional_text(item.get("id"))
        attributes = item.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = {}
        name = _optional_text(attributes.get("name"))
        if not account_id or not name:
            continue
        snapshots.append(
            PubgPlayerSnapshot(
                account_id=account_id,
                name=name,
                shard=shard,
                match_ids=_match_ids_from_player_item(item),
                raw_payload=item,
            )
        )
    return snapshots


def _match_ids_from_player_item(item: Mapping[str, Any]) -> list[str]:
    relationships = item.get("relationships")
    if not isinstance(relationships, Mapping):
        return []
    matches = relationships.get("matches")
    if not isinstance(matches, Mapping):
        return []
    data = matches.get("data")
    if not isinstance(data, list):
        return []

    match_ids: list[str] = []
    seen = set()
    for match_ref in data:
        if not isinstance(match_ref, Mapping):
            continue
        match_id = _optional_text(match_ref.get("id"))
        if match_id and match_id not in seen:
            seen.add(match_id)
            match_ids.append(match_id)
    return match_ids


def _rate_limit_from_headers(headers: Mapping[str, str]) -> PubgRateLimit:
    return PubgRateLimit(
        limit=_optional_int(_header_value(headers, "X-RateLimit-Limit")),
        remaining=_optional_int(_header_value(headers, "X-RateLimit-Remaining")),
        reset_epoch=_optional_int(_header_value(headers, "X-RateLimit-Reset")),
    )


def _header_value(headers: Mapping[str, str], key: str) -> str | None:
    for header_key, value in headers.items():
        if header_key.lower() == key.lower():
            return value
    return None


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _required_text(value: str, label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise PubgApiError(f"{label} is required.")
    return stripped
