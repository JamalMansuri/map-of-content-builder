---
name: _entities
type: substrate
maintainer: human-only
created: "{{ YYYY-MM-DD }}"
---

# Entity Glossary

> **HUMAN-MAINTAINED. THE BUILDER NEVER AUTO-APPENDS TO THIS FILE.**
> Every script reads this file and writes it never — not a row, not a
> cell, not a reordering. Entities discovered during a run are written to
> `<ROOT>/90_system/state/<run_id>_new_entities.json` for you to review, and go no
> further. Nothing there is in effect until you copy it into the table below by hand.
>
> **Why:** asymmetric write. A glossary that grows itself is a closed loop — the
> system proposes a name, then reads its own proposal back as ground truth, and the
> vocabulary drifts toward whatever the model finds easy to say rather than toward
> what the sources actually call things. The one file that decides what two corpora
> mean by "the same thing" is the last file that should be machine-authored.

## What this is for

This is how a MoC over your design docs and a MoC over your incident reports discover
they are talking about the same thing.

Corpora are kept separate on purpose: each has its own records, its own spine, its
own id space. Record ids are unique *within* a corpus, never across them, so there
is no identifier to join on. This table is the only sanctioned bridge. An entity
whose `corpora` cell lists two or more MoCs is a **bridge entity**, and a bridge
entity is what lets a relationship page legitimately span them. Without one, a
cross-corpus connection is asserted rather than grounded.

It is also the strongest signal `propose_relations.py` has. Tag overlap is close to
useless on real corpora (measured: 82% of the tag vocabulary appears on exactly one
page and can never overlap), so with this table empty you will get few proposals and
they will be weak ones. Filling in a dozen rows is the highest-leverage half hour
available in this system.

Shape borrowed from an earlier hand-maintained glossary — with the honest caveat that
that file was never actually populated. What is copied here is its design and its
three load-bearing lines (`maintainer: human-only`, "the tool never writes this file",
"do not let any automated process edit this file"), not a proven result.

## How to fill it in

1. One row per thing a relationship page might be *about*: a system, a plugin, a
   workflow, a team, a concept.
2. **`canonical`** is the join key. Lowercase, hyphenated, stable. Once a page
   references it, never rename it — rename the display text in `note` instead.
3. **`aliases`** are the literal strings as they actually appear in your sources,
   comma-separated. Include the sloppy ones: `auth-gateway`, `auth gw`, `the gateway
   service`. Matching is case- and punctuation-insensitive on word boundaries, so
   you do not need a row per capitalization — you do need a row per *wording*.
4. **`kind`** is a closed set: `system`, `plugin`, `workflow`, `team`, `concept`.
   Nothing else. If a thing does not fit one of the five, it is probably a claim
   rather than an entity, and it belongs on a page, not in this table.
5. **`corpora`** lists the MoC slugs that mention it, comma-separated. This is the
   bridge column and the reason the table exists. Leave it as the single corpus you
   are working in until you have actually seen the entity in another one — an
   optimistic entry here manufactures a cross-corpus link that no source supports.
6. **`note`** is yours. Free text, never read by any script.

## Entries

| canonical | aliases | kind | corpora | note |
|---|---|---|---|---|
| `<<<HUMAN: auth-gateway>>>` | `<<<HUMAN: auth gw, gateway service, authgw>>>` | `<<<HUMAN: system>>>` | `<<<HUMAN: corpus-slug>>>` | `<<<HUMAN: why this matters>>>` |

<!-- Replace the placeholder row above with real entries. Rows still containing a
     <<<HUMAN ...>>> marker are skipped as unfilled and reported as such by
     propose_relations.py, so a half-filled glossary never silently behaves like a
     populated one.

     Add rows below. Do not let any automated process edit this file.

     After each run, read <ROOT>/90_system/state/<run_id>_new_entities.json and
     merge the rows you accept, by hand. Rows you reject will be proposed again on
     the next run; that repetition is the intended friction. -->
