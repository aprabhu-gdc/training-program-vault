"""Azure Service Bus helpers shared by ingest API and workers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def _create_service_bus_client(*, connection_string: str, fully_qualified_namespace: str):
    from azure.servicebus import ServiceBusClient

    if connection_string:
        return ServiceBusClient.from_connection_string(conn_str=connection_string, logging_enable=False)

    if fully_qualified_namespace:
        from azure.identity import DefaultAzureCredential

        return ServiceBusClient(
            fully_qualified_namespace=fully_qualified_namespace,
            credential=DefaultAzureCredential(),
            logging_enable=False,
        )

    raise ValueError(
        "Configure either SERVICE_BUS_CONNECTION_STRING or SERVICE_BUS_NAMESPACE to use the ingest queue."
    )


def send_json_message(
    *,
    connection_string: str,
    fully_qualified_namespace: str,
    queue_name: str,
    payload: dict[str, Any],
    message_id: str,
) -> None:
    from azure.servicebus import ServiceBusMessage

    with _create_service_bus_client(
        connection_string=connection_string,
        fully_qualified_namespace=fully_qualified_namespace,
    ) as client:
        with client.get_queue_sender(queue_name=queue_name) as sender:
            sender.send_messages(
                ServiceBusMessage(
                    json.dumps(payload),
                    content_type="application/json",
                    message_id=message_id,
                )
            )


def process_queue_messages(
    *,
    connection_string: str,
    fully_qualified_namespace: str,
    queue_name: str,
    processor: Callable[[dict[str, Any]], None],
    max_message_count: int = 1,
    max_wait_time: int = 5,
) -> int:
    processed = 0
    with _create_service_bus_client(
        connection_string=connection_string,
        fully_qualified_namespace=fully_qualified_namespace,
    ) as client:
        with client.get_queue_receiver(queue_name=queue_name, max_wait_time=max_wait_time) as receiver:
            messages = receiver.receive_messages(max_message_count=max_message_count, max_wait_time=max_wait_time)
            for message in messages:
                try:
                    body_parts: list[bytes] = []
                    for section in message.body:
                        body_parts.append(section if isinstance(section, bytes) else bytes(section))
                    payload = json.loads(b"".join(body_parts).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Queue payload must decode to a JSON object.")
                    processor(payload)
                except Exception:
                    receiver.abandon_message(message)
                    raise
                receiver.complete_message(message)
                processed += 1
    return processed
