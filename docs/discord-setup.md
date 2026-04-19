# Discord Bot Setup

This guide walks you through creating a Discord bot and connecting it to openDAGent as an input channel.

## 1. Create a Discord Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. `openDAGent`), and confirm
3. In the left sidebar, go to **Bot**
4. Click **Add Bot** and confirm

## 2. Get the Bot Token

1. On the **Bot** page, under **Token**, click **Reset Token** and copy the value
2. Store it in your environment:

```bash
export OPENDAGENT_DISCORD_TOKEN=your-token-here
```

Or add it to your shell profile / `.env` file. Never commit this value to git.

## 3. Set Bot Permissions

On the **Bot** page, under **Privileged Gateway Intents**, enable:

- **Message Content Intent** — required to read message text

Under **OAuth2 > URL Generator**:

1. Check **bot** under Scopes
2. Under Bot Permissions, check at minimum:
   - **Read Messages / View Channels**
   - **Send Messages**
   - **Read Message History**
3. Copy the generated URL and open it in your browser to invite the bot to your server

## 4. Get Your Guild (Server) ID

1. In Discord, go to **User Settings > Advanced** and enable **Developer Mode**
2. Right-click your server name in the sidebar and select **Copy Server ID**
3. This is your `guild_id`

## 5. Configure openDAGent

Edit `runtime/config/app.yaml` and update the `inputs.discord` section:

```yaml
inputs:
  discord:
    enabled: true
    bot_token_env: OPENDAGENT_DISCORD_TOKEN
    allowed_guild_ids:
      - 123456789012345678   # your guild ID
```

- `bot_token_env` — name of the environment variable holding your bot token
- `allowed_guild_ids` — list of guild IDs the bot will accept messages from; leave empty (`[]`) to allow all guilds (not recommended in production)

## 6. Start openDAGent

```bash
openDAGent start --config runtime/config/app.yaml
```

Once running, the bot will come online in your server. Send a message in any channel the bot has access to and it will be picked up as an input.

## Restricting to a Specific Channel (Optional)

Discord channel restrictions are enforced at the permission level in Discord itself:

1. Open the channel settings in Discord
2. Go to **Permissions**
3. Remove **View Channel** from `@everyone`
4. Add the bot role with **View Channel** and **Send Messages**

This limits the bot to only that channel without any code changes.
