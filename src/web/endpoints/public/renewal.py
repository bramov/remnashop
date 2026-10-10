import hashlib
import json
import secrets
from typing import Annotated
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Header, HTTPException, Request, status
from redis.asyncio import Redis

from src.application.common.dao import PaymentGatewayDao, SubscriptionDao, TransactionDao, UserDao
from src.application.dto import PlanDto, PlanSnapshotDto, SubscriptionDto, UserDto
from src.application.services import PricingService
from src.application.use_cases.gateways.commands.payment import (
    CreatePayment,
    CreatePaymentDto,
    ProcessPayment,
    ProcessPaymentDto,
)
from src.application.use_cases.plan.queries.match import MatchPlan, MatchPlanDto
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.constants import TTL_1H, TTL_10M, TTL_30M
from src.core.enums import PaymentGatewayType, PurchaseType, TransactionStatus
from src.web.schemas import (
    RenewalOfferResponse,
    RenewalOffersResponse,
    RenewalPaymentRequest,
    RenewalPaymentResponse,
    RenewalPaymentStatusResponse,
    RenewalResolveRequest,
    RenewalSessionResponse,
)
from src.web.schemas.renewal import RenewalGatewayPriceResponse

router = APIRouter(prefix="/renewal", tags=["Public - Renewal"])

SESSION_PREFIX = "web_renewal:session"
RATE_PREFIX = "web_renewal:rate"
IDEMPOTENCY_PREFIX = "web_renewal:idempotency"
SESSION_TTL_SECONDS = TTL_30M
RESOLVE_ATTEMPTS = 10

RenewalToken = Annotated[str, Header(alias="X-Renewal-Token", min_length=32, max_length=128)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


def _session_key(token: str) -> str:
    return f"{SESSION_PREFIX}:{hashlib.sha256(token.encode()).hexdigest()}"


def _client_fingerprint(request: Request) -> str:
    # The application is expected to be reachable through the bundled reverse proxy,
    # which overwrites X-Real-IP. Hashing avoids retaining raw client addresses in Redis.
    client_ip = request.headers.get("x-real-ip")
    if not client_ip and request.client:
        client_ip = request.client.host
    return hashlib.sha256((client_ip or "unknown").encode()).hexdigest()


async def _enforce_resolve_rate_limit(redis: Redis, request: Request) -> None:
    key = f"{RATE_PREFIX}:{_client_fingerprint(request)}"
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, TTL_10M)
    if attempts > RESOLVE_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
        )


async def _load_session(redis: Redis, token: str) -> dict[str, int]:
    raw = await redis.get(_session_key(token))
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Renewal session is invalid or expired",
        )
    try:
        data = json.loads(raw)
        return {"user_id": int(data["user_id"]), "subscription_id": int(data["subscription_id"])}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Renewal session is invalid or expired",
        ) from e


async def _load_context(
    redis: Redis,
    token: str,
    user_dao: UserDao,
    subscription_dao: SubscriptionDao,
) -> tuple[UserDto, SubscriptionDto]:
    session = await _load_session(redis, token)
    user = await user_dao.get_by_id(session["user_id"])
    subscription = await subscription_dao.get_current(session["user_id"])
    if (
        not user
        or user.is_blocked
        or not subscription
        or subscription.id != session["subscription_id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription is no longer available for renewal",
        )
    return user, subscription


async def _matched_plan(
    user: UserDto,
    subscription: SubscriptionDto,
    get_plans: GetAvailablePlans,
    match: MatchPlan,
) -> PlanDto:
    plans = await get_plans.system(user)
    plan = await match.system(MatchPlanDto(plan_snapshot=subscription.plan_snapshot, plans=plans))
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This subscription plan is not currently available for renewal",
        )
    return plan


@router.post("/resolve", response_model=RenewalSessionResponse)
@inject
async def resolve_subscription(
    body: RenewalResolveRequest,
    request: Request,
    redis: FromDishka[Redis],
    subscription_dao: FromDishka[SubscriptionDao],
    user_dao: FromDishka[UserDao],
) -> RenewalSessionResponse:
    await _enforce_resolve_rate_limit(redis, request)
    subscription = await subscription_dao.get_current_by_url(body.subscription_url)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    user = await user_dao.get_by_id(subscription.user_id)
    current = await subscription_dao.get_current(subscription.user_id)
    if not user or user.is_blocked or not current or current.id != subscription.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    token = secrets.token_urlsafe(32)
    await redis.set(
        _session_key(token),
        json.dumps({"user_id": user.id, "subscription_id": subscription.id}),
        ex=SESSION_TTL_SECONDS,
    )
    return RenewalSessionResponse(
        token=token,
        expires_in=SESSION_TTL_SECONDS,
        plan_name=subscription.plan_snapshot.name,
        subscription_status=subscription.current_status.value,
        expire_at=subscription.expire_at,
    )


@router.get("/offers", response_model=RenewalOffersResponse)
@inject
async def get_renewal_offers(
    renewal_token: RenewalToken,
    redis: FromDishka[Redis],
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
) -> RenewalOffersResponse:
    user, subscription = await _load_context(redis, renewal_token, user_dao, subscription_dao)
    plan = await _matched_plan(user, subscription, get_available_plans, match_plan)
    active_gateways = await payment_gateway_dao.get_active()
    offers: list[RenewalOfferResponse] = []
    for duration in plan.durations:
        prices: list[RenewalGatewayPriceResponse] = []
        for gateway in active_gateways:
            settings = gateway.settings
            if (
                gateway.type == PaymentGatewayType.TELEGRAM_STARS
                or settings is None
                or not settings.is_configured
            ):
                continue
            pricing = pricing_service.calculate(
                user,
                duration.get_price(gateway.currency),
                gateway.currency,
            )
            prices.append(
                RenewalGatewayPriceResponse(
                    gateway_type=gateway.type,
                    gateway_name=settings.display_name or gateway.type.value,
                    original_amount=str(pricing.original_amount),
                    discount_percent=pricing.discount_percent,
                    amount=str(pricing.final_amount),
                    currency=gateway.currency.symbol,
                )
            )
        if prices:
            offers.append(RenewalOfferResponse(duration_days=duration.days, prices=prices))

    return RenewalOffersResponse(plan_name=plan.name, offers=offers)


@router.post("/payments", response_model=RenewalPaymentResponse)
@inject
async def create_renewal_payment(
    body: RenewalPaymentRequest,
    renewal_token: RenewalToken,
    idempotency_key: IdempotencyKey,
    redis: FromDishka[Redis],
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
    create_payment: FromDishka[CreatePayment],
    process_payment: FromDishka[ProcessPayment],
) -> RenewalPaymentResponse:
    user, subscription = await _load_context(redis, renewal_token, user_dao, subscription_dao)
    token_hash = hashlib.sha256(renewal_token.encode()).hexdigest()
    idem_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    result_key = f"{IDEMPOTENCY_PREFIX}:result:{token_hash}:{idem_hash}"
    lock_key = f"{IDEMPOTENCY_PREFIX}:lock:{token_hash}:{idem_hash}"
    cached = await redis.get(result_key)
    if cached:
        return RenewalPaymentResponse.model_validate_json(cached)
    if not await redis.set(lock_key, "1", ex=60, nx=True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This payment request is already being processed",
        )

    try:
        plan = await _matched_plan(user, subscription, get_available_plans, match_plan)
        duration = plan.get_duration(body.duration_days)
        if not duration:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Plan duration not found",
            )
        gateway = await payment_gateway_dao.get_by_type(body.gateway_type)
        if (
            not gateway
            or gateway.type == PaymentGatewayType.TELEGRAM_STARS
            or not gateway.is_active
            or not gateway.settings
            or not gateway.settings.is_configured
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment gateway is unavailable",
            )

        pricing = pricing_service.calculate(
            user,
            duration.get_price(gateway.currency),
            gateway.currency,
        )
        payment = await create_payment(
            user,
            CreatePaymentDto(
                plan_snapshot=PlanSnapshotDto.from_plan(plan, duration.days),
                pricing=pricing,
                purchase_type=PurchaseType.RENEW,
                gateway_type=body.gateway_type,
            ),
        )
        payment_status = TransactionStatus.PENDING
        if pricing.is_free:
            await process_payment.system(
                ProcessPaymentDto(
                    payment_id=payment.id,
                    new_transaction_status=TransactionStatus.COMPLETED,
                    gateway_type=body.gateway_type,
                )
            )
            payment_status = TransactionStatus.COMPLETED

        response = RenewalPaymentResponse(
            payment_id=str(payment.id),
            payment_url=payment.url,
            status=payment_status.value,
            amount=str(pricing.final_amount),
            currency=gateway.currency.symbol,
        )
        await redis.set(result_key, response.model_dump_json(), ex=TTL_1H)
        return response
    finally:
        await redis.delete(lock_key)


@router.get("/payments/{payment_id}", response_model=RenewalPaymentStatusResponse)
@inject
async def get_renewal_payment_status(
    payment_id: UUID,
    renewal_token: RenewalToken,
    redis: FromDishka[Redis],
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    transaction_dao: FromDishka[TransactionDao],
) -> RenewalPaymentStatusResponse:
    user, _ = await _load_context(redis, renewal_token, user_dao, subscription_dao)
    transaction = await transaction_dao.get_by_payment_id(payment_id)
    if not transaction or transaction.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return RenewalPaymentStatusResponse(
        payment_id=str(transaction.payment_id),
        status=transaction.status.value,
    )
