# Discord setup guide (§28.1)

One manual, owner-performed procedure. Everything in this file is a one-time
setup step; the controller never re-does or modifies any of it.

## Step 1 — Create the application and bot

1. Open <https://discord.com/developers/applications> and click **New
   Application**. Name it (e.g. `Agent Roundtable`).
2. Go to the **Bot** tab:
   - **Public Bot: OFF** (the server is private).
   - **Requires OAuth2 Code Grant: OFF**.
   - **Message Content Intent: OFF** — mandatory for v1 (§28.1). The controller
     uses slash commands only; it does not read chat text.

## Step 2 — Install into the private server

1. Open the **OAuth2 → URL Generator** tab.
2. Under **Scopes** select exactly: `bot` and `applications.commands`.
3. Under **Bot Permissions** select:
   - `View Channels`
   - `Send Messages`
   - `Embed Links`
   - `Read Message History`
   - `Create Public Threads`
   - `Send Messages in Threads`
   - `Use Application Commands`
4. Do **not** grant broader permissions (no Administrator, no Manage Channels,
   no Kick/Ban, no Attach Files beyond what is listed).
5. Copy the generated URL, open it in a browser, and select the owner's private
   server when prompted. The permission set above is exactly what the bot
   requests — verify the consent screen matches this list (do not trust a
   hardcoded number from a blog post; the OAuth URL is authoritative).

## Step 3 — Store the bot token locally (never in Discord, never committed)

1. On the **Bot** tab, click **Reset Token** and copy the token once.
2. On the owner's Windows machine run:

   ```powershell
   uv run agent-roundtable secrets set discord-token
   ```

   and paste the token at the hidden prompt. It is stored in **Windows
   Credential Manager** under the name `AgentRoundtable.DiscordBotToken`
   (configurable via `discord.credential_name` in `config.yaml`).

3. The value is never echoed, logged, or committed. Verify only its existence:

   ```powershell
   uv run agent-roundtable secrets status discord-token
   # SECRETS RESULT: 'AgentRoundtable.DiscordBotToken' present ...
   ```

4. If the token is ever exposed: **Reset Token** in the portal, then
   `secrets delete discord-token` and `secrets set discord-token` with the new
   value.

## Step 4 — Record Discord IDs in the configuration

Copy `config/config.example.yaml` to
`%LOCALAPPDATA%\AgentRoundtable\config\config.yaml`, then fill in:

| Setting | How to obtain it |
| --- | --- |
| `discord.guild_id` | Server settings → the server name (on the server) → *Copy Server ID* (requires Developer Mode: User settings → Advanced → Developer Mode) |
| `discord.owner_user_ids` | Right-click your own name in the guild → *Copy User ID* |
| `discord.friend_role_ids` | Create a role (e.g. `Friend`) for invited guests; Server settings → Roles → right-click → *Copy Role ID* |
| `discord.channels.open_room` | Right-click the open discussion channel → *Copy Channel ID* |
| `discord.channels.agent_activity` | Right-click the agent activity channel (owner-only visibility) → *Copy Channel ID* |

All IDs are the numeric Discord snowflake IDs — never guessed from display
names (§28.2). If a channel is deleted, the controller reports a configuration
error rather than posting elsewhere.

## Step 5 — Verify

```powershell
uv run agent-roundtable doctor     # configuration must now PASS
uv run agent-roundtable run        # connects to the gateway; watch logs
```

With the bot in the server, run `/status` from the owner account and from a
friend account: the owner sees full controller state; the friend sees only
connectivity and availability (enforced server-side, §28.3).

## Manual checklist (M2 smoke test, §28.3)

- [ ] `/help` as friend lists only friend commands
- [ ] `/help` as owner lists owner commands too
- [ ] `/talk` as friend creates a shared discussion; message persisted
- [ ] `/implement` (any owner command) as friend → denied + audited
- [ ] `/reply` as friend to owner-only conversation → denied
- [ ] `/thread` as friend shows metadata only (no paths)
- [ ] `/status` as friend shows connectivity only (no quota/paths)
