"""Compatibility facade: everything in the su/ package under one name, as main.py and test_agent.py expect.
Runtime-reassigned attributes live on their module: agent.providers.client_for, agent.channels.channel_run_hook."""
from su import config, providers, conversations, automations, tasks, personas, skills, mcp, pipedream, media, security, web, tools, digest, projects, memory, prompt, runtime_claude, runtime_gemini_cli, planner, runtime_gemini_native, loop, channels, scheduler
from su.config import AGENT_NAME, AUTO_DIR, CONV_DIR, DATA, HOME, KEY_ALIASES, MAX_STEPS, MCP_FILE, PROVIDERS_FILE, RULES_FILE, RUNS_DIR, SKILLS_DIR, SKILLS_MANIFEST, TASK_DIR, TZ, WORKSPACE, _ENV_FILE_KEYS_AT_START, _read, _tool, _write, _ws_path, env, env_file_keys, log, now_iso
from su.providers import CAP_LETTER, CC_MODELS, CLAUDE_BIN, GEMINI_BIN, GEMINI_CLI_MODELS, PROVIDERS, SEED_MODELS, _EFFORT_TIER, _caps, _clean_schema, _family, _family_mods, _gemini_client, _gemini_native_ok, _gemini_tools, _get_provider_key, _local_models, _model_company, _models_cache, add_custom_provider, available_models, cc_available, chat_providers, client_for, configured_providers, default_model, delete_custom_provider, disabled_providers, filter_top_per_company, gemini_cli_available, gemini_cli_logged_in, gemini_models, gemini_simple, get_custom_providers, image_only_providers, provider_status, runtimes, save_custom_providers, set_default_model, set_provider_enabled, split_model, test_provider_connection
from su.conversations import history_transcript, list_conversations, load_conversation, model_history, new_conversation, save_conversation
from su.automations import DAYS, _ampm, delete_automation, describe_schedule, get_automation, list_automations, list_runs, next_run, parse_schedule_nl, save_automation
from su.tasks import TASK_LOG_MAX, _ERR_RE, _TASK_PROCS, _TS_PREFIX, _pid_alive, _proc_env, _systemd_user_ok, _task_log, _task_view, delete_task, get_task, list_tasks, save_task, start_task, stop_task, task_activity, task_alive, task_logs, tasks_tick
from su.personas import AGENTS_DIR, SCOPES, agent_system_extra, delete_agent, get_agent, list_agents, save_agent, scope_filter
from su.skills import _catalog_cache, _frontmatter, _skills_cache, create_skill, install_skill, list_skills, read_skill, skills_catalog
from su.mcp import _root_error, list_mcp, mcp_call, mcp_reload, mcp_session, mcp_tool_specs, save_mcp
from su.pipedream import PD_API, PD_FEATURED, PD_FILE, PD_MCP, PD_USER, _app_tools, _pd_accounts_cache, _pd_app, _pd_cache, _pd_slugs_cache, _pd_store, _pd_token, pd_accounts, pd_app_tools, pd_apps, pd_call, pd_config, pd_connect_link, pd_connected_slugs, pd_delete_account, pd_headers, pd_resolve_slug, pd_token, pd_tool_specs
from su.media import PROVIDER_IMAGE_MODELS, _gemini_image, _generate_via_images_api, _media_out_path, _save_image_data, _veo_video, available_image_models, generate_image, generate_video, image_providers_online, media_models
from su.security import DANGEROUS_PORTS, is_safe_file_path, is_safe_url
from su.web import _mail_send, _strip_html, _tinyfish_key, _web_fetch, _web_search, mail_config, send_email, send_telegram
from su.tools import BUILTIN_TOOLS, CORE_TOOLS, GROUP_HELP, SU_MCP_TOOLS, TOOL_GROUPS, _GROUP_OF, execute_tool, su_mcp_call, su_mcp_tool_list, tool_group, tools_for_tier
from su.digest import DIGEST_FILE, _digest_hash, _digest_source, digest_text
from su.projects import BUILTIN_GROUPS, HIDDEN_KEYS, PROFILE, PROJECTS_FILE, SYSTEM_PROFILE_KEYS, assign_key, default_project_for_key, delete_project, project_of, projects_data, save_project, secrets_block, secrets_by_group
from su.memory import MEMORY_FILE, MEMORY_MAX, _mem_lock, forget, memory_facts, memory_text
from su.prompt import STYLE, _fixed_instructions, system_prompt
from su.runtime_claude import _cc_ensure_skills_link, cc_mcp_config, cc_quick, run_claude_code
from su.runtime_gemini_cli import _gemini_write_settings, run_gemini_cli
from su.planner import FOLD_AT, FOLD_KEEP, PLANNER, _APPROVE_RE, _end_with_plan_or_final, _finish_turn, _json_in, build_model_for, ensure_digest, first_reply, fold_history, plan_turn, quick, quick_available, quick_facts, quick_model, remember_turn, to_schema
from su.runtime_gemini_native import _gemini_stream, run_gemini_native
from su.loop import _Fn, _Msg, _Resp, _TC, _stream_completion, refs_context, run_agent, types_SimpleNamespace
from su.channels import CHANNELS_FILE, _chan_save, _chan_state, _mail_fetch_unseen, _mail_text, channel_run_hook, channels_status, email_channel_tick, mail_allowed, mail_clean_body, telegram_channel_loop, telegram_config
from su.scheduler import _running_lock, automation_default_model, run_automation, scheduler_loop, tunnel_url_tick
from su import audio, diagram, files, media_tools, oauth, research, rrule, rules, scopes, services, tool_docs
from su.rules import create_rule, delete_rule, edit_rule, list_rules, rules_text
from su.automations import normalise_schedule
from su.personas import persona_scopes
