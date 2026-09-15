# Zo Computer reference

Open Computer is modelled on [Zo Computer](https://zo.computer). This page records what Zo exposes, captured on 15 September 2026 from its public API docs and its MCP endpoint, so contributors can see where the clone matches and where it does not. Nothing here is Zo's code; it is the tool surface as any Zo user can list it.

## Public API

Base URL `https://api.zo.computer`, header `Authorization: Bearer zo_sk_...` (token from Settings > Advanced).

| Method | Path | Notes |
|---|---|---|
| POST | `/zo/ask` | `input`, `conversation_id`, `model_name`, `persona_id`, `output_format` (JSON Schema), `stream` (SSE events `FrontendModelResponse`, `End`, `Error`). Returns `output`, `conversation_id`. |
| GET | `/models/available` | `models[]` with `model_name`, `label`, `vendor`, `description`, `type`, `context_window`, `is_byok`. |
| GET | `/personas/available` | `personas[]` with `id`, `name`, `prompt`, `model`, `image`. |
| POST | `/mcp` | Streamable HTTP MCP server exposing every tool below. Server name `zo-tools`. |

Open Computer mirrors the first three routes and serves its own tools at `/mcp` the same way.

## Tools (80)

Parameters marked `?` are optional.
### Files

- `read_file(target_file, start_line?, end_line?, read_entire_file?, pdf_epub_start_page_1_indexed?, pdf_epub_end_page_1_indexed_inclusive?, pdf_epub_include_images?)`
- `write_file(target_file, content?)`
- `edit_file(target_file, operations)`
- `edit_file_llm(target_file, code_edit, instructions?)`
- `list_directory(path, ignore?)`
- `grep_search(query, location?, case_sensitive?, exclude_pattern?, include_pattern?, search_kind?)`
- `copy_file(source_path, dest_path)`
- `transcribe_audio(audio_file_path)`
- `transcribe_video(video_file_path)`
- `generate_image(prompt, file_stem, n?, output_dir?, aspect_ratio?, provider?, model?, reference_filepaths?, quality?, size?)`
- `edit_image(prompt, filepaths?, file_suffix?, provider?, model?, source_filepath?, reference_filepaths?, mask_filepath?, aspect_ratio?, quality?, n?, size?)`
- `generate_video(instruction, filepath?, file_suffix?, orientation?, model?, end_frame_path?, duration_seconds?, aspect_ratio?, resolution?, generate_audio?, output_dir?, file_stem?)`
- `generate_speech(text, file_stem, output_dir?, voice?, format?, language?, speed?, instructions?, model?, voice_sample_path?)`
- `generate_d2_diagram(code, file_stem, output_dir?)`

### Web

- `read_webpage(url, use_browser?)`
- `open_webpage(url)`
- `view_webpage()`
- `use_webpage(task, output_schema?)`
- `save_webpage(url)`
- `web_search(query, time_range?, include_domains?, topic?)`
- `web_research(query, time_range?, category?, include_domains?, exclude_domains?, include_text?)`
- `find_similar_links(url, include_domains?, exclude_domains?, exclude_source_domain?)`
- `image_search(query)`
- `x_search(query, allowed_x_handles?, excluded_x_handles?, time_range?, from_date?, to_date?, enable_image_understanding?, enable_video_understanding?)`
- `maps_search(query, location?, open_now?, min_rating?, included_type?, price_level?, language?, region?)`

### Computer

- `bash(cmd, cwd?, timeout?, description?)`
- `create_website(name, variant?, parent_path_parts?, force?)`
- `publish_site(site_path, public?)`
- `unpublish_site(site_path)`
- `register_user_service(label, mode, local_port?, entrypoint?, workdir?, env_vars?, public?)`
- `list_user_services()`
- `service_doctor(service)`
- `update_user_service(service_id, label?, mode?, local_port?, entrypoint?, workdir?, env_vars?, public?, enabled?)`
- `delete_user_service(service_id)`
- `proxy_local_service(local_port)`

### Integrations

- `send_email_to_user(subject, markdown_body, attachments?)`
- `send_sms_to_user(message, media_files?, contact_name?)`
- `connect_telegram()`
- `list_app_tools(app_slug)`
- `connect_integration(app_slug)`
- `search_app_catalog(query)`
- `use_integration(app_slug, tool_name, configured_props, email?)`
- `use_app_x(tool_name, configured_props, username?)`

### Automations

- `create_automation(rrule, instruction, delivery_method?, model?)`
- `list_automations()`
- `get_automation(automation_id)`
- `edit_automation(automation_id, title?, instruction?, rrule?, delivery_method?, model?, active?)`
- `delete_automation(automation_id)`
- `create_agent(rrule, instruction, delivery_method?, model?)`
- `list_agents()`
- `edit_agent(automation_id, title?, instruction?, rrule?, delivery_method?, model?, active?)`
- `delete_agent(automation_id)`

### Personalisation

- `create_persona(name, prompt, image?, image_hue?, model?)`
- `list_personas()`
- `edit_persona(persona_id, name?, prompt_edit?, edit_instructions?, image?, image_hue?, model?)`
- `delete_persona(persona_id)`
- `set_active_persona(persona_id)`
- `list_available_scopes()`
- `set_persona_scopes(persona_id, scopes)`
- `create_rule(instruction, condition?)`
- `list_rules()`
- `edit_rule(rule_id, instruction?, condition?)`
- `delete_rule(rule_id)`
- `update_user_settings(field, value)`

### Zo Space (hosted site)

- `get_space_route(path)`
- `write_space_route(path, route_type, code?, public?)`
- `edit_space_route(path, code_edit, edit_instructions?, public?)`
- `delete_space_route(path)`
- `list_space_routes()`
- `undo_space_route(path)`
- `redo_space_route(path)`
- `get_space_route_history(path)`
- `update_space_asset(source_file, asset_path)`
- `delete_space_asset(asset_path)`
- `list_space_assets()`
- `get_space_errors()`
- `restart_space_server()`
- `get_space_settings()`
- `update_space_settings(path?, site_title?, site_description?, og_image_url?, favicon_url?, custom_head_html?, robots_txt?, noindex?, custom_404_route?, lang?, atproto_did?)`

### Meta

- `tool_docs(tool_name)`

## Behaviour worth copying

### Automations (`create_automation`)

```text
Create an automation to run an AI task on a schedule.
Automations run on a schedule with an instruction. The automation runs the instruction at the scheduled time. The automation runner is Zo (another instance of yourself, with all the same tools, running on the same computer). Keep this in mind when writing the instruction: it should be specific, detailed, reference specific files, directories, and tools, and include all the context, examples, and nudges needed to give it the best chance of successfully completing the task. delivery_method is the user's preferred communication channel for routine messages about the run; it does not force every run to send a message. Zo emails the user if the automation fails, including when delivery_method is None.

Use RFC 5545 RRULE syntax. Provide a bare RRULE without DTSTART — the system handles start times and timezone conversion automatically. BYHOUR is in the user's local timezone, not UTC.

Recurring schedule examples:
- "FREQ=DAILY;BYHOUR=9;BYMINUTE=0" — Every day at 9:00 AM
- "FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=14;BYMINUTE=30" — Mon/Wed/Fri at 2:30 PM
- "FREQ=HOURLY;INTERVAL=1" — Every hour
- "FREQ=HOURLY;INTERVAL=6" — Every 6 hours
- "FREQ=MINUTELY;INTERVAL=30" — Every 30 minutes

One-time schedule examples:
- "FREQ=DAILY;BYHOUR=14;BYMINUTE=0;COUNT=1" — One-time at 2:00 PM today
- "FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=3;BYHOUR=13;BYMINUTE=0;COUNT=1" — One-time at 1:00 PM on April 3
- "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=25;BYHOUR=9;BYMINUTE=0;COUNT=1" — One-time at 9:00 AM on December 25

For one-time future dates, use FREQ=YEARLY with BYMONTH and BYMONTHDAY to target the exact date.

Important:
- Do NOT include DTSTART — the system adds it.
- Do NOT include TZID — hours are treated as local time.
- Do NOT use cron syntax, ISO 8601 intervals (R/PT...), or FREQ=ONCE (not valid).
- Valid FREQ values: MINUTELY, HOURLY, DAILY, WEEKLY, MONTHLY, YEARLY.

Frequency: each run is a full Zo session, so high-frequency schedules add up. For schedules more often than hourly, explain that each run is a full chat session and confirm with the user before creating. Do not invent specific cost or token numbers.

The instruction should be a direct, actionable command that the automation will execute when triggered.

delivery_method is the user's preferred communication channel for routine messages about the run. It does not force every run to send a message. If the automation should stay silent when there is nothing new or actionable, say that explicitly in the instruction. Zo emails the user if the automation fails, including when delivery_method is None.

Args:
    rrule: RFC 5545 RRULE string (bare RRULE without DTSTART or TZID — system adds these automatically)
    instruction: Clear, actionable instruction to execute when the automation triggers (include all necessary context)
    delivery_method: Optional preferred communication channel for routine messages: one of 'email', 'sms', 'telegram', 'slack', 'discord', or None. Failure alerts are emailed even when this is None
    model: Optional model ID. Call tool_docs('create_automation') or tool_docs('edit_automation') to see available model IDs. User-specific BYOK model IDs in the format 'byok:your_config_id' are also accepted.

Available model IDs: (Zo's built-in catalogue)
- zo:anthropic/claude-sonnet-5
```

### Services (Zo's version of 24/7 tasks) (`register_user_service`)

```text
Register a managed User Service on your Zo server.
A User Service is an automatically managed process on Zo Computer.

Service modes:
- "http" — public or private web service with a URL endpoint
- "tcp" — public TCP service with a host:port endpoint
- "process" — supervised background process with no public endpoint (workers, schedulers, bots, sync loops, or internal-only localhost services like Postgres bound to 127.0.0.1)

Use "process" for anything that doesn't need to be reached from outside the host. Process-only services do not count toward hosted-service limits.

Visibility (HTTP only):
- public (default): reachable at *.zocomputer.io without auth
- private: reachable at *.zo.computer, requires the owner to sign in.
- TCP services are always public. Process services have no endpoint.

The entrypoint runs via supervisord, not a shell. Shell syntax like $PORT or VAR=value won't work directly.
- To use environment variables: use %(ENV_PORT)s syntax, e.g. "python3 -m http.server %(ENV_PORT)s"
- To use shell features: wrap in bash, e.g. "bash -c 'python3 -m http.server $PORT'"
- To set env vars inline: use the env_vars parameter instead of "VAR=value command" syntax.

PORT is only injected for networked services (http/tcp). Process-only services do not receive PORT.

Networked services should bind to the PORT environment variable.

At the API/view layer, process-only services still serialize as protocol="tcp" and local_port=0 for legacy compatibility. The process may still listen on localhost if you configure that explicitly in the command, config file, or env_vars.

Args:
    label: Unique label (per host) for the service. Lowercase and hyphens allowed. e.g. "my-web-app".
    mode: "http", "tcp", or "process". Use "process" for internal-only localhost services or non-network background processes.
    local_port: Localhost port to expose (1-65535, injected as PORT env var). Required for "http" and "tcp". Omit for "process".
    entrypoint: Optional command to run the service, e.g. "python3 -m http.server %(ENV_PORT)s". If omitted, only the tunnel is managed.
    workdir: Working directory for the service entrypoint. Defaults to /home/workspace.
    env_vars: Optional environment variables. PORT will be overridden for networked services and is not set for process-only services.
    public: Whether the service is publicly accessible (defaults to true). Only applies to HTTP services — set to false for a private service at *.zo.computer that requires sign-in. TCP services are always public; process services have no endpoint.
```

### Persona scopes (`list_available_scopes`)

```text
List all available tool permission scopes, presets, and requirement rules.

Returns the full scope vocabulary that can be used with set_persona_scopes.
Scopes are resource:action pairs (e.g. 'files:read', 'web:search'). Presets are named
aliases like 'all', 'workspace', 'read_only'. An empty scope list means chat-only (no tools).

Related tools: set_persona_scopes, list_personas
```

### Web search (`web_search`)

```text
Search the web using a search engine.

Use this tool instead of web_research for broad discovery or current events. Use web_research for more in-depth searches.

Use parameters appropriately:
- For news/current events: set topic="news" and time_range="day" (or "week" for less time-sensitive queries).
- For general queries where recency matters: set an appropriate time_range ("day", "week", "month", "year").
- For general queries where recency doesn't matter: set time_range="anytime".
- Use include_domains to constrain results to specific sites when needed.

Args:
    query: Search query.
    time_range: Recency window for results: "anytime", "day", "week", "month", "year" (aliases: "a", "d", "w", "m", "y"), or a shorthand like "4h", "48h", "7d", "2w". "anytime" applies no date filter. Unrecognized values are treated as "anytime". When both an explicit date range and time_range are given, the explicit dates win.
    include_domains: List of domains to constrain results.
    topic: "general" (search the whole web — the default) or "news" (articles from news sites). This is the search index, not a subject category: for a topic like finance, jobs, or sports, leave it "general" and put the subject in the query.

Related tools: web_research, find_similar_links
```

### File edits (`edit_file`)

```text
Edit a text file using a sequence of precise edit operations.

How to use the `edit_file` tool:
1. DETERMINE CURRENT FILE CONTENT from context or by calling read_file.
2. GENERATE "MATCH BLOCKS" that are EXACT substrings of the current content.
3. DECOMPOSE THE EDIT INTO DISCRETE OPERATIONS.
4. CALL `edit_file` with the target_file and operations.
5. INSPECT THE RESULT to verify the operations achieved the desired state.
6. ITERATE IF NECESSARY.

Available EditOperation objects:
- replace_block: Replaces occurrences of a text block.
- insert_after: Inserts text after a specific block.
- insert_before: Inserts text before a specific block.
- delete_block: Deletes occurrences of a text block.
- append_line: Appends a line to the end of the file.

Args:
    target_file: The absolute path to the file to edit.
    operations: A list of operations to apply to the file: replace_block, insert_after, insert_before, delete_block, append_line.
```

### Image generation (`generate_image`)

```text
Generate images with the host's default image model or an explicit compatible model.
Zo supports a reviewed catalog of image models through Vercel AI Gateway. The default is set per workspace in AI settings.

PROMPTING TIPS:
- Use dashed lists (-) to specify multiple distinct requirements.
- Use ALL CAPS sparingly for critical constraints (MUST, NEVER, EXACTLY).
- For photorealistic output: specify camera, lighting, and publication context.
- Compositional keywords: "rule of thirds", "good use of negative space".
- End prompts with explicit negations: "Do not include any text, watermarks, or logos."

Leave model empty unless the user asks for a specific model. The request fails before generation when the selected model cannot handle its aspect ratio, count, quality, or attachment shape. Use edit_image for image-conditioned work; generation-time reference inputs remain unavailable until a compatible catalog profile is enabled.

Args:
    prompt: Specific description of the image to create.
    file_stem: The base name for the output files (e.g., "myimg" will create "myimg_1.png", "myimg_2.png", etc.).
    n: Number of images. The selected model's catalog profile sets the allowed values.
    output_dir: The directory where the generated images will be saved. Defaults to /home/workspace/Images.
    aspect_ratio: Desired aspect ratio, such as "16:9", "1:1", or "3:4".
    provider: Deprecated compatibility alias: "google" or "openai". Prefer model.
    model: Optional public model ID. Empty uses the workspace default.
    reference_filepaths: Reserved ordered reference images. Current public generation profiles reject these; use edit_image.
    quality: Optional normalized quality. Unsupported values fail before provider work.
    size: Optional exact canvas size for models that expose size instead of aspect_ratio.

Related tools: edit_image
```

### Shell (`bash`)

```text
Run a single shell command on the computer.
Zo can execute shell commands in a bash session, allowing for system operations, script execution, and command-line automation. Use cases include: System automation (execute scripts, manage files, automate tasks), Data processing (process files, run analysis scripts, manage datasets), Environment setup (install packages, configure environments).

Args:
    cmd: The shell command string to execute.
    cwd: The working directory in which to execute the command. Defaults to the user's workspace root.
    timeout: Optional time limit in seconds. When set, the command is terminated if it runs longer than this. Defaults to unbounded.
    description: Optional short human-readable label for the command (shown in the UI). Does not affect execution.
```

## Where Open Computer stands (15 September 2026)

Cloned from Zo:
- **Schedules**: RFC 5545 RRULE (`FREQ=DAILY;BYHOUR=9;BYMINUTE=0`, `COUNT=1` for one-offs) alongside the JSON forms. Delivery is email only, by design.
- **Services**: tasks have modes `process`, `http` (gets `PORT`, private URL at `/api/svc/<label>/`, public via `SU_SERVICE_URL_PATTERN`) and `tcp`, with env vars.
- **Web**: `web_search` with `time_range` and `topic`, `web_research` (search then read the top pages), `view_webpage` (browserless text + screenshot), `web_fetch` through TinyFish Fetch when a key is set. Only TinyFish's free Search and Fetch endpoints are used.
- **Files**: `read_file` by line range and PDF pages, `edit_file` with an operation list applied all-or-nothing, `copy_file`, `list_dir` ignore list, `grep` include/exclude/case filters.
- **Personas and rules**: `resource:action` scopes with presets (all, workspace, read_only, chat); rules as objects with a condition, editable by the agent and in Settings › Agent.
- **Tool docs**: `tool_docs(tool_name)` serves detailed guidance on demand.
- **Media**: `edit_image`, `generate_speech`, `transcribe` (audio and video), `generate_diagram` (D2). Speech and transcription run on any OpenAI-compatible provider via `SU_SPEECH_MODEL` and `SU_TRANSCRIBE_MODEL`.

Not cloned:
- X, image and maps search, and the in-page browser agent (`use_webpage`): they need paid providers or a browser agent.
- SMS, Slack and Discord delivery: email only.
- Hosted sites (`zo.space` routes, `publish_site`).
- Per-model `context_window` from providers; the loop still uses `SU_CONTEXT_BUDGET`.
