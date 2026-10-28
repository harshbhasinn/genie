"""The calculator's one read: `calculator-inputs`, as the user, with the rates half cached.

Availability is read fresh every time - holds come and go every 10 minutes. The rates half is
kept in the shared store under the client and brand, and sent back as `knownRatesVersion`; the
server then omits the rates when they have not changed. A cached copy is only ever used inside a
read that has just succeeded with the user's own token, so it is never shown to someone the BFF
would refuse (CALCULATOR-INPUTS-API.md §6.2).

The endpoint is not in the BFF's spec yet, so it is not in ontology/endpoints.yaml either (whose
test checks every entry against the spec). Until it is, `fetch` raises InputsUnavailable and the
calculator says so. When the spec carries the path: add the entry to endpoints.yaml and replace
the lookup below with endpoint(INPUTS_ENDPOINT).
"""
from __future__ import annotations

import logging
from datetime import date

from ..bff import BffClient
from ..bff.endpoints import endpoints
from ..store import KeyValue
from .inputs import Inputs, InputsError, Rates, parse

log = logging.getLogger(__name__)

INPUTS_ENDPOINT = "campaigns.calculator_inputs"
RATES_TTL_S = 6 * 60 * 60          # bounds memory only; the version check keeps it right


class InputsUnavailable(Exception):
    """The BFF does not serve calculator-inputs yet."""


def rates_key(prefix: str, client_id: str, brand_id: str) -> str:
    return f"{prefix}calc:rates:{client_id}:{brand_id}"


async def _held(kv: KeyValue, key: str) -> Rates | None:
    raw = await kv.get(key)
    if raw is None:
        return None
    try:
        return Rates.from_json(raw)
    except (ValueError, KeyError, TypeError):
        log.warning("dropping an unreadable cached rate matrix")
        await kv.delete(key)
        return None


async def fetch(bff: BffClient, kv: KeyValue, prefix: str, token: str, *, client_id: str,
                brand_id: str, start: date, end: date, time_slots: tuple[str, ...],
                cities: tuple[str, ...] | None = None,
                shop_types: tuple[str, ...] | None = None) -> Inputs:
    path = endpoints().get(INPUTS_ENDPOINT)
    if path is None:
        raise InputsUnavailable("the BFF does not serve calculator-inputs yet")

    key = rates_key(prefix, client_id, brand_id)
    held = await _held(kv, key)
    params: dict = {"clientId": client_id, "advertisedBrandId": brand_id,
                    "startDate": start.isoformat(), "endDate": end.isoformat(),
                    "deviceTier": "NON_PREMIUM", "timeSlots": list(time_slots)}
    if cities:
        params["cities"] = list(cities)
    if shop_types:
        params["shopTypes"] = list(shop_types)

    async def read(known: Rates | None) -> Inputs:
        query = {**params, **({"knownRatesVersion": known.version} if known else {})}
        data = (await bff.get(path, token=token, params=query)).data
        if not isinstance(data, dict):
            raise InputsError("calculator-inputs answered with no data")
        inputs = parse(data, cached=known)
        if not data.get("ratesUnchanged"):
            await kv.set(key, inputs.rates.to_json(), RATES_TTL_S)
        return inputs

    try:
        return await read(held)
    except InputsError:
        if held is None:
            raise
        # The server kept the rates back for a version we do not hold after all (a race with
        # another task's write). Ask once more for everything, and keep what comes back.
        log.info("cached rates did not match the server's version; reading them in full")
        return await read(None)
