# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the repository's **Security** tab
and choose **Report a vulnerability**. Do not open a public issue, pull request or discussion for
a suspected vulnerability.

Include the piglet version (or commit), your OS, whether you used the web app or the desktop app,
and the steps to reproduce. Once a fix is released, the advisory is published with credit to you
unless you ask otherwise.

## Supported versions

piglet is pre-1.0. Only the latest release gets security fixes.

## Security model

piglet is a coding agent: it gives a language model tools that read, write and edit files and run
shell commands **with your user account's permissions**, without asking before each call. That is
the product working as intended, not a vulnerability. Run it only in projects and with models you
trust, and use a container or VM if you need isolation.

**In scope:** anything that lets someone other than you drive piglet, read its data or run code
through it. For example:

- a web page, another local user or another device reaching the local server (it accepts only
  `127.0.0.1`/`localhost` Host headers, same-origin WebSockets and a per-launch token);
- script injection from model output, tool output or exported sessions into the web UI or the
  desktop app;
- leaking API keys or OAuth tokens (`auth.json`) to logs, the UI, exports or other providers;
- code from an **untrusted** project (one you have not trusted in piglet's trust dialog) running
  before you trust it;
- flaws in the sign-in flows or the desktop app's isolation from the web content it shows.

**Out of scope:**

- the model running commands or editing files through its tools, including after a prompt
  injection in content it read (report it if piglet makes that worse than it needs to);
- extensions, packages, `!command` credential values and trusted project files: they run with
  your permissions by design;
- context files (`AGENTS.md`, `CLAUDE.md`) in the project and its parent directories, which the
  model reads even before you trust the project, as in Pi: they are prompt text, not code, so
  treat a repository you have not reviewed as untrusted input to the model;
- issues that need an attacker who already controls your user account or your piglet data
  directory (`~/.piglet/agent`).
