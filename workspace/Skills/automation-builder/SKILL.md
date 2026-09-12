---
name: automation-builder
description: How to design, create, test and maintain a scheduled automation on SU so it runs unattended for months. Read this before creating or editing any automation.
metadata:
  author: su
  category: Official
---

# Building an automation on SU

An automation is a scheduled prompt. On each run the agent gets the full toolset and this manual. The prompt must therefore be a complete job description, not a chat message.

## 1. Interview the request (silently)

Answer for yourself: What is the trigger and cadence? What inputs does it read (apps, files, web)? What does it produce (files, posts, emails)? What must never happen twice? What counts as "nothing to do"? Who gets told, when, and through which channel?

## 2. Create the folder first

`Projects/<automation-name>/` with:
- `README.md` from `templates/README.md` filled in.
- `state.json` from `templates/state.json`.
- `log.jsonl` empty.
Do this with `run_command` (mkdir, cp) and `write_file`.

## 3. Write the prompt

Use `templates/prompt.md` as the skeleton. Every prompt must contain, in this order:
1. Identity and scope: who the agent is, and the one folder it may touch.
2. Read first: the files it reads at the start of every run (README, state.json, knowledge files).
3. The job: numbered steps with concrete tool names (`use_app`, `web_search`, `send_email`).
4. Guardrails: dedupe rules, limits per run and per day, what to skip, what never to do.
5. State and log: exactly what to write to `state.json` and `log.jsonl`, and that state is written only after success.
6. Reporting: when to email and when to stay silent; what the final one-paragraph summary contains.

Keep it under 700 words. Put long knowledge in a `knowledge.md` in the folder and have the prompt read it.

## 4. Create it

Call `create_automation` with name, the prompt, a schedule JSON, and `notify` ("email" when the owner wants results by mail, else "none"). Choose the model only if the owner named one.

## 5. Test it once

Run the automation immediately with a dry-run instruction: tell the owner you are doing so, then call the automation's test (via the dashboard Test button when the owner is present) or run the same prompt yourself with the sentence "DRY RUN: do everything except the final outward action; describe what you would have posted or sent." Confirm the folder, state and log are written as designed. Fix the prompt if not.

## 6. Hand over

Tell the owner: the name, the schedule in words, what one run does, where its files are, and what would make it stop (a missing secret, an app disconnect).

## Maintenance rules

- Editing an existing automation: read its README and state first; keep state compatible or migrate it explicitly.
- Never delete an automation's folder when deleting the automation unless the owner says so.
- If an automation fails three runs in a row, the owner is emailed by the system; the fix usually belongs in the prompt or a missing secret.
