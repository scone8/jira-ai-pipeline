import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from . import kafka_producer

logger = logging.getLogger(__name__)


def extract_plain_text(value) -> str:
    """
    Jira Cloud sends descriptions as Atlassian Document Format (ADF) — a nested
    dict. This recursively pulls out all plain text from it.
    Falls back gracefully if the value is already a string or None.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # ADF text node: {"type": "text", "text": "actual content"}
        if value.get("type") == "text":
            return value.get("text", "")
        # Any other node: recurse into its content list
        return extract_plain_text(value.get("content", []))
    if isinstance(value, list):
        return " ".join(extract_plain_text(item) for item in value).strip()
    return ""


@csrf_exempt
@require_POST
def jira_webhook(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    issue = body.get("issue", {})
    fields = issue.get("fields", {})

    issue_key = issue.get("key", "")
    summary = fields.get("summary", "")
    # description can be an ADF dict (Jira Cloud) or plain string — normalise it
    description = extract_plain_text(fields.get("description", ""))

    if not issue_key:
        return JsonResponse({"error": "Missing issue key"}, status=400)

    try:
        kafka_producer.publish_issue(issue_key, summary, description)
    except Exception as exc:
        logger.error("Failed to publish to Kafka: %s", exc)
        return JsonResponse({"error": "Failed to publish event"}, status=500)

    return JsonResponse({"status": "accepted"})
