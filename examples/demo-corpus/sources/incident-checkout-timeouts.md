---
title: "Incident: checkout timeouts during the evening peak"
summary: "Incident writeup for the evening-peak checkout timeouts, tracing the outage to a retry storm that saturated the intake queue's consumer group."
tags: [incident, queue, order-intake]
---

# Incident: checkout timeouts during the evening peak

## Summary

Checkout requests timed out for thirty-one minutes during the evening peak.
No orders were lost; every submission that reached the queue was eventually processed.

## How it failed

The payments provider began returning 503s to every authorisation call.
Every failed message re-entered the intake queue on backoff, and because backoff is scheduled per message the queue consumer group spent the whole window redelivering work it could not complete.
Consumer lag rose to roughly forty minutes before the provider recovered.

## Contributing factors

The dead-letter threshold of five attempts was too high for an outage of this shape.
A message that will never succeed still burns five consumer slots before it is parked.

## What we changed

We shortened the dead-letter threshold to three attempts.
We added a circuit breaker in front of the payments client so that a provider outage stops producing new work.

## Timeline

The provider recovered on its own and no rollback was performed during this incident.
