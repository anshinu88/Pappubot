import os
import re
import discord
from discord.ext import commands
import google.generativeai as genai

# ------------- CONFIG (ENV VARS SE) -------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
# ------------------------------------------------

# Gemini config
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --------- Personality ---------
PERSONALITY = """
You are Pappu Programmer, a smart, funny Discord bot.

Language & Style:
- Reply in Hinglish (Hindi + English mix).
- Tone: funny, thoda savage, light roast allowed.
- Thoda attitude de sakte ho, lekin hardcore abusive gaali, caste/religion/gender slurs mat use karo.
- If the user is the owner, respectfully call them "Papa ji".
- Default: chhote / medium reply (2–4 lines).
- Agar user specifically bole: "detail", "samjha", "explain", "theory", "history", "kya hota hai" etc.
  tab thoda lamba answer de sakte ho (max ~10–12 lines, ~1500 chars).

Knowledge:
- Explain science, technology, coding, history, geography, general religion info,
  relationships, motivation, gaming, Discord etc.
- You are NOT connected to live internet or real-time news.
  For "aaj ki news / live score" type questions, clearly say you might not have latest info
  and give general explanation instead.

Important:
- Har baar SIRF latest message ka answer do.
- Pehle wale questions ko dobara mat repeat karo, jab tak user khud na bole.
"""

# --------- Helper functions ---------
def is_owner(user: discord.abc.User):
    return user.id == OWNER_ID

def is_detailed_question(text: str) -> bool:
    t = text.lower()
    keywords = [
        "detail", "details", "samjha", "samjhao", "explain", "explanation",
        "theory", "history", "kya hota hai", "kaise kaam karta hai",
        "kaise work", "physics", "science", "concept"
    ]
    return any(k in t for k in keywords)

def build_normal_prompt(user_name: str, user_text: str, owner_flag: bool) -> str:
    title_name = "Papa ji" if owner_flag else user_name
    detailed = is_detailed_question(user_text)
    if detailed:
        length_line = "Answer thoda detailed de sakte ho (max ~10–12 lines)."
    else:
        length_line = "Answer chhota / medium rakho (max 2–4 lines, seedha point pe)."

    return f"""{PERSONALITY}

User ({title_name}) ne pucha:
\"\"\"{user_text}\"\"\"

{length_line}

Ab Pappu Programmer ka reply (Hinglish me, thoda masti + smart):
"""

def build_announcement_prompt(user_name: str, topic: str, owner_flag: bool) -> str:
    title_name = "Papa ji" if owner_flag else user_name
    return f"""{PERSONALITY}

You are now writing a Discord SERVER ANNOUNCEMENT.

Requested by: {title_name}
Topic: {topic}

Write an announcement in Hinglish with:
- A bold title line
- 3–6 short bullet points
- Friendly but clear tone
- 2–3 emojis max
- Overall length under 1800 characters.
- No overacting, no cringe.

Return ONLY the announcement text that can be directly pasted into Discord.
"""

async def send_long_message(channel: discord.TextChannel, text: str):
    """Discord 2000-char limit ko handle karta hai."""
    if not text:
        await channel.send("Papa ji, reply thoda khali sa aa gaya, dubara try karein? 😅")
        return

    max_len = 1900  # safe limit
    if len(text) <= max_len:
        await channel.send(text)
    else:
        for i in range(0, len(text), max_len):
            chunk = text[i:i + max_len]
            await channel.send(chunk)

async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = is_owner(user)
    if is_announcement:
        prompt = build_announcement_prompt(user.display_name, text, owner_flag)
    else:
        prompt = build_normal_prompt(user.display_name, text, owner_flag)

    try:
        async with channel.typing():
            response = model.generate_content(prompt)
            reply = getattr(response, "text", None)
            if not reply:
                reply = "Papa ji, mujhe thoda blank sa feel ho raha hai, question dobara bhej do? 😅"

            await send_long_message(channel, reply)

    except Exception as e:
        await channel.send(f"Kuch error aa gaya Papa ji: `{str(e)}`")

# --------- OWNER NATURAL-LANGUAGE ADMIN HANDLER ---------
async def handle_owner_nl_admin(message: discord.Message, clean_text: str) -> bool:
    """
    Sirf OWNER ke liye: natural language admin commands.
    Example:
    - "pappu is bande ko mute kar de"
    - "pappu isko unmute kar de"
    - "pappu isko kick maar de"
    - "pappu #announcement me movie night ka announcement daal de"
    - "pappu #announcement me last message delete kar de"
    """
    text = clean_text.lower()
    guild = message.guild
    if guild is None:
        return False  # DM me admin nahi

    # Target channel: agar #channel mention hai to woh, warna current
    target_channel = message.channel
    if message.channel_mentions:
        target_channel = message.channel_mentions[0]

    # Target member: pehla mention jo bot nahi hai
    target_member = None
    for m in message.mentions:
        if m != guild.me:
            target_member = m
            break

    # ---------- DELETE last Pappu msg ----------
    if any(k in text for k in ["delete", "del", "uda", "hata", "remove"]) and any(
        k in text for k in ["last", "pichla", "pichle"]
    ):
        async for msg in target_channel.history(limit=50):
            if msg.author == bot.user:
                await msg.delete()
                await message.channel.send(
                    f"Theek hai Papa ji, {target_channel.mention} me Pappu ka last message delete kar diya. 💣"
                )
                return True
        await message.channel.send(
            f"{target_channel.mention} me Pappu ka recent message nahi mila Papa ji. 🤔"
        )
        return True

    # ---------- ANNOUNCEMENT ----------
    if "announcement" in text or "announce" in text:
        # topic = clean_text se 'announcement/announce' + channel mention hata
        topic = clean_text
        for word in ["announcement", "announce"]:
            topic = topic.replace(word, "")
        for ch in message.channel_mentions:
            topic = topic.replace(ch.mention, "")
        topic = topic.strip()
        if not topic:
            await message.channel.send(
                "Kis topic pe announcement chahiye Papa ji? Thoda detail me likh do. 🙂"
            )
            return True
        await ask_pappu(message.author, topic, True, target_channel)
        return True

    # ---------- UNMUTE (pehle check) ----------
    if "unmute" in text or "un mute" in text or ("mute" in text and "remove" in text):
        if not target_member:
            await message.channel.send("Kisko unmute karna hai Papa ji? @mention karke bolo. 🙂")
            return True

        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            await message.channel.send("Papa ji, 'Muted' role hi nahi mila, unmute kya karu. 😅")
            return True

        try:
            await target_member.remove_roles(muted_role, reason="Unmuted by Pappu on request of owner.")
            await message.channel.send(f"{target_member.mention} ka mute hata diya Papa ji. 🔊")
        except discord.Forbidden:
            await message.channel.send("Role remove nahi kar paya Papa ji, role/position check karo. 🔒")
        except Exception as e:
            await message.channel.send(f"Unmute karte time error aa gaya Papa ji: `{e}`")
        return True

    # ---------- MUTE (Muted role add) ----------
    if "mute" in text and "unmute" not in text and "un mute" not in text:
        if not target_member:
            await message.channel.send("Kisko mute karna hai Papa ji? @mention karke bolo. 🙂")
            return True

        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            await message.channel.send(
                "Papa ji, server me 'Muted' naam ka role nahi mila. Pehle woh role bana ke uske permissions set karo."
            )
            return True

        try:
            await target_member.add_roles(muted_role, reason="Muted by Pappu on request of owner.")
            await message.channel.send(
                f"{target_member.mention} ko mute kar diya Papa ji. 🤐"
            )
        except discord.Forbidden:
            await message.channel.send("Role add nahi kar paya Papa ji, role/position check karo. 🔒")
        except Exception as e:
            await message.channel.send(f"Mute karte time error aa gaya Papa ji: `{e}`")
        return True

    # ---------- KICK ----------
    if "kick" in text or "bahar nikal" in text:
        if not target_member:
            await message.channel.send("Kisko kick karna hai Papa ji? @mention karke bolo. 🙂")
            return True
        try:
            await target_member.kick(reason=f"Kicked by Pappu on request of owner.")
            await message.channel.send(f"{target_member} ko server se kick kar diya Papa ji. 👢")
        except discord.Forbidden:
            await message.channel.send("Kick nahi kar paya Papa ji, role hierarchy/permissions check karo. 🔒")
        except Exception as e:
            await message.channel.send(f"Kick karte time error aa gaya Papa ji: `{e}`")
        return True

    # Agar koi admin keyword match nahi hua
    return False

# --------- Events ---------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online hai Papa ji!")

@bot.event
async def on_message(message: discord.Message):
    # Ignore khud ke messages + doosre bots
    if message.author.bot:
        return

    content_lower = message.content.lower()
    invoked = bot.user.mentioned_in(message) or "pappu" in content_lower

    if invoked:
        # mention remove + clean text
        clean_text = (
            message.content
            .replace(f"<@{bot.user.id}>", "")
            .replace(f"<@!{bot.user.id}>", "")
            .strip()
        )

        # OWNER ke liye pehle natural-language admin try karo
        if is_owner(message.author):
            handled = await handle_owner_nl_admin(message, clean_text)
            if handled:
                await bot.process_commands(message)
                return

        # sirf normal pappu call
        if not clean_text or clean_text.lower() in ["pappu", "pappu?", "pappu!", "pappu bot"]:
            name = "Papa ji" if is_owner(message.author) else message.author.name
            await message.channel.send(f"Haan {name}, bol kya scene hai? 😎")
        else:
            await ask_pappu(message.author, clean_text, False, message.channel)

    # Commands bhi kaam karein (backup)
    await bot.process_commands(message)

# --------- Simple Commands (backup) ---------
@bot.command(name="hello")
async def hello_cmd(ctx):
    name = "Papa ji" if is_owner(ctx.author) else ctx.author.name
    await ctx.send(f"Namaste {name}! 🙏 Main Pappu Programmer hu, gossip + knowledge dono ready. 😎")

@bot.command(name="ask")
async def ask_cmd(ctx, *, question: str):
    await ask_pappu(ctx.author, question, False, ctx.channel)

# --------- BOT RUN ---------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN env var missing!")
    else:
        bot.run(DISCORD_TOKEN)
