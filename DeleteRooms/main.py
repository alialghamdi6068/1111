```python
import logging
import os

import discord
from discord.ext import commands


# =========================
# Configuration
# =========================

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
PREFIX = "!"
CONFIRMATION_TEXT = "CONFIRM-DELETE"
CONFIRMATION_TIMEOUT = 30

# =========================
# Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("DeleteRooms")


# =========================
# Bot Setup
# =========================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
)


# =========================
# Events
# =========================

@bot.event
async def on_ready():
    logger.info("Bot connected: %s", bot.user)
    logger.info("Bot ID: %s", bot.user.id)


# =========================
# Commands
# =========================

@bot.command(name="deleteallrooms")
@commands.guild_only()
@commands.has_guild_permissions(administrator=True)
@commands.bot_has_guild_permissions(manage_channels=True)
async def delete_all_rooms(ctx: commands.Context):
    """Delete server channels after explicit confirmation."""

    guild = ctx.guild

    if guild is None:
        return

    await ctx.send(
        "⚠️ **تحذير: حذف القنوات**\n\n"
        "سيتم حذف جميع القنوات التي يستطيع البوت حذفها.\n"
        "اكتب `CONFIRM-DELETE` خلال 30 ثانية للتأكيد.\n"
        "اكتب أي شيء آخر أو تجاهل الرسالة للإلغاء."
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
        await ctx.send("انتهت المهلة؛ تم إلغاء العملية.")
        return

    if
