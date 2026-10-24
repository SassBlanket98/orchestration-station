"""Read-only client for the local quota_service quota service.

The dashboard does not own quota state: it asks the quota_service daemon, which runs on the
same machine, for a fresh usage snapshot and a worker pick. The routes live in ``api.py``;
this module makes the upstream calls and maps their camelCase payloads onto the snake_case
allowlisted models in ``models.py`` (``usage_from_upstream``, ``pick_from_upstream``).

Security and shape rules:

* The upstream is fixed to the caller-supplied loopback URL. Anything that is not a
  loopback host is rejected, so a misconfiguration cannot send the bearer to a remote
  host or an attacker-controlled one.
* The bearer comes from the caller as a plain string. It is only ever put into the
  request's ``Authorization`` header. It is never stored on a result, never logged, and
  never included in ``repr``.
* Exactly two upstream calls exist: ``GET /api/pick/usage`` and
  ``GET /api/pick?job=&slices=&lead=&builder=``.
* Response bodies are streamed and capped at ``MAX_RESPONSE_BYTES``; an over-long body is
  reported as an error instead of being buffered whole.
* Every failure mode maps to one status. When the upstream cannot be trusted to answer
  (offline, timeout, malformed, oversized), no metrics are produced: ``used``, ``pace``,
  ``projected`` and ``score`` stay ``None`` rather than being invented.
* Only allowlisted fields leave this module. Upstream timestamps are Unix epoch
  milliseconds and stay in milliseconds; the dashboard's own ``fetched_at`` is seconds.
"""

import ipaddress
import json
import logging
import math
import os
import ssl
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from stagewatch.models import (
    PickResult,
    QuotaAssignment,
    QuotaCredit,
    QuotaFallback,
    QuotaLead,
    QuotaLimit,
    QuotaPickCandidate,
    QuotaSkip,
    QuotaSubscription,
    QuotaUsage,
    QuotaWindowRef,
)

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 5.0
MAX_RESPONSE_BYTES = 256 * 1024
USAGE_PATH = "/api/pick/usage"
PICK_PATH = "/api/pick"
_METRIC_KEYS = ("used", "pace", "projected", "score")


class QuotaStatus(StrEnum):
    """Outcome of one upstream call.

    ``online`` is the only status that carries data. ``bad_request`` is a 400 from the
    upstream and ``unavailable`` a 503; ``invalid_query`` is a local rejection made
    before any network call.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    BAD_REQUEST = "bad_request"
    UNAVAILABLE = "unavailable"
    INVALID_QUERY = "invalid_query"
    ERROR = "error"


@dataclass(frozen=True)
class QuotaResult:
    """One call's outcome. ``data`` and the metrics are set only for ``online``.

    The four metric fields mirror the small set the dashboard renders. They are read
    verbatim from the upstream payload when present and left ``None`` otherwise, so a
    stale or unreachable upstream never turns into a made-up number.
    """

    status: QuotaStatus
    data: dict[str, Any] | None = None
    detail: str | None = None
    http_status: int | None = None
    used: float | None = None
    pace: float | None = None
    projected: float | None = None
    score: float | None = None

    @property
    def online(self) -> bool:
        return self.status is QuotaStatus.ONLINE


@dataclass(frozen=True)
class PickQuery:
    """A validated ``/api/pick`` query. Built via :meth:`parse`, never directly."""

    job: str
    slices: int
    lead: str
    builder: str

    @classmethod
    def parse(cls, job: object, slices: object, lead: object, builder: object) -> "PickQuery":
        return cls(
            job=_required_text("job", job),
            slices=_positive_int("slices", slices),
            lead=_required_text("lead", lead),
            builder=_required_text("builder", builder),
        )

    def params(self) -> dict[str, str]:
        return {
            "job": self.job,
            "slices": str(self.slices),
            "lead": self.lead,
            "builder": self.builder,
        }


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _positive_int(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip():
        try:
            number = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
    else:
        raise ValueError(f"{name} must be a positive integer")
    if number < 1:
        raise ValueError(f"{name} must be at least 1")
    return number


def _number(value: object) -> float | None:
    """A finite float, or ``None`` for booleans, strings, NaN/Infinity and unrepresentable ints."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _count(value: object) -> int | None:
    """A whole number no larger than 2**53, else ``None``."""
    number = _number(value)
    if number is None or not number.is_integer() or abs(number) > 2**53:
        return None
    return int(number)


def _objects(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _metrics(data: dict[str, Any]) -> dict[str, float]:
    """The dashboard metrics present in ``data``; absent or non-numeric keys are dropped."""
    found: dict[str, float] = {}
    for key in _METRIC_KEYS:
        value = _number(data.get(key))
        if value is not None:
            found[key] = value
    return found


def _limit_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "window": _text(raw.get("window")),
        "name": _text(raw.get("name")),
        "used_pct": _number(raw.get("usedPct")),
        "pace_pct": _number(raw.get("pacePct")),
        "projected_pct": _number(raw.get("projectedPct")),
        "runs_out_at": _number(raw.get("runsOutAt")),
        "resets_at": _number(raw.get("resetsAt")),
    }


def _window_ref(value: object) -> QuotaWindowRef | None:
    if not isinstance(value, dict):
        return None
    return QuotaWindowRef(**_limit_fields(value), headroom_pct=_number(value.get("headroomPct")))


def _candidate(raw: dict[str, Any]) -> QuotaPickCandidate:
    return QuotaPickCandidate(
        model=_text(raw.get("model")),
        effort=_text(raw.get("effort")),
        subscription=_text(raw.get("subscription")),
        score=_number(raw.get("score")),
        runway=_number(raw.get("runway")),
        group=_text(raw.get("group")),
        tier=_text(raw.get("tier")),
        reason=_text(raw.get("reason")),
        shortest=_window_ref(raw.get("shortest")),
        longest=_window_ref(raw.get("longest")),
    )


def _fallback(value: object) -> QuotaFallback | None:
    if not isinstance(value, dict):
        return None
    return QuotaFallback(
        model=_text(value.get("model")),
        effort=_text(value.get("effort")),
        subscription=_text(value.get("subscription")),
    )


def _lead(value: object) -> QuotaLead | None:
    if not isinstance(value, dict):
        return None
    return QuotaLead(
        model=_text(value.get("model")),
        over_pace=_flag(value.get("overPace")),
        suggestion=_text(value.get("suggestion")),
    )


def usage_from_upstream(data: dict[str, Any], fetched_at: float | None) -> QuotaUsage:
    """Allowlisted view of a ``GET /api/pick/usage`` payload. Unknown keys are dropped."""
    return QuotaUsage(
        offline=False,
        generated_at=_number(data.get("generatedAt")),  # ms, upstream clock
        fetched_at=fetched_at,  # s, dashboard clock
        subscriptions=[
            QuotaSubscription(
                id=_text(raw.get("id")),
                account=_text(raw.get("account")),
                stale=_flag(raw.get("stale")),
                last_resort=_flag(raw.get("lastResort")),
                limits=[
                    QuotaLimit(**_limit_fields(limit)) for limit in _objects(raw.get("limits"))
                ],
            )
            for raw in _objects(data.get("subscriptions"))
        ],
        credits=[
            QuotaCredit(
                account=_text(raw.get("account")),
                balance_usd=_number(raw.get("balanceUsd")),
                burn_per_day_usd=_number(raw.get("burnPerDayUsd")),
                runs_out_at=_number(raw.get("runsOutAt")),  # ms
                low=_flag(raw.get("low")),
                reason=_text(raw.get("reason")),
            )
            for raw in _objects(data.get("credits"))
        ],
    )


def pick_from_upstream(data: dict[str, Any]) -> PickResult:
    """Allowlisted view of a ``GET /api/pick`` payload. Candidate order is kept as sent."""
    return PickResult(
        job=_text(data.get("job")),
        generated_at=_number(data.get("generatedAt")),  # ms, upstream clock
        tier_used=_text(data.get("tierUsed")),
        stretched=_flag(data.get("stretched")),
        lead=_lead(data.get("lead")),
        fallback=_fallback(data.get("fallback")),
        candidates=[_candidate(raw) for raw in _objects(data.get("candidates"))],
        assignment=[
            QuotaAssignment(
                model=_text(raw.get("model")),
                effort=_text(raw.get("effort")),
                slices=_count(raw.get("slices")),
            )
            for raw in _objects(data.get("assignment"))
        ],
        skipped=[
            QuotaSkip(model=_text(raw.get("model")), why=_text(raw.get("why")))
            for raw in _objects(data.get("skipped"))
        ],
        warnings=_strings(data.get("warnings")),
    )


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base_url(base_url: str) -> str:
    """Return the normalised origin, or raise ``ValueError`` if it is not loopback.

    Only ``http``/``https`` origins are accepted. Credentials, a path, a query or a
    fragment are rejected so the caller cannot smuggle a different destination through
    the base URL. An explicit port must be an integer in ``1..65535``.
    """
    if not isinstance(base_url, str):
        raise ValueError("base_url must be a string")
    base = base_url.strip().rstrip("/")
    try:
        origin = urlsplit(base)
    except ValueError as exc:
        raise ValueError("base_url is not a valid URL") from exc
    if origin.scheme not in ("http", "https"):
        raise ValueError("base_url must be an http or https URL")
    if origin.username or origin.password:
        raise ValueError("base_url must not contain credentials")
    if origin.path not in ("", "/") or origin.query or origin.fragment:
        raise ValueError("base_url must be an origin without a path")
    if origin.hostname is None or not _loopback(origin.hostname):
        raise ValueError("base_url must point at a loopback host")
    try:
        port = origin.port
    except ValueError as exc:
        raise ValueError("base_url port must be an integer in 1..65535") from exc
    # urlsplit reports an empty explicit port ("host:") as None, so check the netloc too.
    if origin.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise ValueError("base_url port must be an integer in 1..65535")
    return base


def _tls_verify() -> ssl.SSLContext | bool:
    """CA trust for the upstream, taken from ``SSL_CERT_FILE``/``SSL_CERT_DIR`` when set.

    The client ignores proxy env (``trust_env=False``), which would also drop these two
    variables; this keeps a private CA working. With neither set, ``True`` means the
    httpx default bundle.
    """
    cafile = os.environ.get("SSL_CERT_FILE") or None
    capath = os.environ.get("SSL_CERT_DIR") or None
    if cafile is None and capath is None:
        return True
    return ssl.create_default_context(cafile=cafile, capath=capath)


async def _read_bounded(response: httpx.Response) -> bytes | None:
    """Read at most ``MAX_RESPONSE_BYTES``; ``None`` once the cap is exceeded."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


class QuotaClient:
    """Two-call client for one quota_service origin. One instance per configured upstream."""

    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("secret must be a non-empty string")
        self._base_url = validate_base_url(base_url)
        self._secret = secret
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"QuotaClient(base_url={self._base_url!r})"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}", "Accept": "application/json"}

    async def usage(self) -> QuotaResult:
        """Fetch ``GET /api/pick/usage``."""
        return await self._get(USAGE_PATH, None)

    async def pick(self, *, job: str, slices: int, lead: str, builder: str) -> QuotaResult:
        """Fetch ``GET /api/pick?job=&slices=&lead=&builder=``, validating first."""
        try:
            query = PickQuery.parse(job, slices, lead, builder)
        except ValueError as exc:
            log.debug("quota pick rejected locally: %s", exc)
            return QuotaResult(status=QuotaStatus.INVALID_QUERY, detail=str(exc))
        return await self._get(PICK_PATH, query.params())

    async def _get(self, path: str, params: dict[str, str] | None) -> QuotaResult:
        url = self._base_url + path
        verify: ssl.SSLContext | bool = True
        # CA settings only matter for https; a plain http upstream must not fail on them.
        if urlsplit(url).scheme == "https":
            try:
                verify = _tls_verify()
            except OSError as exc:
                log.debug("quota call %s could not load its CA bundle: %s", path, exc)
                return self._finish(
                    QuotaResult(
                        status=QuotaStatus.ERROR, detail="TLS CA bundle could not be loaded"
                    ),
                    path,
                )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                trust_env=False,
                verify=verify,
            ) as client:
                async with client.stream(
                    "GET", url, params=params, headers=self._headers()
                ) as response:
                    body = await _read_bounded(response)
                    status = response.status_code
        except httpx.TimeoutException:
            return self._finish(
                QuotaResult(status=QuotaStatus.TIMEOUT, detail="upstream timed out"), path
            )
        except httpx.HTTPError:
            return self._finish(
                QuotaResult(status=QuotaStatus.OFFLINE, detail="upstream unreachable"), path
            )
        except (httpx.InvalidURL, OverflowError, ValueError) as exc:
            log.debug("quota call %s rejected the URL: %s", path, exc)
            return self._finish(
                QuotaResult(status=QuotaStatus.ERROR, detail="upstream URL is not usable"),
                path,
            )

        if status == 400:
            return self._finish(
                QuotaResult(
                    status=QuotaStatus.BAD_REQUEST,
                    detail="upstream rejected the query",
                    http_status=status,
                ),
                path,
            )
        if status == 503:
            return self._finish(
                QuotaResult(
                    status=QuotaStatus.UNAVAILABLE,
                    detail="upstream unavailable",
                    http_status=status,
                ),
                path,
            )
        if status != 200:
            return self._finish(
                QuotaResult(
                    status=QuotaStatus.ERROR,
                    detail=f"unexpected upstream status {status}",
                    http_status=status,
                ),
                path,
            )
        if body is None:
            return self._finish(
                QuotaResult(
                    status=QuotaStatus.ERROR,
                    detail=f"response exceeded {MAX_RESPONSE_BYTES} bytes",
                    http_status=status,
                ),
                path,
            )
        try:
            data = json.loads(body)
        except ValueError:
            return self._finish(
                QuotaResult(
                    status=QuotaStatus.ERROR,
                    detail="upstream sent invalid JSON",
                    http_status=status,
                ),
                path,
            )
        if not isinstance(data, dict):
            return self._finish(
                QuotaResult(
                    status=QuotaStatus.ERROR,
                    detail="upstream sent a non-object payload",
                    http_status=status,
                ),
                path,
            )
        return self._finish(
            QuotaResult(
                status=QuotaStatus.ONLINE,
                data=data,
                http_status=status,
                **_metrics(data),
            ),
            path,
        )

    def _finish(self, result: QuotaResult, path: str) -> QuotaResult:
        log.debug("quota call %s -> %s", path, result.status.value)
        return result
