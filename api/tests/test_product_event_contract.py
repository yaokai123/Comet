"""Contract tests for the privacy-scoped first-value funnel."""
from app.controllers.product_event_controller import router
from app.schemas.product_event_schema import FirstValueFunnel, ProductEventRequest


def test_first_value_events_are_allowlisted():
    assert ProductEventRequest(event_name="capture_created").event_name == "capture_created"
    assert ProductEventRequest(event_name="capture_processed").event_name == "capture_processed"
    assert ProductEventRequest(event_name="source_question_started").event_name == "source_question_started"
    assert ProductEventRequest(event_name="source_question_submitted").event_name == "source_question_submitted"
    assert ProductEventRequest(event_name="cited_answer_received").event_name == "cited_answer_received"
    assert ProductEventRequest(event_name="citation_opened").event_name == "citation_opened"
    assert ProductEventRequest(event_name="capture_processing_failed").event_name == "capture_processing_failed"
    assert ProductEventRequest(event_name="capture_retry_started").event_name == "capture_retry_started"
    assert ProductEventRequest(event_name="capture_retry_recovered").event_name == "capture_retry_recovered"


def test_first_value_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/product-events" in paths
    assert "/product-events/first-value" in paths


def test_first_value_response_covers_trust_stages():
    funnel = FirstValueFunnel(
        days=30,
        captured=4,
        processed=3,
        questioned=2,
        cited=1,
        reviewed=1,
        failed=1,
        recovered=1,
        outstanding_failures=0,
        processing_rate=0.75,
        question_rate=2 / 3,
        citation_rate=0.5,
        review_rate=1,
    )
    assert funnel.captured >= funnel.processed >= funnel.questioned >= funnel.cited >= funnel.reviewed
