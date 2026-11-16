import asyncio
import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
PREFIX = "!"
CONFIRMATION_TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("DeleteRooms")

if not TOKEN:
    logger.error("Missing DISCORD_BOT_TOKEN environment variable.")
    sys.exit(1)

intents = discord.Intents.default()
# Needed only for the legacy prefix commands. Slash commands work without it.
intents.message_content = True


class DeleteRoomsBot(commands.Bot):
    async def setup_hook(self):
        # setup_hook runs once during startup; unlike on_ready, it is not
        # repeated every time the gateway reconnects.
        synced = await self.tree.sync()
        logger.info("Successfully synced %s global slash command(s).", len(synced))


bot = DeleteRoomsBot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
)

# Prevent two confirmed wipe operations from running simultaneously in one guild.
guild_locks: dict[int, asyncio.Lock] = {}


class ConfirmationView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=CONFIRMATION_TIMEOUT)
        self.user_id = user_id
        self.confirmed = False
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who started this command can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm server cleanup",
        style=discord.ButtonStyle.danger,
        emoji="⚠️",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(
            content="Confirmation received. Cleanup is starting...",
            view=None,
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.confirmed = False
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled. Nothing was deleted.",
            view=None,
        )

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(
                    content="Confirmation timed out. Nothing was deleted.",
                    view=None,
                )
            except discord.HTTPException:
                logger.info("Could not update the expired confirmation message.")


async def request_confirmation(
    *,
    channel: discord.abc.Messageable,
    user: discord.abc.User,
) -> bool:
    """Require an explicit button confirmation from the command invoker."""
    view = ConfirmationView(user.id)
    view.message = await channel.send(
        "**⚠️ Confirm server cleanup**\n"
        "This permanently deletes every channel and every role the bot is "
        "allowed to delete. The @everyone role and managed/integration roles "
        "are protected. This action cannot be undone.\n"
        "Only continue if you understand the consequences.",
        view=view,
    )
    await view.wait()
    return view.confirmed


async def delete_server_content(
    guild: discord.Guild,
    requested_by: discord.abc.User,
) -> str:
    """Delete manageable channels and roles, preserving protected roles."""
    bot_member = guild.me
    if bot_member is None:
        bot_member = await guild.fetch_member(bot.user.id)

    channels = list(guild.channels)
    deleted_channels = 0
    failed_channels = 0
    deleted_roles = 0
    failed_roles = 0
    skipped_roles = 0
    reason = f"Cleanup requested by {requested_by} ({requested_by.id})"

    for channel in channels:
        try:
            await channel.delete(reason=reason)
            deleted_channels += 1
        except discord.HTTPException as exc:
            failed_channels += 1
            logger.warning(
                "Could not delete channel %s (%s): %s",
                getattr(channel, "name", "unknown"),
                channel.id,
                exc,
            )

    for role in list(guild.roles):
        if role.is_default() or role.managed:
            skipped_roles += 1
            continue

        if role >= bot_member.top_role:
            skipped_roles += 1
            continue

        try:
            await role.delete(reason=reason)
            deleted_roles += 1
        except discord.HTTPException as exc:
            failed_roles += 1
            logger.warning("Could not delete role %s (%s): %s", role.name, role.id, exc)

    summary = (
        "**Server cleanup finished**\n"
        f"Channels: {deleted_channels} deleted, {failed_channels} failed\n"
        f"Roles: {deleted_roles} deleted, {failed_roles} failed, "
        f"{skipped_roles} protected or above the bot's role\n"
        "The @everyone role and Discord-managed roles were preserved."
    )
    logger.info(
        "Cleanup result for guild %s (%s): channels deleted=%s failed=%s; "
        "roles deleted=%s failed=%s skipped=%s",
        guild.name,
        guild.id,
        deleted_channels,
        failed_channels,
        deleted_roles,
        failed_roles,
        skipped_roles,
    )
    return summary


async def run_cleanup(guild: discord.Guild, user: discord.abc.User) -> str:
    lock = guild_locks.setdefault(guild.id, asyncio.Lock())
    if lock.locked():
        raise RuntimeError("A cleanup operation is already running in this server.")

    async with lock:
        return await delete_server_content(guild, user)


@bot.event
async def on_ready():
    logger.info("Connected as %s (ID: %s)", bot.user, bot.user.id)


@bot.command(name="deleteallrooms")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
@commands.bot_has_guild_permissions(manage_channels=True, manage_roles=True)
async def delete_all_rooms(ctx: commands.Context):
    """Legacy prefix command; use slash command where possible."""
    if ctx.guild is None:
        return

    confirmed = await request_confirmation(
        channel=ctx.channel,
        user=ctx.author,
    )
    if not confirmed:
        return

    try:
        summary = await run_cleanup(ctx.guild, ctx.author)
    except RuntimeError as exc:
        await ctx.author.send(str(exc))
        return

    try:
        await ctx.author.send(summary)
    except discord.HTTPException:
        logger.warning("Could not DM the cleanup summary to the requester.")


@bot.tree.command(
    name="nuke",
    description="Delete all manageable server channels and roles after confirmation.",
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_channels=True, manage_roles=True)
async def nuke(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        await interaction.response.send_message(
            "Use this command inside a server.",
            ephemeral=True,
        )
        return

    if guild.id in guild_locks and guild_locks[guild.id].locked():
        await interaction.response.send_message(
            "A cleanup operation is already running in this server.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "A confirmation button will appear in the channel. "
        "Nothing is deleted unless you press Confirm.",
        ephemeral=True,
    )

    confirmed = await request_confirmation(
        channel=channel,
        user=interaction.user,
    )
    if not confirmed:
        return

    try:
        summary = await run_cleanup(guild, interaction.user)
    except RuntimeError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except discord.HTTPException:
        logger.exception("Discord API error while running cleanup in guild %s", guild.id)
        try:
            await interaction.followup.send(
                "Cleanup stopped because Discord returned an API error. "
                "Check the bot logs for details.",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass
        return

    try:
        await interaction.followup.send(summary, ephemeral=True)
    except discord.HTTPException:
        try:
            await interaction.user.send(summary)
        except discord.HTTPException:
            logger.warning("Could not deliver the final cleanup summary.")


@bot.command(name="roomshelp")
async def rooms_help(ctx: commands.Context):
    await ctx.send(
        "**DeleteRooms commands**\n"
        "/nuke — delete all manageable server channels and roles (administrator only).\n"
        "!deleteallrooms — legacy prefix command (administrator only).\n"
        "!roomshelp — show this help message.\n"
        "Both cleanup commands require confirmation using the button."
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        message = "You need the Administrator permission to use this command."
    elif isinstance(error, commands.BotMissingPermissions):
        message = "The bot needs Manage Channels and Manage Roles permissions."
    elif isinstance(error, commands.NoPrivateMessage):
        message = "Use this command inside a server, not in direct messages."
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        logger.error("Prefix command failed", exc_info=error)
        message = "An unexpected error occurred. Check the bot logs."

    try:
        await ctx.send(message)
    except discord.HTTPException:
        logger.warning("Could not send a prefix command error response.")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need the Administrator permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = "The bot needs Manage Channels and Manage Roles permissions."
    elif isinstance(error, app_commands.CommandInvokeError):
        logger.error("Slash command execution failed", exc_info=error.original)
        message = "The command failed. Check the bot logs."
    else:
        logger.error("Slash command failed", exc_info=error)
        message = "An unexpected error occurred. Check the bot logs."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.warning("Could not deliver slash command error response.")


bot.run(TOKEN)
