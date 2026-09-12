# Security

SU gives a language model a shell on your server. Read the security model in the README before installing.

## Reporting a vulnerability

Do not open a public issue for a security problem. Use GitHub's private vulnerability report on this repository, or email the address in the repository profile. Include the version (`git rev-parse HEAD`), what you did, and what happened. You will get a reply within a week.

## What counts

- Reading or writing outside the SU user through the web app or the API without valid sign-in.
- Bypassing the file-tool guard, the outbound email allowlist, or the private-network block in `web_fetch`.
- Sign-in or session weaknesses.
- Prompt-injection paths that make the agent leak secrets or act on instructions from fetched content.

## What does not count

- Anything the SU Linux user is allowed to do through `run_command`. That is the design.
- Running SU as root, on a shared machine, or bound to a public address without HTTPS. The README says not to.
