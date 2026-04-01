"""
Consumer 1 — Issue Parser & Enricher

Reads from: jira.issues.created
Writes to:  jira.issues.classified

The user puts ALL details into the issue title. The AI:
  - Extracts a short, clean title (5-10 words)
  - Writes a proper description from the raw title
  - Generates a resolution timeline
  - Assigns a priority: LOWEST, LOW, MEDIUM, HIGH, HIGHEST
  - Extracts an assignee name/email if mentioned
  - Saves the original raw title at the bottom of the description
"""

import json
import logging
import os
import traceback
from confluent_kafka import Consumer, Producer, KafkaError
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
SOURCE_TOPIC = "jira.issues.created"
DEST_TOPIC = "jira.issues.classified"
CONSUMER_GROUP = "classifier-group"

VALID_PRIORITIES = ("LOWEST", "LOW", "MEDIUM", "HIGH", "HIGHEST")

ollama_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")

# ---------------------------------------------------------------------------
# Kafka helpers
# ---------------------------------------------------------------------------

def make_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
        }
    )


def make_producer() -> Producer:
    return Producer({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})


def delivery_report(err, msg):
    if err:
        logger.error("Kafka delivery failed: %s", err)
    else:
        logger.info("Delivered to %s [partition %d]", msg.topic(), msg.partition())


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def to_str(value) -> str:
    """Guarantee a plain string — handles ADF dicts, None, lists, etc."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("type") == "text":
            return value.get("text", "")
        parts = [to_str(item) for item in value.get("content", [])]
        return " ".join(p for p in parts if p).strip()
    if isinstance(value, list):
        return " ".join(to_str(item) for item in value).strip()
    return str(value)


def ask(prompt: str, max_tokens: int = 400) -> str:
    """Single focused prompt → plain text response."""
    response = ollama_client.chat.completions.create(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior software engineering project manager. "
                    "Follow instructions exactly. Be concise and professional."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    return to_str(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# AI extraction — one focused call per field
# ---------------------------------------------------------------------------

def extract_short_title(raw_title: str) -> str:
    result = ask(
        f"Convert this raw issue title into a concise, professional Jira summary of 5-10 words. "
        f"Remove urgency words, names, and extra detail — keep only the core problem.\n\n"
        f"Raw title: {raw_title}\n\n"
        "Short title (no quotes, no explanation):",
        max_tokens=30,
    )
    logger.info("Short title: %s", result)
    return result


def extract_description(raw_title: str) -> str:
    result = ask(
        f"A user submitted a Jira issue with all details crammed into the title below. "
        f"Write a clear, professional 3-4 sentence description of the issue and what needs to be done. "
        f"Extract any relevant context (affected users, systems, urgency) from the title.\n\n"
        f"Raw title: {raw_title}\n\n"
        "Description (plain text only, no bullet points, no headings):",
        max_tokens=250,
    )
    logger.info("Description generated (%d chars)", len(result))
    return result


def extract_priority(raw_title: str) -> str:
    result = ask(
        f"Classify the priority of this Jira issue as exactly one word.\n\n"
        f"Rules:\n"
        f"- HIGHEST: affects payments, money, or revenue; has a hard deadline today or EOD; "
        f"blocks all users; data loss risk; words like urgent/ASAP/critical/emergency\n"
        f"- HIGH: affects many users, needs fixing today or tomorrow, important feature broken\n"
        f"- MEDIUM: affects some users, no hard deadline, inconvenient but not blocking\n"
        f"- LOW: minor issue, nice to have, no deadline\n"
        f"- LOWEST: cosmetic, trivial, no impact\n\n"
        f"Issue: {raw_title}\n\n"
        "Reply with one word only — HIGHEST, HIGH, MEDIUM, LOW, or LOWEST:",
        max_tokens=5,
    )
    priority = result.strip().upper().strip(".,!?;: ")
    if priority not in VALID_PRIORITIES:
        logger.warning("Unexpected priority '%s', defaulting to HIGH", result)
        priority = "HIGH"
    logger.info("Priority: %s", priority)
    return priority


def extract_timeline(raw_title: str) -> str:
    result = ask(
        f"Write a 4-step resolution timeline for this issue. "
        f"Each step must be on its own line with a time estimate.\n\n"
        f"Issue: {raw_title}\n\n"
        "Format exactly like this:\n"
        "1. Immediate (0-1h): action here\n"
        "2. Investigation (1-3h): action here\n"
        "3. Fix & Deploy (3-6h): action here\n"
        "4. Follow-up (next day): action here\n\n"
        "Timeline:",
        max_tokens=250,
    )
    logger.info("Timeline generated (%d chars)", len(result))
    return result


def extract_assignee(raw_title: str) -> str:
    """
    Returns a name or email if the user mentioned one, otherwise empty string.
    Examples it catches: 'assign to Sarah', '@john.doe', 'for Mike', 'cc: emma@co.com'
    """
    result = ask(
        f"Does this issue title mention a specific person to assign the issue to? "
        f"If yes, reply with only their name or email. "
        f"If no person is mentioned, reply with the single word: NONE.\n\n"
        f"Title: {raw_title}\n\n"
        "Assignee (name/email or NONE):",
        max_tokens=20,
    )
    assignee = result.strip().strip(".,!?;:")
    if assignee.upper() == "NONE" or not assignee:
        return ""
    logger.info("Assignee extracted: %s", assignee)
    return assignee


def parse_issue(raw_title: str) -> dict:
    logger.info("Parsing raw title: %s", raw_title)
    return {
        "short_title":   extract_short_title(raw_title),
        "description":   extract_description(raw_title),
        "priority":      extract_priority(raw_title),
        "timeline":      extract_timeline(raw_title),
        "assignee_name": extract_assignee(raw_title),
        "original_title": raw_title,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def process_message(producer: Producer, raw_value: bytes) -> None:
    data = json.loads(raw_value)
    issue_key = data["issue_key"]
    raw_title = to_str(data.get("summary", ""))

    logger.info("Processing issue %s", issue_key)

    parsed = parse_issue(raw_title)

    logger.info(
        "Issue %s parsed → title='%s' priority=%s assignee='%s'",
        issue_key,
        parsed["short_title"],
        parsed["priority"],
        parsed["assignee_name"] or "unassigned",
    )

    payload = json.dumps({
        "issue_key":     issue_key,
        "short_title":   parsed["short_title"],
        "description":   parsed["description"],
        "priority":      parsed["priority"],
        "timeline":      parsed["timeline"],
        "assignee_name": parsed["assignee_name"],
        "original_title": parsed["original_title"],
    })

    producer.produce(
        topic=DEST_TOPIC,
        value=payload.encode("utf-8"),
        callback=delivery_report,
    )
    producer.poll(0)


def main():
    consumer = make_consumer()
    producer = make_producer()
    consumer.subscribe([SOURCE_TOPIC])
    logger.info("Classifier listening on '%s'...", SOURCE_TOPIC)

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
                process_message(producer, msg.value())
            except Exception as exc:
                logger.error(
                    "Error processing message: %s\n%s", exc, traceback.format_exc()
                )
    except KeyboardInterrupt:
        logger.info("Shutting down classifier.")
    finally:
        consumer.close()
        producer.flush()


if __name__ == "__main__":
    main()
