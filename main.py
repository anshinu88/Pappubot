# ---------- PART 1: Imports, dotenv, globals, Gemini config ----------
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

# Gemini (Google generative AI)
import google.generativeai as genai

# Discord
import discord
from discord.ext import commands

# Load .env
load_dotenv()

# Environment / Config
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Gemini API key (optional)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Live-search keys (optional)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# Configure Gemini safely if key present
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Warning: Gemini config failed:", e)

# Bot init
PREFIX = "!"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS)

# Persistence file
PERSIST_FILE = Path("pappu_state.json")
# ---------- PART 2: Runtime settings, persistence, model init, helpers ----------
RUNTIME_SETTINGS: Dict[str, Any] = {
    "owner_dm_only": False,
    "stealth": False,
    "english_lock": False,     # True => only English replies; False => only Hinglish
    "allow_profanity": False,  # owner toggles this
    "mode": "funny",
    "memory": {}
}

# convenience var (kept in sync)
ALLOW_PROFANITY = RUNTIME_SETTINGS.get("allow_profanity", False)

def save_persistent_state():
    try:
        PERSIST_FILE.write_text(json.dumps(RUNTIME_SETTINGS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("Warning: failed saving persistent state:", e)

def load_persistent_state():
    global RUNTIME_SETTINGS, ALLOW_PROFANITY
    try:
        if PERSIST_FILE.exists():
            data = json.loads(PERSIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                RUNTIME_SETTINGS.update(data)
                ALLOW_PROFANITY = RUNTIME_SETTINGS.get("allow_profanity", ALLOW_PROFANITY)
    except Exception as e:
        print("Warning: failed loading persistent state:", e)

# Load persisted settings at startup
load_persistent_state()

# Initialize Gemini model object if configured (safe)
try:
    model = genai.GenerativeModel("gemini-2.5-flash") if GEMINI_API_KEY else None
except Exception as e:
    print("Warning: Gemini model init failed:", e)
    model = None

# Basic helpers
def is_owner(user: discord.abc.User) -> bool:
    try:
        return user.id == OWNER_ID
    except Exception:
        return False

def apply_mode(mode: str) -> bool:
    if not mode:
        return False
    mode = mode.lower()
    allowed = ["funny","angry","serious","flirty","sarcastic","bhaukaal","kid","toxic","coder","bhai-ji","dark","normal"]
    if mode not in allowed:
        return False
    if mode == "normal":
        mode = "funny"
    RUNTIME_SETTINGS["mode"] = mode
    save_persistent_state()
    return True

# Short context memory (per-user, transient)
CONTEXT_MEMORY: Dict[int, Dict[str, Any]] = {}
MEMORY_TTL = 60 * 60 * 6  # 6 hours

def _now_ts() -> int:
    return int(time.time())

def prune_memory():
    now = _now_ts()
    for uid in list(CONTEXT_MEMORY.keys()):
        if now - CONTEXT_MEMORY[uid].get("ts", 0) > MEMORY_TTL:
            CONTEXT_MEMORY.pop(uid, None)

def set_context(user_id: int, subject: str, query: str, items: Optional[List[str]] = None):
    if not user_id:
        return
    CONTEXT_MEMORY[user_id] = {"last_subject": subject, "last_query": query, "items": items or [], "ts": _now_ts()}

def get_context(user_id: int) -> Optional[Dict[str, Any]]:
    prune_memory()
    return CONTEXT_MEMORY.get(user_id)
# ---------- PART 3: Roasts, profanity markers, language helpers, send_long_message ----------
# Roasts (safe and stronger)
SAFE_ROASTS = [
    "{name}, tera code dekh ke mera debugger bhi confuse ho gaya. 😂",
    "{name}, pehle chai pe lele, phir bug hunt karein.",
    "{name}, thoda soch ke bol, abhi toh argument ka bhi breakpoint lag gaya."
]
PROFANE_ROASTS = [
    "{name}, tu asli baklol nikla yaar — fix kar warna main aag laga dunga. 😅",
    "{name}, tera logic itna weak hai ki mera try/except bhi fail kar raha.",
    "{name}, chill kar aur phir se padh, warna gaali se kaam nahi chalega."
]

# Profanity detection keywords (customize on-host). Avoid protected-class slurs.
PROFANE_KEYWORDS = ["chutiya", "madarchod", "bhosd", "bc", "saale", "mc", "bsdk", "gandu"]

# Language strictness: per your request english_lock == True => always English; False => always Hinglish
def choose_language_for_reply(_: str) -> str:
    return "en" if RUNTIME_SETTINGS.get("english_lock", False) else "hi"

# Devanagari detection (not used for strict mode but kept if needed)
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

def choose_roast(name: str, profane: bool = False) -> str:
    if profane:
        return random.choice(PROFANE_ROASTS).format(name=name)
    return random.choice(SAFE_ROASTS).format(name=name)

async def send_long_message(channel: discord.abc.Messageable, text: str):
    if not text:
        await channel.send("Papa ji, reply thoda khali sa aa gaya, dobara try karo.")
        return
    max_len = 1900
    for i in range(0, len(text), max_len):
        await channel.send(text[i:i+max_len])
        await asyncio.sleep(0.06)
# ---------- PART 4: Live-search helpers + prompt builder ----------
def perform_search_serpapi(query: str, num: int = 3) -> str:
    if not SERPAPI_KEY:
        return ""
    params = {"engine": "google", "q": query, "num": num, "api_key": SERPAPI_KEY}
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=8)
        data = r.json()
        out = []
        for item in data.get("organic_results", [])[:num]:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            out.append(f"{title}\n{snippet}\n{link}")
        return "\n\n".join(out)
    except Exception:
        return ""

def perform_search_google(query: str, num: int = 3) -> str:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return ""
    try:
        params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": num}
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=8)
        data = r.json()
        out = []
        for it in data.get("items", [])[:num]:
            out.append(f"{it.get('title','')}\n{it.get('snippet','')}\n{it.get('link','')}")
        return "\n\n".join(out)
    except Exception:
        return ""

def perform_live_search(query: str) -> str:
    # prefer serpapi
    if SERPAPI_KEY:
        s = perform_search_serpapi(query)
        if s:
            return s
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        s = perform_search_google(query)
        if s:
            return s
    return ""

def build_normal_prompt(user_name: str, user_text: str, owner_flag: bool, lang: str) -> str:
    title = "Papa ji" if owner_flag else user_name
    mode = RUNTIME_SETTINGS.get("mode", "funny")
    tone = {
        "funny":"masti + light roast",
        "angry":"short + savage",
        "serious":"calm + informative",
        "flirty":"playful",
        "sarcastic":"sarcastic",
        "bhaukaal":"mafia-tone",
        "coder":"technical"
    }.get(mode, "masti + light roast")
    if lang == "hi":
        lang_preamble = "Reply in Hinglish (Hindi+English). Tone: " + tone
    else:
        lang_preamble = "Reply in English. Tone: " + tone
    prompt = f"""{lang_preamble}
User: {user_text}
Answer concisely (2-6 lines). If additional info from web provided, use it.

"""
    return prompt
# ---------- PART 5: Hybrid ask_pappu (Gemini + live-search) ----------
async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = is_owner(user)
    name = "Papa ji" if owner_flag else user.display_name

    # strict language choice per owner's english_lock setting
    lang = choose_language_for_reply(text)  # 'en' or 'hi'

    # quick follow-up resolution using short context
    ctx = get_context(user.id)
    short_followups = ["naam","name","bta","bata","kaun","kis","kis ka"]
    if ctx and len(text.split()) <= 5 and any(w in text.lower() for w in short_followups):
        items = ctx.get("items") or []
        if items:
            text = f"{ctx.get('last_query')} — items: {', '.join(items[:6])} — follow-up: {text}"
        else:
            text = f"{ctx.get('last_query')} — follow-up: {text}"

    # determine if user likely wants live info
    triggers = ["search","kab","aaj","news","release","lyrics","khabar","price","brand","date","kab aayega"]
    wants_live = any(t in (text or "").lower() for t in triggers)

    search_summary = ""
    if wants_live:
        search_summary = perform_live_search(text)
        if not search_summary:
            # if user explicitly asked live info and keys missing, tell them
            await send_long_message(channel, "Papa ji, live-search keys/config missing ya result nahi mila.")
            return

    # If Gemini model present, prefer it
    if model is not None:
        prompt = build_normal_prompt(user.display_name, text, owner_flag, lang)
        if search_summary:
            prompt += f"\nSearch results:\n{search_summary}\n\nUse these to answer accurately.\n"
        # If announcement, slightly change instruction
        if is_announcement:
            prompt = ("Write a Discord announcement in " + ("Hinglish." if lang=="hi" else "English.") +
                      " Provide bold title line and 3-6 bullets.\n\n") + prompt

        try:
            async with channel.typing():
                resp = model.generate_content(prompt)
                out = getattr(resp, "text", None)
                if not out:
                    out = search_summary or "Papa ji, thoda blank sa aa gaya. Dobara bhejo."
                # save context items if extractable
                items = []
                for line in out.splitlines():
                    l = line.strip()
                    if l and len(l.split()) <= 6 and len(l) < 120:
                        items.append(l)
                    if len(items) >= 8:
                        break
                if items:
                    set_context(user.id, extract_subject_from_text(text) if 'extract_subject_from_text' in globals() else "general", text, items=items)
                await send_long_message(channel, out)
                return
        except Exception as e:
            # Gemini error => fallback to simple reply
            await channel.send(f"Gemini error/timeout: {e}. Falling back to simple reply.")

    # If no model or Gemini failed:
    if search_summary:
        await send_long_message(channel, f"Live search results:\n\n{search_summary}")
        return

    # Simple fallback reply (canned)
    mode = RUNTIME_SETTINGS.get("mode", "funny")
    if mode == "funny":
        base = f"Haan {name}, bol kya scene hai? 😎\nShort: {text[:200]}"
    elif mode == "serious":
        base = f"{name}, jawab seedha: {text[:300]}"
    else:
        base = f"{name}, main help karunga: {text[:300]}"

    if lang == "en":
        reply = f"Hey {name}, (English mode) — {base}"
    else:
        reply = f"{base} (Hinglish mode)"
    await send_long_message(channel, reply)
# ---------- PART 6: SECRET ADMIN + OWNER NL ADMIN ----------
async def handle_secret_admin(message: discord.Message, clean_text: str) -> bool:
    if not is_owner(message.author):
        return False

    global ALLOW_PROFANITY
    text = (clean_text or "").lower().strip()

    # shutdown
    if text in ("pappu shutdown", "pappu stop", "pappu sleep"):
        await message.channel.send("Theek hai Papa ji, going offline. 👋")
        save_persistent_state()
        try:
            await bot.close()
        except Exception:
            os._exit(0)
        return True

    # restart
    if text in ("pappu restart", "pappu reboot"):
        await message.channel.send("Restarting now, Papa ji... 🔁")
        save_persistent_state()
        try:
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as e:
            await message.channel.send(f"Restart failed: `{e}` — restart from panel.")
        return True

    # owner_dm toggle
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
        if len(parts) >= 3 and apply_mode(parts[2]):
            await message.channel.send(f"Mode set to `{parts[2]}`.")
        else:
            await message.channel.send("Usage: `pappu mode funny|angry|serious|...`")
        return True

    # english strict toggle (owner)
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

    # profanity toggle (owner)
    if "allow_profanity" in text:
        if "on" in text:
            RUNTIME_SETTINGS["allow_profanity"] = True
            ALLOW_PROFANITY = True
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to ON (owner-approved).")
        elif "off" in text:
            RUNTIME_SETTINGS["allow_profanity"] = False
            ALLOW_PROFANITY = False
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to OFF.")
        else:
            await message.channel.send("Use: `pappu allow_profanity on` / `pappu allow_profanity off`")
        return True

    # Guild-only admin commands
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
    if any(k in text for k in ["delete","del","uda","hata","remove"]) and any(k in text for k in ["last","pichla","pichle"]):
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
        for word in ["announcement","announce"]:
            topic = topic.replace(word,"")
        for ch in message.channel_mentions:
            topic = topic.replace(ch.mention,"")
        topic = topic.strip()
        if not topic:
            await message.channel.send("Announcement kis topic par chahiye Papa ji?")
            return True
        await ask_pappu(message.author, topic, True, target_channel)
        return True

    # unmute/mute/kick/ban/unban logic (same as earlier blocks)
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

    # owner-requested insult
    if is_owner(message.author) and ("gali de" in text or "insult" in text or "gali bhej" in text):
        if not target_member:
            await message.channel.send("Kisko insult bhejna hai @mention karo.")
            return True
        prof = RUNTIME_SETTINGS.get("allow_profanity", False)
        roast = choose_roast(target_member.display_name, profane=prof)
        await message.channel.send(roast)
        return True

    return False
# ---------- PART 7: Events + commands (on_message includes auto-retaliation) ----------
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
    if message.author.bot:
        return

    content = message.content or ""
    content_lower = content.lower()

    # owner_dm_only enforcement
    if RUNTIME_SETTINGS.get("owner_dm_only", False) and not is_owner(message.author):
        return

    invoked = bot.user.mentioned_in(message) or ("pappu" in content_lower)

    if invoked:
        clean_text = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

        # Owner secret admin try first
        if is_owner(message.author):
            handled_secret = await handle_secret_admin(message, clean_text)
            if handled_secret:
                await bot.process_commands(message)
                return

        # ---------- AUTO-RETALIATE ON INSULTS ----------
        lowered = clean_text.lower()
        if any(k in lowered for k in PROFANE_KEYWORDS):
            if RUNTIME_SETTINGS.get("allow_profanity", False):
                roast = choose_roast(message.author.display_name, profane=True)
                await message.channel.send(roast)
                return
            else:
                await message.channel.send("Papa ji, shishtachar rakho. Aise mat bolo.")
                return

        # Normal chat
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
# ---------- PART 8: Run + alias + final save ----------
# compatibility alias so older callsites keep working
handle_owner_nl_admin = handle_secret_admin

if __name__ == "__main__":
    # ensure persisted values loaded (already loaded at import)
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing in .env")
        sys.exit(1)
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        save_persistent_state()