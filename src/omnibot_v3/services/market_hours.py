"""Market-hours status for operator-facing dashboard visibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from omnibot_v3.domain.runtime import Market

_EASTERN = ZoneInfo("America/New_York")
_NZ = ZoneInfo("Pacific/Auckland")
_DISPLAY_TIMEZONE = "Pacific/Auckland"
_DISPLAY_TIMEZONE_LABEL = "NZT"


@dataclass(frozen=True, slots=True)
class MarketHoursStatus:
    market: Market
    label: str
    is_open: bool
    status: str
    detail: str
    next_transition_at: datetime | None
    timezone: str = _DISPLAY_TIMEZONE


class MarketHoursService:
    def get_payload(self, now: datetime | None = None) -> dict[str, object]:
        observed_at = now or datetime.now(UTC)
        statuses = [self.status_for(market, observed_at) for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)]
        return {
            "generated_at": observed_at.isoformat(),
            "markets": [self._to_payload(item) for item in statuses],
        }

    def status_for(self, market: Market, now: datetime | None = None) -> MarketHoursStatus:
        observed_at = now or datetime.now(UTC)
        eastern_now = observed_at.astimezone(_EASTERN)
        if market == Market.STOCKS:
            return _stocks_status(eastern_now)
        if market == Market.CRYPTO:
            return MarketHoursStatus(
                market=market,
                label="Crypto",
                is_open=True,
                status="open",
                detail="Crypto markets trade continuously.",
                next_transition_at=None,
            )
        return _forex_status(eastern_now)

    def _to_payload(self, status: MarketHoursStatus) -> dict[str, object]:
        return {
            "market": status.market.value,
            "label": status.label,
            "is_open": status.is_open,
            "status": status.status,
            "detail": status.detail,
            "next_transition_at": status.next_transition_at.isoformat() if status.next_transition_at else None,
            "timezone": status.timezone,
        }


def _stocks_status(now: datetime) -> MarketHoursStatus:
    open_at = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_at = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now.weekday() >= 5:
        next_open = _next_weekday_time(now, weekday=0, target=time(hour=9, minute=30))
        return MarketHoursStatus(
            market=Market.STOCKS,
            label="US Stocks",
            is_open=False,
            status="closed",
            detail=f"Closed. Next open {_format_nz_time(next_open, reference=now)}.",
            next_transition_at=next_open.astimezone(UTC),
        )
    if open_at <= now < close_at:
        return MarketHoursStatus(
            market=Market.STOCKS,
            label="US Stocks",
            is_open=True,
            status="open",
            detail=f"Open until {_format_nz_time(close_at, reference=now)}.",
            next_transition_at=close_at.astimezone(UTC),
        )
    if now < open_at:
        return MarketHoursStatus(
            market=Market.STOCKS,
            label="US Stocks",
            is_open=False,
            status="pre-market",
            detail=f"Pre-market. Opens {_format_nz_time(open_at, reference=now)}.",
            next_transition_at=open_at.astimezone(UTC),
        )
    next_open = _next_weekday_time(now + timedelta(days=1), weekday=(now.weekday() + 1) % 7, target=time(hour=9, minute=30))
    return MarketHoursStatus(
        market=Market.STOCKS,
        label="US Stocks",
        is_open=False,
        status="closed",
        detail=f"Closed. Next open {_format_nz_time(next_open, reference=now)}.",
        next_transition_at=next_open.astimezone(UTC),
    )


def _forex_status(now: datetime) -> MarketHoursStatus:
    weekday = now.weekday()
    friday_close = now.replace(hour=17, minute=0, second=0, microsecond=0)
    sunday_open = _next_sunday_open(now)
    if weekday == 4 and now >= friday_close:
        return MarketHoursStatus(
            market=Market.FOREX,
            label="Forex",
            is_open=False,
            status="closed",
            detail=f"Closed for weekend. Reopens {_format_nz_time(sunday_open, reference=now)}.",
            next_transition_at=sunday_open.astimezone(UTC),
        )
    if weekday == 5:
        return MarketHoursStatus(
            market=Market.FOREX,
            label="Forex",
            is_open=False,
            status="closed",
            detail=f"Closed for weekend. Reopens {_format_nz_time(sunday_open, reference=now)}.",
            next_transition_at=sunday_open.astimezone(UTC),
        )
    if weekday == 6 and now < now.replace(hour=17, minute=0, second=0, microsecond=0):
        reopen_at = now.replace(hour=17, minute=0, second=0, microsecond=0)
        return MarketHoursStatus(
            market=Market.FOREX,
            label="Forex",
            is_open=False,
            status="closed",
            detail=f"Closed for weekend. Reopens {_format_nz_time(reopen_at, reference=now)}.",
            next_transition_at=reopen_at.astimezone(UTC),
        )
    next_close = _next_friday_close(now)
    return MarketHoursStatus(
        market=Market.FOREX,
        label="Forex",
        is_open=True,
        status="open",
        detail=f"Open until {_format_nz_time(next_close, reference=now)}.",
        next_transition_at=next_close.astimezone(UTC),
    )


def _format_nz_time(moment: datetime, *, reference: datetime | None = None) -> str:
    local_moment = moment.astimezone(_NZ)
    include_day = False
    if reference is not None:
        include_day = local_moment.date() != reference.astimezone(_NZ).date()
    hour = str(int(local_moment.strftime("%I")))
    minute = local_moment.strftime("%M")
    suffix = local_moment.strftime("%p").lower()
    time_text = f"{hour}:{minute}{suffix}"
    if include_day:
        return f"{local_moment.strftime('%a')} {time_text} {_DISPLAY_TIMEZONE_LABEL}"
    return f"{time_text} {_DISPLAY_TIMEZONE_LABEL}"


def _next_weekday_time(now: datetime, *, weekday: int, target: time) -> datetime:
    candidate = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    while candidate.weekday() != weekday or candidate <= now:
        candidate += timedelta(days=1)
        candidate = candidate.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    return candidate


def _next_sunday_open(now: datetime) -> datetime:
    candidate = now.replace(hour=17, minute=0, second=0, microsecond=0)
    while candidate.weekday() != 6 or candidate <= now:
        candidate += timedelta(days=1)
        candidate = candidate.replace(hour=17, minute=0, second=0, microsecond=0)
    return candidate


def _next_friday_close(now: datetime) -> datetime:
    candidate = now.replace(hour=17, minute=0, second=0, microsecond=0)
    while candidate.weekday() != 4 or candidate <= now:
        candidate += timedelta(days=1)
        candidate = candidate.replace(hour=17, minute=0, second=0, microsecond=0)
    return candidate