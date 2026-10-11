# Agents Usage

A KDE Plasma 6 widget that shows how much of the 5-hour and weekly limits are left on your Claude, OpenAI Codex and Z.ai GLM Coding Plan subscriptions, how much of the 5-hour, weekly and monthly limits is left on OpenCode Go, plus a second tab with tokens by day and by model counted from the local Claude Code and Codex logs plus the account-wide Z.ai usage (so only as far back as those tools keep them, and without claude.ai chats) and what they would have cost at API list prices, cache included, using LiteLLM's public price list fetched once a day. The text follows the system language, English or Turkish. It reads the logins Claude Code (`~/.claude/.credentials.json`), the Codex CLI (`~/.codex/auth.json`) and OpenCode (`~/.local/share/opencode/auth.json`, which keeps the API keys of the Z.ai GLM Coding Plan and OpenCode Go) already store, so those tools need to be signed in once. An expired Codex token is refreshed the way the CLI does it and written back to `auth.json`; the Claude token is only read, so after a long break from Claude Code the widget shows the last known numbers until you open `claude` again. It refreshes every 5 minutes and whenever the popup opens.

![Light theme](docs/screenshot-light.png)
![Dark theme](docs/screenshot-dark.png)

## Install

Needs Plasma 6, `python3`, and `msgfmt` (gettext) for the Turkish translation.

```sh
git clone https://github.com/yusufipk/kde-agents-usage.git
cd kde-agents-usage
./install.sh
```

Then add "Agents Usage" to a panel (right click the panel, Add Widgets), or open the system tray arrow, Configure System Tray, and set it to always shown. To update, run `git pull && ./install.sh` and restart Plasma with `systemctl --user restart plasma-plasmashell`.
