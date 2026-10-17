import logging
import os
import sys

import discord
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


@bot.event
async def on_ready():
    logger.info("Connected as %s (ID: %s)", bot.user, bot.user.id)


@bot.command(name="deleteallrooms")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def delete_all_rooms(ctx: commands.Context):
    """Delete all channels in this server after explicit confirmation."""
    guild = ctx.guild
    if guild is None:
        return

    await ctx.send(
        "**Warning: delete all server channels?**\n"
        "This will attempt to delete every channel the bot can manage. "
        "This cannot be undone.\n"
        f"Type `{CONFIRMATION_TEXT}` within {CONFIRMATION_TIMEOUT} seconds to continue."
    )

    def check(message: discord.Message) -> bool:
        return (
            message.author.id == ctx.author.id
            and message.channel.id == ctx.channel.id
            and message.guild is not None
            and message.guild.id == guild.id
        )

    try:
        confirmation = await bot.wait_for(
            "message",
            check=check,
            timeout=CONFIRMATION_TIMEOUT,
        )
    except TimeoutError:
        await ctx.send("Confirmation timed out. No channels were deleted.")
        return

    if confirmation.content.strip() != CONFIRMATION_TEXT:
        await ctx.send("Confirmation did not match. No channels were deleted.")
        return

    channels = list(guild.channels)
    deleted = 0
    failed = 0

    status = await ctx.send(f"Starting channel deletion for {len(channels)} channels...")

    for channel in channels:
        try:
            await channel.delete(reason=f"Bulk channel deletion requested by {ctx.author} ({ctx.author.id})")
            deleted += 1
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed += 1
            logger.warning("Could not delete channel %s (%s): %s", channel.name, channel.id, exc)

    summary = f"Finished. Deleted: {deleted}. Failed: {failed}."
    logger.info("%s Server: %s (%s)", summary, guild.name, guild.id)

    try:
        await status.edit(content=summary)
    except discord.HTTPException:
        logger.info("Could not edit the status message; channel may have been deleted.")


@bot.command(name="roomshelp")
async def rooms_help(ctx: commands.Context):
    await ctx.send(
        "**DeleteRooms commands**\n"
        "`!deleteallrooms` — request deletion of all server channels (administrator only).\n"
        "The bot will ask for an exact confirmation before doing anything."
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Administrator permission to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("The bot needs the Manage Channels permission.")
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("Use this command inside a server, not in direct messages.")
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        logger.error("Command failed", exc_info=error)
        await ctx.send("An unexpected error occurred. Check the bot logs.")


bot.run(TOKEN)
