import json
import logging
from confluent_kafka import Producer
from django.conf import settings

logger = logging.getLogger(__name__)

_producer = None


def get_producer():
    global _producer
    if _producer is None:
        _producer = Producer({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS})
    return _producer


def delivery_report(err, msg):
    if err:
        logger.error("Kafka delivery failed: %s", err)
    else:
        logger.info("Delivered to %s [partition %d]", msg.topic(), msg.partition())


def publish_issue(key: str, summary: str, description: str) -> None:
    payload = json.dumps(
        {
            "issue_key": key,
            "summary": summary,
            "description": description,
        }
    )
    producer = get_producer()
    producer.produce(
        topic="jira.issues.created",
        value=payload.encode("utf-8"),
        callback=delivery_report,
    )
    producer.poll(0)
