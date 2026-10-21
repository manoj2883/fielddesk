# Decisions

ADR-lite. Each entry gives the context, the options, the choice and the trade-off. Numbering follows `plans/chapter-1.md`.

## D5 · Labels come from the spec, not from a model

- **Context:** The eval is only as good as its labels. If an LLM labels the tickets, the agent is graded against another model's guesses, and both make the same kinds of mistakes.
- **Options:** (a) the generator LLM labels each ticket as it writes it; (b) a human labels all 200; (c) each spec row states facts, and fixed rules compute the label.
- **Choice:** (c). The spec states facts (`asks`, `machine_impact`, safety code/symptom, deadline, warranty-policy fit, conflicting asks). `fielddesk.labeling` turns those facts into category, priority, needs_human and safety_risk, following the rule table in `data/LABELING_GUIDE.md`.
- **Trade-off:** A label is only as correct as the generated text is faithful to its spec. If the LLM drifts (writes "machine is fine" for a `down` spec), the label is wrong for that text. The blind second-model pass exists to catch that drift. Disagreements go to `review_queue.jsonl` and are never fixed silently.
- **Sub-decision:** priority is *derived* from machine impact and safety, not chosen per row. This lets a test assert the mapping, and stops the generator from writing "urgent" tickets whose facts say `low`.
