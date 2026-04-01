"""
Consumer 2 — Jira Issue Updater

Reads from: jira.issues.classified
Updates the Jira issue with:
  - Short clean title (summary field)
  - Priority (Highest / High / Medium / Low / Lowest)
  - Structured description (description + timeline + original title preserved)
  - Assignee (if a name/email was found in the original title)
"""

import json
import logging
import os
import sys
from base64 import b64encode
import requests
from confluent_kafka import Consumer, KafkaError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
JIRA_URL = os.environ["JIRA_URL"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SOURCE_TOPIC = "jira.issues.classified"
CONSUMER_GROUP = "jira-updater-group"

PRIORITY_MAP = {
    "LOWEST":  "Lowest",
    "LOW":     "Low",
    "MEDIUM":  "Medium",
    "HIGH":    "High",
    "HIGHEST": "Highest",
}

# ---------------------------------------------------------------------------
# Kafka helper
# ---------------------------------------------------------------------------

def make_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
        }
    )


# ---------------------------------------------------------------------------
# Jira API helpers
# ---------------------------------------------------------------------------

def build_headers() -> dict:
    credentials = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    encoded = b64encode(credentials.encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def adf_heading(text: str, level: int = 2) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def adf_paragraph(text: str) -> dict:
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": text}],
    }


def adf_rule() -> dict:
    return {"type": "rule"}


def adf_ordered_list(lines: list) -> dict:
    return {
        "type": "orderedList",
        "content": [
            {
                "type": "listItem",
                "content": [adf_paragraph(line)],
            }
            for line in lines
            if line.strip()
        ],
    }


def build_adf_description(
    description: str,
    timeline: str,
    original_title: str,
) -> dict:
    """
    Builds a structured Atlassian Document Format body:

      ## Description
      <AI generated description>

      ---

      ## Resolution Timeline
      1. step...
      2. step...

      ---

      ## Original Issue Title
      <raw title the user typed>
    """
    timeline_lines = [
        line.strip()
        for line in timeline.splitlines()
        if line.strip()
    ]

    return {
        "version": 1,
        "type": "doc",
        "content": [
            adf_heading("Description", 2),
            adf_paragraph(description),
            adf_rule(),
            adf_heading("Resolution Timeline", 2),
            adf_ordered_list(timeline_lines) if timeline_lines else adf_paragraph(timeline),
            adf_rule(),
            adf_heading("Original Issue Title", 2),
            adf_paragraph(original_title),
        ],
    }


def find_assignee_account_id(name_or_email: str) -> "str | None":
    """
    Search Jira for a user matching name_or_email.
    Returns their accountId if found, None otherwise.
    """
    url = f"{JIRA_URL}/rest/api/3/user/search"
    params = {"query": name_or_email, "maxResults": 1}
    response = requests.get(url, headers=build_headers(), params=params, timeout=10)

    if response.status_code == 200:
        results = response.json()
        if results:
            account_id = results[0]["accountId"]
            display_name = results[0].get("displayName", name_or_email)
            logger.info("Found assignee: %s → accountId=%s", display_name, account_id)
            return account_id
        else:
            logger.warning("No Jira user found for '%s' — skipping assignee", name_or_email)
    else:
        logger.warning(
            "Assignee search failed (HTTP %d) for '%s' — skipping",
            response.status_code,
            name_or_email,
        )
    return None


def verify_connection() -> bool:
    url = f"{JIRA_URL}/rest/api/3/myself"
    try:
        response = requests.get(url, headers=build_headers(), timeout=10)
    except requests.exceptions.ConnectionError:
        logger.error("Cannot reach Jira at %s — check JIRA_URL in .env", JIRA_URL)
        return False

    if response.status_code == 200:
        data = response.json()
        logger.info(
            "Jira auth OK — connected as %s (%s)",
            data.get("displayName"),
            data.get("emailAddress"),
        )
        return True
    elif response.status_code == 401:
        logger.error(
            "Jira auth FAILED (401) — JIRA_EMAIL or JIRA_API_TOKEN is wrong.\n"
            "  Email used : %s\n"
            "  Check      : https://id.atlassian.com/manage-profile/security/api-tokens",
            JIRA_EMAIL,
        )
        return False
    else:
        logger.error(
            "Unexpected Jira response: HTTP %d — %s",
            response.status_code,
            response.text,
        )
        return False


def update_jira_issue(
    issue_key: str,
    short_title: str,
    priority: str,
    description: str,
    timeline: str,
    original_title: str,
    assignee_name: str,
) -> None:
    priority_name = PRIORITY_MAP.get(priority, "Medium")
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}"

    fields = {
        "summary":     short_title,
        "priority":    {"name": priority_name},
        "description": build_adf_description(description, timeline, original_title),
    }

    # Only set assignee if we can resolve a valid accountId
    if assignee_name:
        account_id = find_assignee_account_id(assignee_name)
        if account_id:
            fields["assignee"] = {"accountId": account_id}

    logger.info(
        "Updating %s — title='%s' priority=%s assignee=%s",
        issue_key,
        short_title,
        priority_name,
        assignee_name or "unassigned",
    )

    response = requests.put(
        url, headers=build_headers(), json={"fields": fields}, timeout=10
    )

    if response.status_code == 204:
        logger.info("Successfully updated %s", issue_key)
    elif response.status_code == 404:
        logger.error("Issue %s not found (404) in %s", issue_key, JIRA_URL)
    elif response.status_code == 400:
        logger.error(
            "Bad request for %s (400) — a field may not be enabled.\nResponse: %s",
            issue_key,
            response.text,
        )
    else:
        logger.error(
            "Failed to update %s: HTTP %d — %s",
            issue_key,
            response.status_code,
            response.text,
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def process_message(raw_value: bytes) -> None:
    data = json.loads(raw_value)
    issue_key      = data["issue_key"]
    short_title    = data.get("short_title", "")
    description    = data.get("description", "")
    priority       = data.get("priority", "MEDIUM")
    timeline       = data.get("timeline", "")
    assignee_name  = data.get("assignee_name", "")
    original_title = data.get("original_title", "")

    logger.info("Received parsed issue %s", issue_key)

    update_jira_issue(
        issue_key=issue_key,
        short_title=short_title,
        priority=priority,
        description=description,
        timeline=timeline,
        original_title=original_title,
        assignee_name=assignee_name,
    )


def main():
    if not verify_connection():
        logger.error("Aborting — fix the Jira credentials in .env and restart.")
        sys.exit(1)

    consumer = make_consumer()
    consumer.subscribe([SOURCE_TOPIC])
    logger.info("Jira updater listening on '%s'...", SOURCE_TOPIC)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Consumer error: %s", msg.error())
                continue
            try:
                process_message(msg.value())
            except Exception as exc:
                logger.error("Error processing message: %s", exc)
    except KeyboardInterrupt:
        logger.info("Shutting down Jira updater.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
