# ---------- PART 1: Imports, dotenv, globals ----------
import os
import sys
import re
import json
import time
import random
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv

# Google Gemini (optional but enabled)
import google.generativeai as genai

# Discord
import discord
from discord.ext import commands

# Load .env
load_dotenv()

# Environment / Config
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Optional search keys (for live search)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# Configure Gemini if key exists
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Gemini config error:", e)

# Bot init
PREFIX = "!"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS)

# Persistence file
PERSIST_FILE = Path("pappu_state.json")
# ---------- PART 2: Runtime settings & persistence ----------
# Runtime flags (will be persisted)
RUNTIME_SETTINGS: Dict[str, Any] = {
    "owner_dm_only": False,
    "stealth": False,
    "english_lock": False,     # True => only English; False => only Hinglish
    "allow_profanity": False,  # owner toggles this
    "mode": "funny",
    "memory": {}
}

# module-level convenience var (kept in sync with RUNTIME_SETTINGS)
ALLOW_PROFANITY = RUNTIME_SETTINGS["allow_profanity"]

def save_persistent_state():
    try:
        PERSIST_FILE.write_text(json.dumps(RUNTIME_SETTINGS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("Save state failed:", e)

def load_persistent_state():
    global RUNTIME_SETTINGS, ALLOW_PROFANITY
    try:
        if PERSIST_FILE.exists():
            data = json.loads(PERSIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                RUNTIME_SETTINGS.update(data)
                ALLOW_PROFANITY = RUNTIME_SETTINGS.get("allow_profanity", ALLOW_PROFANITY)
    except Exception as e:
        print("Load state failed:", e)

load_persistent_state()

def is_owner(user: discord.abc.User) -> bool:
    try:
        return user.id == OWNER_ID
    except Exception:
        return False
# ---------- PART 3: Roasts, language helpers, messaging ----------
# safe and profane roast pools (you can edit PROFANE_ROASTS on-host if needed)
SAFE_ROASTS = [
    "{name}, tera code dekh ke mera debugger bhi confuse ho gaya. 😂",
    "{name}, pehle chai pe lele, phir bug hunt karein.",
    "{name}, thoda soch ke bol, abhi toh argument ka bhi breakpoint lag gaya."
]
PROFANE_ROASTS = [
    "{name}, tu pagal hai ya logic ka hacker? Seedha dimag format kar doon? 😅",
    "{name}, tu itna bakwaas karta hai ki mera stack overflow ho raha hai.",
    "{name}, shut up and fix your life (aur code)."
]

# Simple profanity keywords for detection (customize on-host)
PROFANE_KEYWORDS = ["chutiya", "madarchod", "bhosd", "bc", "saale", "mc", "bsdk", "gandu"]

# Devanagari regex
DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')

def choose_roast(name: str, profane: bool = False) -> str:
    if profane:
        return random.choice(PROFANE_ROASTS).format(name=name)
    return random.choice(SAFE_ROASTS).format(name=name)

def choose_language_for_reply(_text: str) -> str:
    """
    Strict behavior per request:
    - If english_lock == True -> always 'en'
    - If english_lock == False -> always 'hi' (Hinglish)
    """
    if RUNTIME_SETTINGS.get("english_lock", False):
        return "en"
    return "hi"

async def send_long_message(channel: discord.abc.Messageable, text: str):
    """Send long text split into safe chunks."""
    if not text:
        await channel.send("Papa ji, reply thoda khali sa aa gaya, dobara bhejo.")
        return
    max_len = 1900
    for i in range(0, len(text), max_len):
        await channel.send(text[i:i+max_len])
        await asyncio.sleep(0.08)
# ---------- PART 4: Live search helpers (SerpAPI / GoogleCSE) ----------
def perform_search_serpapi(query: str) -> str:
    if not SERPAPI_KEY:
        return ""
    params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY}
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=8)
        j = r.json()
        out = []
        for item in j.get("organic_results", [])[:3]:
            title = item.get("title", "")
            snippet = item.get("snippet", "") or ""
            link = item.get("link", "")
            out.append(f"{title}\n{snippet}\n{link}")
        return "\n\n".join(out)
    except Exception:
        return ""

def perform_search_google(query: str) -> str:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return ""
    try:
        params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query}
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=8)
        j = r.json()
        out = []
        for it in j.get("items", [])[:3]:
            out.append(f"{it.get('title','')}\n{it.get('snippet','')}\n{it.get('link','')}")
        return "\n\n".join(out)
    except Exception:
        return ""

def perform_live_search(query: str) -> str:
    # prefer serpapi if key present
    if SERPAPI_KEY:
        res = perform_search_serpapi(query)
        if res:
            return res
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        res = perform_search_google(query)
        if res:
            return res
    return ""
# ---------- PART 5: ask_pappu (main reply handler) ----------
async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = is_owner(user)
    name = "Papa ji" if owner_flag else user.display_name

    # Language selection: strict per owner's english_lock
    lang = choose_language_for_reply(text)  # 'en' or 'hi'

    # Live search triggers
    triggers = ["search", "kab", "aaj", "news", "release", "lyrics", "kya", "kaun", "kab aayega", "price", "brand"]
    wants_live = any(t in (text or "").lower() for t in triggers)

    if wants_live:
        summary = perform_live_search(text)
        if summary:
            reply = f"Papa ji — live search results:\n\n{summary}"
            await send_long_message(channel, reply)
            return
        else:
            # fallback message
            await send_long_message(channel, "Papa ji, live search key/config missing ya result nahi mila.")
            return

    # Non-live: produce a canned / personality reply (you can integrate LLM here)
    mode = RUNTIME_SETTINGS.get("mode", "funny")
    if mode == "funny":
        base = f"Haan {name}, bol kya scene hai? 😎\nShort: {text[:200]}"
    elif mode == "serious":
        base = f"{name}, jawab seedha: {text[:400]}"
    else:
        base = f"{name}, main help karunga: {text[:300]}"

    # Language adaptation: keep a simple suffix for clarity
    if lang == "en":
        # ensure English phrasing (we're not translating here; we give English-style reply)
        reply = f"Hey {name}, (English mode) — {base}"
    else:
        # Hinglish reply
        reply = f"{base} (Hinglish mode)".

    # send
    await send_long_message(channel, reply)
# ---------- PART 6: SECRET ADMIN + OWNER NL ADMIN ----------
async def handle_secret_admin(message: discord.Message, clean_text: str) -> bool:
    if not is_owner(message.author):
        return False

    global ALLOW_PROFANITY
    text = (clean_text or "").lower().strip()

    # Shutdown
    if text in ("pappu shutdown", "pappu stop", "pappu sleep"):
        await message.channel.send("Theek hai Papa ji, going offline. 👋")
        save_persistent_state()
        await bot.close()
        return True

    # Restart
    if text in ("pappu restart", "pappu reboot"):
        await message.channel.send("Restarting now, Papa ji... 🔁")
        save_persistent_state()
        try:
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as e:
            await message.channel.send(f"Restart failed: `{e}` — please restart manually.")
        return True

    # owner_dm
    if text.startswith("pappu owner_dm"):
        if "on" in text:
            RUNTIME_SETTINGS["owner_dm_only"] = True
            save_persistent_state()
            await message.channel.send("Owner DM only mode ON.")
        elif "off" in text:
            RUNTIME_SETTINGS["owner_dm_only"] = False
            save_persistent_state()
            await message.channel.send("Owner DM only mode OFF.")
        else:
            await message.channel.send("Use: `pappu owner_dm on` / `pappu owner_dm off`")
        return True

    # stealth
    if text.startswith("pappu stealth"):
        if "on" in text:
            RUNTIME_SETTINGS["stealth"] = True
            save_persistent_state()
            await message.channel.send("Stealth ON.")
            try:
                await bot.change_presence(status=discord.Status.invisible)
            except Exception:
                pass
        elif "off" in text:
            RUNTIME_SETTINGS["stealth"] = False
            save_persistent_state()
            await message.channel.send("Stealth OFF.")
            try:
                await bot.change_presence(status=discord.Status.online)
            except Exception:
                pass
        else:
            await message.channel.send("Use: `pappu stealth on` / `pappu stealth off`")
        return True

    # mode
    if text.startswith("pappu mode"):
        parts = text.split()
        if len(parts) >= 3:
            candidate = parts[2]
            RUNTIME_SETTINGS["mode"] = candidate
            save_persistent_state()
            await message.channel.send(f"Mode set to `{candidate}`.")
        else:
            await message.channel.send("Usage: `pappu mode <name>`")
        return True

    # english lock strict: ON => only English, OFF => only Hinglish
    if text.startswith("pappu english"):
        if "on" in text:
            RUNTIME_SETTINGS["english_lock"] = True
            save_persistent_state()
            await message.channel.send("English-Lock ON. Ab sirf English me reply karunga.")
        elif "off" in text:
            RUNTIME_SETTINGS["english_lock"] = False
            save_persistent_state()
            await message.channel.send("English-Lock OFF. Ab sirf Hinglish me reply karunga.")
        else:
            await message.channel.send("Use: `pappu english on` / `pappu english off`")
        return True

    # profanity toggle
    if "allow_profanity" in text:
        if "on" in text:
            RUNTIME_SETTINGS["allow_profanity"] = True
            ALLOW_PROFANITY = True
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to ON. Owner-approved profanity enabled.")
        elif "off" in text:
            RUNTIME_SETTINGS["allow_profanity"] = False
            ALLOW_PROFANITY = False
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to OFF. Profanity disabled.")
        else:
            await message.channel.send("Use: `pappu allow_profanity on` / `pappu allow_profanity off`")
        return True

    # Guild-only admin commands (mute/kick/ban/etc.)
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
                try:
                    await msg.delete()
                except Exception:
                    pass
                await message.channel.send(f"{target_channel.mention} me last Pappu message delete kar diya.")
                return True
        await message.channel.send("Papa ji, last Pappu message nahi mila.")
        return True

    # announcement
    if "announcement" in text or "announce" in text:
        topic = clean_text
        for word in ["announcement", "announce"]:
            topic = topic.replace(word, "")
        for ch in message.channel_mentions:
            topic = topic.replace(ch.mention, "")
        topic = topic.strip()
        if not topic:
            await message.channel.send("Announcement kis topic par chahiye Papa ji?")
            return True
        await ask_pappu(message.author, topic, True, target_channel)
        return True

    # mute/unmute
    if "unmute" in text:
        if not target_member:
            await message.channel.send("Kisko unmute karna hai @mention karo.")
            return True
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            await message.channel.send("Muted role nahi mila.")
            return True
        try:
            await target_member.remove_roles(muted_role)
            await message.channel.send(f"{target_member.mention} ka mute hata diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    if "mute" in text and "unmute" not in text:
        if not target_member:
            await message.channel.send("Kisko mute karna hai @mention karo.")
            return True
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if not muted_role:
            await message.channel.send("Muted role nahi mila.")
            return True
        try:
            await target_member.add_roles(muted_role)
            await message.channel.send(f"{target_member.mention} ko mute kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # kick
    if "kick" in text or "bahar nikal" in text:
        if not target_member:
            await message.channel.send("Kisko kick karna hai @mention karo.")
            return True
        try:
            await target_member.kick()
            await message.channel.send(f"{target_member} ko kick kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # ban/unban
    if "ban" in text and "unban" not in text:
        if not target_member:
            await message.channel.send("Kisko ban karna hai @mention karo.")
            return True
        try:
            await guild.ban(target_member)
            await message.channel.send(f"{target_member} ko ban kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    if "unban" in text:
        parts = clean_text.split()
        target_spec = None
        for p in parts:
            if "#" in p or p.isdigit():
                target_spec = p
                break
        if not target_spec and target_member is None:
            await message.channel.send("Kisko unban karna hai? user#1234 ya ID batao.")
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
            await guild.unban(user_obj)
            await message.channel.send(f"{user_obj} ko unban kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # owner requested insult
    if is_owner(message.author) and ("gali de" in text or "insult" in text or "gali bhej" in text):
        if not target_member:
            await message.channel.send("Kisko insult bhejna hai @mention karo.")
            return True
        prof = RUNTIME_SETTINGS.get("allow_profanity", False)
        roast = choose_roast(target_member.display_name, profane=prof)
        await message.channel.send(roast)
        return True

    return False
# ---------- PART 7: Events (on_ready, on_message) + commands ----------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online hai Papa ji!")
    try:
        if RUNTIME_SETTINGS.get("stealth"):
            await bot.change_presence(status=discord.Status.invisible)
        else:
            await bot.change_presence(status=discord.Status.online)
    except Exception:
        pass

@bot.event
async def on_message(message: discord.Message):
    # ignore bots (including self)
    if message.author.bot:
        return

    content = message.content or ""
    content_lower = content.lower()

    # owner_dm_only enforcement (if on, ignore non-owner)
    if RUNTIME_SETTINGS.get("owner_dm_only", False) and not is_owner(message.author):
        return

    invoked = bot.user.mentioned_in(message) or ("pappu" in content_lower)

    if invoked:
        # clean mention tokens
        clean_text = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

        # Owner secret admin try first
        if is_owner(message.author):
            handled_secret = await handle_secret_admin(message, clean_text)
            if handled_secret:
                await bot.process_commands(message)
                return

        # ---------- AUTO-RETALIATE ON INSULTS ----------
        # place here so it runs before normal ask_pappu flow
        lowered = clean_text.lower()
        if any(k in lowered for k in PROFANE_KEYWORDS):
            # Only retaliate if owner allowed profanity
            if RUNTIME_SETTINGS.get("allow_profanity", False):
                roast = choose_roast(message.author.display_name, profane=True)
                await message.channel.send(roast)
                return
            else:
                # polite warn when profanity not allowed
                await message.channel.send("Papa ji, shishtachar rakho bhai. Aise mat bolo.")
                return

        # normal reply / ask handler
        if not clean_text or clean_text.lower() in ["pappu", "pappu?", "pappu!", "pappu bot"]:
            name = "Papa ji" if is_owner(message.author) else message.author.name
            await message.channel.send(f"Haan {name}, bol kya scene hai? 😎")
        else:
            await ask_pappu(message.author, clean_text, False, message.channel)

    await bot.process_commands(message)

# Simple commands
@bot.command(name="hello")
async def hello_cmd(ctx):
    name = "Papa ji" if is_owner(ctx.author) else ctx.author.name
    await ctx.send(f"Namaste {name}! 🙏 Main Pappu Programmer hu.")

@bot.command(name="ask")
async def ask_cmd(ctx, *, question: str):
    await ask_pappu(ctx.author, question, False, ctx.channel)
# ---------- PART 8: Run + compatibility alias ----------
# compatibility alias so older callsites still work
handle_owner_nl_admin = handle_secret_admin

if __name__ == "__main__":
    # ensure persisted setting loaded (already done at import)
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing in .env")
        sys.exit(1)
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        save_persistent_state()