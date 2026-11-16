import asyncio
import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
PREFIX = "!"
CONFIRMATION_TEXT = "CONFIRM-DELETE"
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
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
)


async def request_confirmation(
    *,
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    user: discord.abc.User,
) -> bool:
    """Ask for an exact, user-bound confirmation before destructive changes."""
    await channel.send(
        "**⚠️ Server wipe confirmation**\n"
        "This will permanently delete every channel and every role the bot is "
        "allowed to delete. The @everyone role and managed/integration roles "
        "are protected. This cannot be undone.\n"
        f"To continue, type `{CONFIRMATION_TEXT}` within "
        f"{CONFIRMATION_TIMEOUT} seconds."
    )

    def check(message: discord.Message) -> bool:
        return (
            message.author.id == user.id
            and message.channel.id == getattr(channel, "id", None)
            and message.guild is not None
            and message.guild.id == guild.id
            and message.content.strip() == CONFIRMATION_TEXT
        )

    try:
        await bot.wait_for(
            "message",
            check=check,
            timeout=CONFIRMATION_TIMEOUT,
        )
        return True
    except asyncio.TimeoutError:
        await channel.send("Confirmation timed out. Nothing was deleted.")
        return False


async def delete_server_content(guild: discord.Guild, requested_by: discord.abc.User):
    """Delete manageable channels and roles, preserving Discord-managed roles."""
    channels = list(guild.channels)
    roles = [
        role
        for role in guild.roles
        if role.id != guild.id
        and not role.is_default()
        and not role.managed
        and role < guild.me.top_role
    ]

    deleted_channels = 0
    failed_channels = 0
    deleted_roles = 0
    failed_roles = 0
    reason = f"Bulk server cleanup requested by {requested_by} ({requested_by.id})"

    # Delete channels first. Categories are included in guild.channels.
    for channel in channels:
        try:
            await channel.delete(reason=reason)
            deleted_channels += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed_channels += 1
            logger.warning(
                "Could not delete channel %s (%s): %s",
                getattr(channel, "name", "unknown"),
                channel.id,
                exc,
            )

    # Refresh roles after channel deletion and re-check hierarchy.
    for role in list(guild.roles):
        if role.id == guild.id or role.is_default() or role.managed:
            continue
        try:
            if role >= guild.me.top_role:
                continue
            await role.delete(reason=reason)
            deleted_roles += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed_roles += 1
            logger.warning("Could not delete role %s (%s): %s", role.name, role.id, exc)

    summary = (
        "**Server cleanup finished**\n"
        f"Channels — deleted: {deleted_channels}, failed: {failed_channels}\n"
        f"Roles — deleted: {deleted_roles}, failed: {failed_roles}\n"
        "Protected roles (including @everyone and managed roles) were not deleted."
    )
    logger.info(
        "%s Guild: %s (%s)",
        summary.replace("\n", " | "),
        guild.name,
        guild.id,
    )
    return summary


@bot.event
async def setup_hook():
    synced = await bot.tree.sync()
    logger.info("Registered %s global slash command(s).", len(synced))


@bot.event
async def on_ready():
    logger.info("Connected as %s (ID: %s)", bot.user, bot.user.id)


@bot.command(name="deleteallrooms")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
@commands.bot_has_guild_permissions(manage_channels=True, manage_roles=True)
async def delete_all_rooms(ctx: commands.Context):
    """Delete all manageable channels and roles after explicit confirmation."""
    guild = ctx.guild
    if guild is None:
        return

    confirmed = await request_confirmation(
        guild=guild,
        channel=ctx.channel,
        user=ctx.author,
    )
    if not confirmed:
        return

    status = await ctx.send("Cleanup started. This may take a little while...")
    summary = await delete_server_content(guild, ctx.author)
    try:
        await status.edit(content=summary)
    except discord.HTTPException:
        # The command's channel may have been deleted as part of the operation.
        try:
            await ctx.author.send(summary)
        except discord.HTTPException:
            logger.warning("Could not deliver the cleanup summary to the requester.")


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
            "Use this command inside a server.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "A confirmation prompt will be posted in this channel. "
        "No changes happen unless you type the exact confirmation phrase.",
        ephemeral=True,
    )

    confirmed = await request_confirmation(
        guild=guild,
        channel=channel,
        user=interaction.user,
    )
    if not confirmed:
        return

    await interaction.edit_original_response(
        content="Cleanup started. This may take a little while..."
    )
    summary = await delete_server_content(guild, interaction.user)
    try:
        await interaction.edit_original_response(content=summary)
    except discord.HTTPException:
        logger.exception("Could not update the slash-command response.")


@bot.command(name="roomshelp")
async def rooms_help(ctx: commands.Context):
    await ctx.send(
        "**DeleteRooms commands**\n"
        "`/nuke` — delete all manageable channels and roles (administrator only).\n"
        "`!deleteallrooms` — same operation using the legacy prefix command.\n"
        "`!roomshelp` — show this help message.\n"
        "Both cleanup commands require the exact confirmation phrase."
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Administrator permission to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("The bot needs Manage Channels and Manage Roles permissions.")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Use this command inside a server, not in direct messages.")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        logger.error("Command failed", exc_info=error)
        await ctx.send("An unexpected error occurred. Check the bot logs.")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need the Administrator permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = "The bot needs Manage Channels and Manage Roles permissions."
    else:
        logger.error("Slash command failed", exc_info=error)
        message = "An unexpected error occurred. Check the bot logs."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


bot.run(TOKEN)
