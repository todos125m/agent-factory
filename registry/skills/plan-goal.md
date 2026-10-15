---
name: plan-goal
version: 1
summary: Turn a user goal into a small task graph of registered specialists.
---
1. Restate the goal in one sentence; list assumptions you had to make.
2. Split into at most `max_steps` tasks. Each task: title, agent (a registered name), goal (what it must produce), depends_on (indices), risk (low|medium|high).
3. Tasks with no dependency between them run in parallel; do not chain them without reason.
4. Risk is high for anything costly, irreversible, external (messages, purchases, deploys) or strategic.
5. Prefer the fewest tasks that answer the goal. No task without a clear output.
