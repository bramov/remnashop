from src.web.schemas import RenewalResolveRequest


def test_resolve_request_strips_query_parameters_and_fragment() -> None:
    url = "https://sub.example.com/abcDEF_123?token=xYz"

    request = RenewalResolveRequest(subscription_url=f"  {url}#client-data  ")

    assert request.subscription_url == "https://sub.example.com/abcDEF_123"
