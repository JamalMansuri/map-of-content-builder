---
title: "Order intake service — design note"
summary: "Design note for the order intake service, covering the queue consumer, its retry policy, and the idempotency key that makes replays safe."
tags: [design, queue, order-intake]
---

# Order intake service — design note

## Scope

The order intake service accepts checkout submissions over HTTP and hands each one to a durable queue.
Everything downstream of the queue is asynchronous.

## Queue consumer

A single consumer group drains the intake queue.
Each message carries an idempotency key derived from the cart id and the submission timestamp, so a replayed message is a no-op rather than a duplicate order.

## Retry policy

Failed messages are retried with exponential backoff, starting at two seconds and doubling to a ceiling of sixty seconds.
After five attempts the message moves to the dead-letter queue and pages the on-call engineer.

## Known limits

Backoff is scheduled per message rather than per consumer, so a broad downstream outage produces a retry storm rather than a clean pause.
Nothing in this design caps the total redelivery rate across the consumer group.
