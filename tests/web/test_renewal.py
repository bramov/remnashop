import pytest
from pydantic import ValidationError

from src.application.use_cases.plan.queries.match import find_plan_by_snapshot_id
from src.web.schemas import RenewalResolveRequest


def test_resolve_request_strips_query_parameters_and_fragment() -> None:
    url = "https://sub.example.com/abcDEF_123?token=xYz"

    request = RenewalResolveRequest(subscription_url=f"  {url}#client-data  ")

    assert request.subscription_url == "https://sub.example.com/abcDEF_123"


def test_modified_plan_is_found_by_snapshot_id() -> None:
    class Item:
        def __init__(self, item_id: int) -> None:
            self.id = item_id

    snapshot = Item(4)
    modified_plan = Item(4)

    assert find_plan_by_snapshot_id(snapshot, [Item(2), modified_plan]) is modified_plan


def test_invalid_subscription_url_error_is_localized() -> None:
    with pytest.raises(ValidationError) as error:
        RenewalResolveRequest(subscription_url="not-a-valid-url")

    assert "Введите корректную HTTPS-ссылку подписки" in str(error.value)
