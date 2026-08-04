---
type: relationship
title: "The blue-green hold window is sized by the lag one incident measured"
workstream: platform_reliability
relates:
  - record_id: "design_notes:incident-checkout-timeouts.md"
    content_hash: "da2adfe4"
  - record_id: "design_notes:decision-blue-green-deploys.md"
    content_hash: "7eca0e25"
tags: [queue, deploys, order-intake]
status: draft
created: 2026-08-03
updated: 2026-08-03
ai-generated: true
manual_edit: false
---

# The blue-green hold window is sized by the lag one incident measured

The deploy decision does not derive its hold window from first principles, it inherits one number that the checkout-timeout incident happened to produce [src:design_notes:decision-blue-green-deploys.md|H:consequences].

## How they connect

The incident measured consumer lag of roughly forty minutes before the payments provider recovered [src:design_notes:incident-checkout-timeouts.md|H:how-it-failed], and the decision record then adopted that same forty minutes as the bound on how long both colours are held [src:design_notes:decision-blue-green-deploys.md|H:consequences].
Neither record says so, but the deploy policy is therefore calibrated against a single observation rather than against a distribution [src:design_notes:decision-blue-green-deploys.md|H:consequences].
The mechanism that produced the lag is the same one the hold window has to wait out, which is why the two records belong on one page [src:design_notes:incident-checkout-timeouts.md|H:how-it-failed].

## Evidence

- Backoff scheduling is what turned a provider outage into sustained redelivery across the consumer group [src:design_notes:incident-checkout-timeouts.md|H:how-it-failed].
- The decision holds the previous colour until the new one has drained the queue below its pre-deploy depth, so the hold lasts as long as the backlog does [src:design_notes:decision-blue-green-deploys.md|H:decision].
- The incident's own remediation was to shorten the dead-letter threshold, which changes the very lag the hold window is sized against [src:design_notes:incident-checkout-timeouts.md|H:what-we-changed].

## Tension / what does not line up

- The incident report states plainly that no rollback was performed, so the deploy machinery was never exercised under the conditions it is now sized for [src:design_notes:incident-checkout-timeouts.md|H:timeline].
- If the shortened dead-letter threshold works as intended, the forty-minute figure is already stale and the hold window is now conservative rather than correct [src:design_notes:incident-checkout-timeouts.md|H:what-we-changed].

## Open questions

- Should the hold window be re-derived after the circuit breaker has been in production for a quarter?
- Who owns the number if the incident that produced it is never repeated?

## Updates

- 2026-08-03 — page drafted from the two records listed below.

## Sources

- `design_notes:incident-checkout-timeouts.md` — supplies the measured lag figure and the mechanism that produced it.
- `design_notes:decision-blue-green-deploys.md` — supplies the hold rule that consumes that figure.

<!-- trace: design_notes:incident-checkout-timeouts.md | 1 | H:how-it-failed | raw: "the queue consumer group spent the whole window redelivering work it could not complete" -->
<!-- trace: design_notes:decision-blue-green-deploys.md | 1 | H:decision | raw: "we will hold the previous colour until the new one has drained the queue below its pre-deploy depth" -->
<!-- trace: design_notes:incident-checkout-timeouts.md | 1 | H:contributing-factors | raw: "the dead-letter threshold of five attempts was too generous for an outage shaped like this one" -->
<!-- trace: design_notes:decision-blue-green-deploys.md | 1 | H:context | raw: "The hold period is bounded by the worst consumer lag we have measured" -->
