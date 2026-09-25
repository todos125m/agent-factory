---
name: delegate
version: 1
summary: Pick the registered agent whose capabilities match a task; request a new specialist only when none does.
---
- Match the task's required capability against the agent list you are given.
- If several match, pick the one with the narrowest fitting capability set.
- If none match, do not improvise: return a specialist request {role, mission, capabilities, why} for the Agent Factory.
- Never give a task to an agent whose permissions do not allow the tools it needs.
