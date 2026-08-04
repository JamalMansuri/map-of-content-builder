---
title: "Decision: blue-green deploys for the order intake service"
summary: "Decision record adopting blue-green deploys for the order intake service, with a hold window bounded by the worst consumer lag on record."
tags: [decision, deploys, order-intake]
---

# Decision: blue-green deploys for the order intake service

## Context

Rolling deploys restart consumers one at a time, which is fine when the queue is shallow and unsafe when it is not.
The intake queue is not reliably shallow.

## Decision

We will deploy the order intake service blue-green, and we will hold the previous colour until the new one has drained the queue below its pre-deploy depth.

## Consequences

The hold period is bounded by the worst consumer lag we have measured, which is forty minutes.
Holding both colours doubles the consumer footprint for the length of the window.

## Alternatives considered

Canary deploys were rejected because a partial rollout leaves two retry policies in force at once.
