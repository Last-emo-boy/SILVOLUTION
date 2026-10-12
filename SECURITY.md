# Security

- The server trusts three credentials: the bearer token `SA_TOKEN` (CLI, MCP, the Agnes Harness plugin), the
  browser session cookie derived from it (`POST /api/login`), and optional gateway identity headers signed with
  `SA_GATEWAY_HMAC_KEY`. Keep `SA_TOKEN` long and random; rotating it invalidates every browser session.
- The published port binds to `127.0.0.1` by default (`SA_BIND`). Put your own TLS reverse proxy in front before
  exposing it; the login cookie is marked `Secure` when the proxy sends `X-Forwarded-Proto: https`.
- Code the agent writes (studio RTL, golden models, generator proposals) runs only through the EDA tools or in an
  isolated `python -I` subprocess with a timeout; it never executes in the server process. The Agnes Harness
  sessions have no shell beyond `ls/cat/grep/diff`, and their file tools are confined to the session's directory
  by the plugin's `tool_call` hook.
- Secrets (`DEEPSEEK_API_KEY`, `SA_TOKEN`) are read from the environment / `.env`; the DeepSeek key is handed to
  the harness's credential store once at provisioning and not written elsewhere. Never commit `.env`.

Report a vulnerability by opening a private security advisory on the GitHub repository.
