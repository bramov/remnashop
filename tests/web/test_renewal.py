from src.web.schemas import RenewalResolveRequest


def test_resolve_request_preserves_subscription_url_exactly() -> None:
    url = "https://sub.example.com/abcDEF_123?token=xYz"

    request = RenewalResolveRequest(subscription_url=f"  {url}  ")

    assert request.subscription_url == url
