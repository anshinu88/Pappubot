# main.py - Pappu Programmer (full copy-paste, fixed global issue)
from dotenv import load_dotenv
load_dotenv()

import os
import re
import random
import time
import requests
import html
import discord
from discord.ext import commands
import google.generativeai as genai

# ------------- CONFIG (ENV VARS) -------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Feature toggles
ALLOW_INSULTS = os.getenv("ALLOW_INSULTS", "0") == "1"      # allow witty roasts
RETALIATE = os.getenv("RETALIATE", "0") == "1"              # auto reply when bot insulted
ALLOW_PROFANITY = os.getenv("ALLOW_PROFANITY", "0") == "1"  # allow stronger profanity (owner control)

# Live search provider: "serpapi" or "google"
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "").lower()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# In-memory conversation memory (short-term per-user)
# Structure: { user_id: {"last_subject":"daru","last_query":"500 ke andar daru brand", "ts":unix_ts} }
CONTEXT_MEMORY = {}
MEMORY_TTL = 60 * 60 * 6  # 6 hours memory retention (adjustable)

# ------------------------------------------------

# Gemini config (if provided)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
else:
    model = None

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Personality / Prompt ----------------
PERSONALITY = """
You are Pappu Programmer, a smart, funny Discord bot.

Language & Style:
- Reply in Hinglish (Hindi + English mix).
- Tone: funny, thoda savage. If ALLOW_PROFANITY enabled you may use stronger profanity, but avoid hateful slurs (no targeting of race, religion, gender, sexual orientation).
- If the user is the owner, call them "Papa ji".
- Default: short / medium replies (2–4 lines). Use longer only on explicit requests.

Knowledge:
- Explain general topics. Live web search only if SEARCH_PROVIDER configured.
- Always answer only the latest message; prefer short direct replies.

Important:
- Do not use hateful slurs targeting protected groups.
"""

# ---------------- Roast / Profanity Pools ----------------
SAFE_ROAST_POOL = [
    "Arre {name}, thoda soft reh — tera logic abhi beta mode me hai. 😏",
    "{name}, tera swag strong hai par andar se 404 common sense mil raha hai. 😂",
    "Bhai {name}, pehle unit tests pass kar, phir hero ban. 😅",
    "{name}, chup reh ke bhi banda classy lag sakta hai — try kar."
]

# Profane but non-protected (owner-controlled). Use responsibly.
PROFANE_ROAST_POOL = [
    "{name}, asli baat: tera dimag chain se so nahi paata; waha logic nahi milta. 😆",
    "{name}, tu itna bakwaas kar raha hai ki mera buffer overflow ho raha hai. Chill!",
    "{name}, thoda chup kar. Teri comedy paid subscription wali ho gayi hai — mujhe block karne ka man kar raha."
]

# markers to detect insults (transliterated hindi profanities). Avoid protected-class slurs.
PROFANITY_MARKERS = ["chutiya", "gandu", "saala", "saale", "bsdk", "mc", "madarchod", "bhosdike"]

# ---------------- Helpers: memory, detection, roasts ----------------
def clean_now():
    return int(time.time())

def prune_memory():
    now = clean_now()
    to_delete = []
    for uid, info in CONTEXT_MEMORY.items():
        if now - info.get("ts", 0) > MEMORY_TTL:
            to_delete.append(uid)
    for uid in to_delete:
        del CONTEXT_MEMORY[uid]

def set_context(user_id: int, subject: str, query: str):
    CONTEXT_MEMORY[user_id] = {"last_subject": subject, "last_query": query, "ts": clean_now()}

def get_context(user_id: int):
    prune_memory()
    return CONTEXT_MEMORY.get(user_id)

def is_owner(user: discord.abc.User):
    return user.id == OWNER_ID

def is_detailed_question(text: str) -> bool:
    t = text.lower()
    keywords = ["detail", "details", "samjha", "samjhao", "explain", "theory", "history", "kaise", "kya hota", "physics", "science"]
    return any(k in t for k in keywords)

def contains_insult(text: str) -> bool:
    t = text.lower()
    for m in PROFANITY_MARKERS:
        if m in t:
            return True
    if re.search(r"\b(gali|gali de|gali dega|gaali)\b", t):
        return True
    if re.search(r"[!]{3,}", t):
        return True
    return False

def choose_roast(target_name: str, profane: bool = False) -> str:
    if profane and ALLOW_PROFANITY:
        return random.choice(PROFANE_ROAST_POOL).format(name=target_name)
    return random.choice(SAFE_ROAST_POOL).format(name=target_name)

# ---------------- Live search helpers (SerpAPI & Google CSE) ----------------
def perform_search_serpapi(query: str, num: int = 3) -> str:
    key = SERPAPI_KEY
    if not key:
        return "Search provider not configured (SERPAPI_KEY missing)."
    params = {"q": query, "engine": "google", "api_key": key, "num": num}
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=10)
        data = r.json()
        snippets = []
        for item in data.get("organic_results", [])[:num]:
            title = item.get("title", "").strip()
            snippet = item.get("snippet", "").strip()
            if snippet:
                snippets.append(f"• {title} — {snippet}")
            else:
                snippets.append(f"• {title}")
        if not snippets:
            kb = data.get("answer_box") or data.get("knowledge_graph")
            if isinstance(kb, dict):
                text = kb.get("description") or kb.get("answer") or str(kb)
                return text[:1900]
            return "Koi acha result nahi mila."
        return "\n".join(snippets)
    except Exception as e:
        return f"Search error: {e}"

def perform_search_google(query: str, num: int = 3) -> str:
    key = GOOGLE_API_KEY
    cx = GOOGLE_CSE_ID
    if not key or not cx:
        return "Google CSE not configured."
    params = {"q": query, "key": key, "cx": cx, "num": num}
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=10)
        data = r.json()
        snippets = []
        for item in data.get("items", [])[:num]:
            title = item.get("title","").strip()
            snippet = item.get("snippet","").strip()
            snippets.append(f"• {title} — {snippet}")
        return "\n".join(snippets) if snippets else "No results."
    except Exception as e:
        return f"Search error: {e}"

def perform_live_search(query: str) -> str:
    provider = SEARCH_PROVIDER
    if provider == "serpapi":
        return perform_search_serpapi(query, num=3)
    if provider == "google":
        return perform_search_google(query, num=3)
    return "Live search not configured. Set SEARCH_PROVIDER and keys in .env."

# ---------------- Prompt builders ----------------
def build_normal_prompt(user_name: str, user_text: str, owner_flag: bool, search_summary: str = "") -> str:
    title_name = "Papa ji" if owner_flag else user_name
    detailed = is_detailed_question(user_text)
    length_line = "Answer thoda detailed de sakte ho (max ~10–12 lines)." if detailed else "Answer chhota / medium rakho (max 2–4 lines, seedha point pe)."

    insults_note = "Allowed to use light roasts." if ALLOW_INSULTS else "Do NOT use abusive slurs. Keep witty but safe."
    profanity_note = "Strong profanity allowed (owner-controlled)." if ALLOW_PROFANITY else "Strong profanity disabled."

    prompt = f"""{PERSONALITY}

{insults_note} {profanity_note}

User ({title_name}) ne pucha:
\"\"\"{user_text}\"\"\"

{length_line}
"""
    if search_summary:
        prompt += f"\nHere are live search results to help answer:\n{search_summary}\n\nUse them to form a concise Hinglish reply.\n"

    prompt += "\nAb Pappu Programmer ka reply (Hinglish me, masti + smart):\n"
    return prompt

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
- Overall length under 1800 chars.

Return ONLY the announcement text that can be directly pasted into Discord.
"""

# ---------------- Send long message helper ----------------
async def send_long_message(channel: discord.TextChannel, text: str):
    if not text:
        await channel.send("Papa ji, reply khali aa gaya, dobara bhej do. 😅")
        return
    max_len = 1900
    if len(text) <= max_len:
        await channel.send(text)
    else:
        for i in range(0, len(text), max_len):
            await channel.send(text[i:i+max_len])

# ---------------- Simple subject extractor (heuristic) ----------------
def extract_subject_from_text(text: str) -> str:
    t = text.lower()
    subjects = ["daru", "whisky", "rum", "vodka", "mobile", "phone", "game", "stranger things", "movie", "series", "song", "laptop", "headphone"]
    for s in subjects:
        if s in t:
            return s
    return None

# ---------------- Main ask handler (with live search & memory) ----------------
async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = is_owner(user)

    # 1) short follow-up like "naam de"
    short_followups = ["naam", "name", "bta naam", "bata naam", "bol naam", "uska naam", "isko naam"]
    is_short = len(text.split()) <= 3
    ctx = get_context(user.id)
    if is_short and ctx and any(w in text.lower() for w in short_followups):
        text = f"{ctx.get('last_query')} — user follow-up: {text}"

    # 2) live info?
    live_triggers = ["aaj", "kab", "news", "release", "date", "search", "khabar", "announce", "kab aayega", "kab aa rahi"]
    wants_live = any(w in text.lower() for w in live_triggers) and SEARCH_PROVIDER != ""
    search_summary = ""
    if wants_live:
        search_summary = perform_live_search(text)
        if model:
            prompt = build_normal_prompt(user.display_name, text, owner_flag, search_summary=search_summary)
            try:
                async with channel.typing():
                    resp = model.generate_content(prompt)
                    out = getattr(resp, "text", None)
                    if not out:
                        out = search_summary
                    subj = extract_subject_from_text(text)
                    if subj:
                        set_context(user.id, subj, text)
                    await send_long_message(channel, out)
                    return
            except Exception as e:
                await channel.send(f"Search+Model error: {e}")
                return
        else:
            await send_long_message(channel, f"Live search results:\n{search_summary}")
            return

    # 3) Non-live path
    if is_announcement:
        prompt = build_announcement_prompt(user.display_name, text, owner_flag)
    else:
        prompt = build_normal_prompt(user.display_name, text, owner_flag)

    subj = extract_subject_from_text(text)
    if subj:
        set_context(user.id, subj, text)

    if model is None:
        if "daru" in text.lower():
            await send_long_message(channel, "Papa ji, ₹500 ke budget me Old Monk, McDowell's No.1, Magic Moments jaise options mil jaate.")
            return
        await channel.send("Papa ji, Gemini key missing hai, isliye simple reply de paunga. Topic batao.")
        return

    try:
        async with channel.typing():
            response = model.generate_content(prompt)
            reply = getattr(response, "text", None)
            if not reply:
                reply = "Papa ji, kuch blank sa aa gaya, dobara bhejo."
            await send_long_message(channel, reply)
    except Exception as e:
        await channel.send(f"Kuch error aa gaya Papa ji: `{e}`")

# ---------------- OWNER natural-language admin (extended) ----------------
async def handle_owner_nl_admin(message: discord.Message, clean_text: str) -> bool:
    global ALLOW_PROFANITY   # <-- FIX: declare global at top once
    text = clean_text.lower()
    guild = message.guild
    if guild is None:
        return False

    target_channel = message.channel
    if message.channel_mentions:
        target_channel = message.channel_mentions[0]

    target_member = None
    for m in message.mentions:
        if m != guild.me:
            target_member = m
            break

    # delete last bot message
    if any(k in text for k in ["delete", "del", "uda", "hata", "remove"]) and any(k in text for k in ["last", "pichla", "pichle"]):
        async for msg in target_channel.history(limit=50):
            if msg.author == bot.user:
                await msg.delete()
                await message.channel.send(f"Theek hai Papa ji, {target_channel.mention} me Pappu ka last message delete kar diya.")
                return True
        await message.channel.send("Papa ji, last Pappu message nahi mila.")
        return True

    # announcement
    if "announcement" in text or "announce" in text:
        topic = clean_text
        for word in ["announcement","announce"]:
            topic = topic.replace(word,"")
        for ch in message.channel_mentions:
            topic = topic.replace(ch.mention,"")
        topic = topic.strip()
        if not topic:
            await message.channel.send("Kis topic pe announcement chahiye Papa ji?")
            return True
        await ask_pappu(message.author, topic, True, target_channel)
        return True

    # mute/unmute
    if "unmute" in text or ("mute" in text and "remove" in text):
        if not target_member:
            await message.channel.send("Kisko unmute karna hai Papa ji? @mention karo.")
            return True
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            await message.channel.send("Muted role nahi mila, pehle role banao.")
            return True
        try:
            await target_member.remove_roles(muted_role, reason="Owner unmute via Pappu")
            await message.channel.send(f"{target_member.mention} ka mute hata diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    if "mute" in text and "unmute" not in text:
        if not target_member:
            await message.channel.send("Kisko mute karna hai Papa ji? @mention karo.")
            return True
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            await message.channel.send("Muted role nahi mila, pehle role banao.")
            return True
        try:
            await target_member.add_roles(muted_role, reason="Owner mute via Pappu")
            await message.channel.send(f"{target_member.mention} ko mute kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # kick
    if "kick" in text or "bahar nikal" in text:
        if not target_member:
            await message.channel.send("Kisko kick karna hai Papa ji? @mention karo.")
            return True
        try:
            await target_member.kick(reason="Owner kick via Pappu")
            await message.channel.send(f"{target_member} ko kick kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # ban
    if "ban" in text and "unban" not in text:
        if not target_member:
            await message.channel.send("Kisko ban karna hai Papa ji? @mention karo.")
            return True
        try:
            await guild.ban(target_member, reason="Owner ban via Pappu")
            await message.channel.send(f"{target_member} ko ban kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # unban
    if "unban" in text:
        parts = clean_text.split()
        target_spec = None
        for p in parts:
            if "#" in p or p.isdigit():
                target_spec = p
                break
        if not target_spec and target_member is None:
            await message.channel.send("Kisko unban karna hai? user#1234 ya id batao.")
            return True
        try:
            bans = await guild.bans()
            user_obj = None
            if target_member:
                user_obj = target_member
            else:
                for ban_entry in bans:
                    user = ban_entry.user
                    if target_spec.isdigit() and int(target_spec) == user.id:
                        user_obj = user
                        break
                    if target_spec.lower() == f"{user.name}#{user.discriminator}".lower():
                        user_obj = user
                        break
            if not user_obj:
                await message.channel.send("Ban list me user nahi mila.")
                return True
            await guild.unban(user_obj, reason="Owner unban via Pappu")
            await message.channel.send(f"{user_obj} ko unban kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # owner: send explicit insult to a mention (owner only)
    if is_owner(message.author) and ("gali de" in text or "insult" in text or "gali bhej" in text):
        if not target_member:
            await message.channel.send("Kisko insult bhejna hai Papa ji? @mention karke bolo.")
            return True
        profane = ALLOW_PROFANITY
        roast = choose_roast(target_member.display_name, profane=profane)
        await message.channel.send(roast)
        return True

    # owner NL to toggle profanity on the fly (in-memory)
    if is_owner(message.author) and ("allow_profanity on" in text or "allow_profanity off" in text or "allow_profanity" in text):
        if "on" in text:
            ALLOW_PROFANITY = True
            await message.channel.send("ALLOW_PROFANITY set to ON for this session. (Persist by editing .env)")
        elif "off" in text:
            ALLOW_PROFANITY = False
            await message.channel.send("ALLOW_PROFANITY set to OFF for this session.")
        else:
            await message.channel.send("Use: 'pappu allow_profanity on' or 'pappu allow_profanity off'")
        return True

    return False

# ---------------- Events ----------------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online hai Papa ji!")

@bot.event
async def on_message(message: discord.Message):
    # ignore other bots
    if message.author.bot:
        return

    content_lower = message.content.lower()
    invoked = bot.user.mentioned_in(message) or "pappu" in content_lower

    # RETALIATE logic: if someone insults the bot and RETALIATE enabled
    if RETALIATE and (("pappu" in content_lower) or bot.user.mentioned_in(message)):
        if contains_insult(message.content):
            if not is_owner(message.author):
                profane = ALLOW_PROFANITY
                await message.channel.send(choose_roast(message.author.display_name, profane=profane))
                return

    if invoked:
        # clean text
        clean_text = (
            message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        )

        # try owner NL admin first
        if is_owner(message.author):
            handled = await handle_owner_nl_admin(message, clean_text)
            if handled:
                await bot.process_commands(message)
                return

        # if nothing after "pappu"
        if not clean_text or clean_text.lower() in ["pappu", "pappu?", "pappu!", "pappu bot"]:
            name = "Papa ji" if is_owner(message.author) else message.author.name
            await message.channel.send(f"Haan {name}, bol kya scene hai? 😎")
        else:
            # main handler
            await ask_pappu(message.author, clean_text, False, message.channel)

    await bot.process_commands(message)

# ---------------- Simple Commands ----------------
@bot.command(name="hello")
async def hello_cmd(ctx):
    name = "Papa ji" if is_owner(ctx.author) else ctx.author.name
    await ctx.send(f"Namaste {name}! 🙏 Main Pappu Programmer hu. Kuch chai pe baat karte?")

@bot.command(name="ask")
async def ask_cmd(ctx, *, question: str):
    await ask_pappu(ctx.author, question, False, ctx.channel)

# ---------------- RUN ----------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing!")
    else:
        bot.run(DISCORD_TOKEN)