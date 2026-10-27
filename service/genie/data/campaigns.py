"""Campaigns, read live as the user and turned into facts.

Field ALLOWLIST, as for the wallet: only the fields named here are read. The
full-detail response also carries adgridCost and a cost breakdown — the
AdGrid-to-agency leg — which stays out for the same reason as on the wallet.

Sources (live spec; /reach and /spend are also what the AdOps dashboard on QA calls):
  list, state, dates, cost, scope   campaigns.list         ?clientId=  campaigns[], totalElements
  one campaign                      campaigns.detail
  campaign_impressions              campaigns.impressions  totalImpressions
  campaign_reach                    campaigns.reach        reachForRange
  campaign_spend                    campaigns.spend        spendForRange
  campaign_time_elapsed             computed here from startDate, endDate and today
  by city / store type / daypart    campaigns.breakdown    ?groupBy=
  by hour of the day                campaigns.dayparts

groupBy is CITY, CATEGORY or DAYPART (CampaignPerformanceBreakdownServiceImpl on QA;
the dashboard sends CITY). A breakdown row carries spend and reach too, but those are
the plan (below), so only its impressions and screens are read.

No date range is sent: the backend's default window is used and echoed back
from the response's own from/to, so the answer always says which days it covers.

Reach and spend are the PLAN, by the contract: reach is totalDeliverableReach
with one flat dailyReach, and spend is spendForRange with one flat dailySpend -
the booking's audience and the cost schedule, not measured delivery. Only
impressions are a measurement. To confirm with the backend (asks/BACKEND-CHECKS.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..bff import BffClient, BffError
from ..bff.endpoints import endpoint
from .approvals import STATUS_LABEL as APPROVAL_STATUS
from .fields import day, humanize, parse_date, to_int, window_of
from .fields import today as today_ist
from .money import count, count_short, inr, inr_short, to_decimal
from .wallet import LiveFact

LIST = endpoint("campaigns.list")
DETAIL = endpoint("campaigns.detail")
IMPRESSIONS = endpoint("campaigns.impressions")
REACH = endpoint("campaigns.reach")
SPEND = endpoint("campaigns.spend")
GENDER = endpoint("campaigns.gender")
BREAKDOWN = endpoint("campaigns.breakdown")
DAYPART = endpoint("campaigns.dayparts")
# The campaign's own page - the ontology's campaign_detail route (a test holds them equal).
CAMPAIGN_ROUTE = "/campaign/{campaignId}"

PAGE_SIZE = 100

# effectiveStatus as the user reads it. "Live", never "Active" (catalog gotcha).
STATE_LABEL = {"LIVE": "Live", "PAUSED": "Paused", "NOT_PLAYING": "Not playing",
               "SCHEDULED": "Scheduled", "COMPLETED": "Completed",
               "CANCELLED": "Cancelled", "DRAFT": "Draft"}
# Running, or meant to be: what "my campaign" most likely means.
RUNNING = frozenset({"LIVE", "PAUSED", "NOT_PLAYING"})
NOT_STARTED = frozenset({"DRAFT", "SCHEDULED"})


@dataclass(slots=True)
class Campaign:
    id: str
    name: str
    state: str                  # effectiveStatus, falling back to status
    not_playing_reason: str | None
    start: date | None
    end: date | None
    total_cost: Decimal | None
    screens: int | None
    stores: int | None
    cities: int | None
    # (reference, status) of the campaign's approval request: approvalReferenceCode and
    # approvalStatus on the row (live QA 2026-10-24). approval_known is False only for a
    # row without those fields, and then the responder looks the request up instead.
    approval: tuple[str, str] | None = None
    approval_known: bool = False

    @property
    def state_label(self) -> str:
        return STATE_LABEL.get(self.state) or (humanize(self.state) or "").capitalize()

    @classmethod
    def from_payload(cls, d: dict) -> "Campaign":
        scope = d.get("scope") or {}
        state = str(d.get("effectiveStatus") or d.get("status") or "UNKNOWN").upper()
        return cls(id=str(d.get("campaignId") or ""), name=str(d.get("name") or "Unnamed campaign"),
                   state=state,
                   not_playing_reason=d.get("notPlayingReason") or None,
                   start=parse_date(d.get("startDate")), end=parse_date(d.get("endDate")),
                   total_cost=to_decimal(d.get("totalCost")), screens=to_int(scope.get("devices")),
                   stores=to_int(scope.get("store")), cities=to_int(scope.get("cities")),
                   approval=((str(d["approvalReferenceCode"]),
                              APPROVAL_STATUS.get(str(d.get("approvalStatus") or "").upper(),
                                                  "pending"))
                             if d.get("approvalReferenceCode") else None),
                   approval_known="approvalStatus" in d or "approvalReferenceCode" in d)

    @property
    def route(self) -> str | None:
        """The campaign's own page (ontology: campaign_detail)."""
        return CAMPAIGN_ROUTE.format(campaignId=self.id) if self.id else None

    def as_meta(self) -> dict:
        return {"id": self.id, "name": self.name, "state": self.state_label, "route": self.route,
                "approval": f"{self.approval[1]} ({self.approval[0]})" if self.approval else None,
                "start": day(self.start) if self.start else None,
                "end": day(self.end) if self.end else None,
                "total_cost": inr(self.total_cost) if self.total_cost is not None else None}


@dataclass(slots=True)
class CampaignList:
    campaigns: list[Campaign] = field(default_factory=list)
    total: int | None = None            # totalElements: the count, not one page's rows
    problem: str | None = None          # BffError kind, if the list could not be read

    @property
    def partial(self) -> bool:
        return self.total is not None and self.total > len(self.campaigns)

    def by_state(self) -> dict[str, list[Campaign]]:
        out: dict[str, list[Campaign]] = {}
        for c in self.campaigns:
            out.setdefault(c.state, []).append(c)
        return out

    def find(self, campaign_id: str) -> Campaign | None:
        return next((c for c in self.campaigns if c.id == campaign_id), None)


@dataclass(slots=True)
class CampaignView:
    campaign: Campaign
    facts: list[LiveFact] = field(default_factory=list)
    window: tuple[date, date] | None = None     # the days the delivery figures cover
    today: date = field(default_factory=today_ist)
    problems: dict[str, str] = field(default_factory=dict)

    def fact(self, metric_id: str) -> LiveFact | None:
        return next((f for f in self.facts if f.metric_id == metric_id), None)


async def list_campaigns(bff: BffClient, token: str, client_id: str) -> CampaignList:
    try:
        data = (await bff.get(LIST, token=token, params={
            "clientId": client_id, "page": 0, "size": PAGE_SIZE})).data or {}
    except BffError as err:
        return CampaignList(problem=err.kind)
    rows = [Campaign.from_payload(r) for r in data.get("campaigns") or [] if isinstance(r, dict)]
    rows.sort(key=lambda c: c.start or date.min, reverse=True)      # newest first
    return CampaignList(campaigns=rows, total=to_int(data.get("totalElements")))


async def one_campaign(bff: BffClient, token: str, campaign_id: str) -> Campaign | str:
    """The campaign, or the BffError kind explaining why not."""
    try:
        data = (await bff.get(DETAIL, token=token, path_params={"campaignId": campaign_id})).data
    except BffError as err:
        return err.kind
    return Campaign.from_payload(data or {})


def flight_days(c: Campaign, today: date) -> tuple[int, int] | None:
    """(days run so far, days in the flight), counting both end days."""
    if not (c.start and c.end) or c.end < c.start:
        return None
    flight = (c.end - c.start).days + 1
    return min(max((today - c.start).days + 1, 0), flight), flight


def time_elapsed(c: Campaign, today: date) -> LiveFact | None:
    days = flight_days(c, today)
    if days is None:
        return None
    done, flight = days
    pct = round(100 * done / flight)
    return LiveFact("campaign_time_elapsed", "Time elapsed", Decimal(pct),
                    f"Day {done} of {flight} ({pct}%)", None,
                    "computed · startDate, endDate and today")


async def campaign_view(bff: BffClient, token: str, c: Campaign, *,
                        today: date | None = None) -> CampaignView:
    """One campaign's state and delivery, read in parallel."""
    today = today or today_ist()
    view = CampaignView(campaign=c, today=today)
    reason = c.not_playing_reason if c.state == "NOT_PLAYING" else None
    view.facts.append(LiveFact("campaign_state", "Status", c.state,
                               c.state_label + (f" — {reason}" if reason else ""),
                               None, f"{LIST} · effectiveStatus"))
    if c.state not in NOT_STARTED and (elapsed := time_elapsed(c, today)) is not None:
        view.facts.append(elapsed)
    for metric_id, label, n in (("campaign_active_screens", "Screens", c.screens),
                                ("campaign_stores", "Stores", c.stores),
                                ("campaign_cities", "Cities", c.cities)):
        if n is not None:
            view.facts.append(LiveFact(metric_id, label, n, count(n), None, f"{LIST} · scope"))

    if c.state in NOT_STARTED or not c.id:
        return view                      # nothing has played, so there is nothing to read
    got, view.problems = await bff.get_all(
        token, {ep: {"path_params": {"campaignId": c.id}} for ep in (IMPRESSIONS, REACH, SPEND)})
    view.window = next((w for body in got.values() if (w := window_of(body))), None)

    if (imp := to_int((got.get(IMPRESSIONS) or {}).get("totalImpressions"))) is not None:
        view.facts.append(LiveFact("campaign_impressions", "Impressions", imp, count(imp),
                                   count_short(imp), f"{IMPRESSIONS} · totalImpressions"))
    # Reach and spend are the PLAN (see the module docstring), so they are labelled
    # as the plan, never as delivery.
    if (reach := to_int((got.get(REACH) or {}).get("totalDeliverableReach"))) is not None:
        view.facts.append(LiveFact("campaign_reach", "Planned reach", reach, count(reach),
                                   count_short(reach), f"{REACH} · totalDeliverableReach"))
    spend_body = got.get(SPEND) or {}
    spend = to_decimal(spend_body.get("spendForRange"))
    if spend is not None:
        cost = c.total_cost if c.total_cost is not None else to_decimal(spend_body.get("totalCost"))
        whole = cost is not None and spend == cost
        view.facts.append(LiveFact("campaign_spend", "Cost to date", spend,
                                   inr(spend) + ("" if whole or cost is None else f" of {inr(cost)}"),
                                   inr_short(spend), f"{SPEND} · spendForRange, totalCost"))
    return view


@dataclass(slots=True)
class GenderSplit:
    """A campaign's IMPRESSIONS split by gender - plays, not people."""
    shares: list[tuple[str, Decimal]] = field(default_factory=list)  # label, % of impressions
    total: int | None = None
    window: tuple[date, date] | None = None
    problem: str | None = None


async def campaign_gender(bff: BffClient, token: str, c: Campaign) -> GenderSplit:
    try:
        data = (await bff.get(GENDER, token=token, path_params={"campaignId": c.id})).data or {}
    except BffError as err:
        return GenderSplit(problem=err.kind)
    out = GenderSplit(total=to_int(data.get("totalImpressions")), window=window_of(data))
    for g in data.get("genders") or []:
        share = to_decimal((g or {}).get("sharePct"))
        if share is None:
            continue
        label = str(g.get("label") or g.get("gender") or "Unknown")
        out.shares.append((label, share))
    out.shares.sort(key=lambda x: x[1], reverse=True)
    return out


@dataclass(slots=True)
class Breakdown:
    """A campaign's IMPRESSIONS split by city, store type or daypart."""
    group: str
    rows: list[tuple[str, int | None, Decimal | None, int | None]] = field(default_factory=list)
    total: int | None = None                                  # label, plays, share %, screens
    window: tuple[date, date] | None = None
    problem: str | None = None


async def campaign_breakdown(bff: BffClient, token: str, c: Campaign, group: str) -> Breakdown:
    try:
        data = (await bff.get(BREAKDOWN, token=token, path_params={"campaignId": c.id},
                              params={"groupBy": group})).data or {}
    except BffError as err:
        return Breakdown(group, problem=err.kind)
    out = Breakdown(group, total=to_int(data.get("totalImpressions")), window=window_of(data))
    for r in data.get("rows") or []:
        if isinstance(r, dict):
            out.rows.append((str(r.get("label") or r.get("key") or "Unknown"),
                             to_int(r.get("impressions")),
                             to_decimal(r.get("sharePct")), to_int(r.get("screens"))))
    out.rows.sort(key=lambda r: r[1] or 0, reverse=True)
    return out


@dataclass(slots=True)
class Dayparts:
    """IMPRESSIONS by daypart, each with its busiest hour."""
    slots: list[tuple[str, int | None, Decimal | None, int | None]] = field(default_factory=list)
    total: int | None = None                                  # label, plays, share %, peak hour
    outside: int | None = None                                # plays outside any daypart
    window: tuple[date, date] | None = None
    problem: str | None = None


async def campaign_dayparts(bff: BffClient, token: str, c: Campaign) -> Dayparts:
    try:
        data = (await bff.get(DAYPART, token=token, path_params={"campaignId": c.id})).data or {}
    except BffError as err:
        return Dayparts(problem=err.kind)
    out = Dayparts(total=to_int(data.get("totalImpressions")),
                   outside=to_int(data.get("outsideDaypartImpressions")),
                   window=window_of(data))
    for d in data.get("dayparts") or []:
        if not isinstance(d, dict):
            continue
        hours = [h for h in d.get("hours") or []
                 if isinstance(h, dict) and to_int(h.get("impressions"))]
        peak = max(hours, key=lambda h: to_int(h.get("impressions")) or 0) if hours else None
        out.slots.append((str(d.get("timeSlot") or "Unknown"), to_int(d.get("impressions")),
                          to_decimal(d.get("sharePct")), to_int(peak.get("hour")) if peak else None))
    out.slots.sort(key=lambda s: s[1] or 0, reverse=True)
    return out


# ── what a draft still needs ────────────────────────────────────────────

@dataclass(slots=True)
class Creative:
    name: str | None
    zone: str | None          # HERO (image) or SHOWCASE (video)
    review: str | None        # AWAITING_AI_VERDICT, APPROVED, PENDING_REVIEW, REJECTED
    reason: str | None        # why it was rejected, when it was


@dataclass(slots=True)
class LaunchCheck:
    """The wizard's checklist, read from the campaign itself: screens chosen, a creative in
    every requested slot, every creative approved."""
    image_slots: int = 0
    video_slots: int = 0
    has_screens: bool = False
    creatives: list[Creative] = field(default_factory=list)
    problem: str | None = None


async def launch_check(bff: BffClient, token: str, c: Campaign) -> LaunchCheck:
    """Reads the campaign's slots, selections and creatives - not its costs (adgridCost and
    the cost breakdown are the AdGrid-to-partner leg, never read)."""
    try:
        d = (await bff.get(DETAIL, token=token, path_params={"campaignId": c.id})).data or {}
    except BffError as err:
        return LaunchCheck(problem=err.kind)
    return LaunchCheck(
        image_slots=to_int(d.get("imageSlotsRequested")) or 0,
        video_slots=to_int(d.get("videoSlotsRequested")) or 0,
        # screens chosen by city and store type, store by store, or - for a targeted
        # campaign, which books no screens - as the cities and store types it targets
        has_screens=bool(d.get("deviceSelections") or d.get("storeSelections") or d.get("targets")),
        creatives=[Creative(x.get("fileName") or None, x.get("zone") or None, x.get("reviewStatus") or None,
                            x.get("reviewDecisionReason") or x.get("aiMessage") or None)
                   for x in d.get("creatives") or [] if isinstance(x, dict)])
