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

# Yeh naam Pappu ke dimaag me "creator" ke liye fix rahega
CREATOR_NICK = os.getenv("CREATOR_NICK", "Papa Ji")

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
        PERSIST_FILE.write_text(
            json.dumps(RUNTIME_SETTINGS, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
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


def get_nice_name(user: discord.abc.User) -> str:
    """
    Pappu yahan se decide karega kisko kya bulaana hai.
    Owner ko hamesha 'Papa Ji', baaki ko display_name.
    """
    try:
        if is_owner(user):
            return CREATOR_NICK
        return getattr(user, "display_name", None) or getattr(user, "name", "User")
    except Exception:
        return "User"


def apply_mode(mode: str) -> bool:
    if not mode:
        return False
    mode = mode.lower()
    allowed = [
        "funny", "angry", "serious", "flirty", "sarcastic",
        "bhaukaal", "kid", "toxic", "coder", "bhai-ji", "dark", "normal"
    ]
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
    CONTEXT_MEMORY[user_id] = {
        "last_subject": subject,
        "last_query": query,
        "items": items or [],
        "ts": _now_ts()
    }


def get_context(user_id: int) -> Optional[Dict[str, Any]]:
    prune_memory()
    return CONTEXT_MEMORY.get(user_id)


async def resolve_target_user(message: discord.Message) -> discord.abc.User:
    """
    Decide kis user par Pappu ko focus karna chahiye.

    Priority:
    1) Jis message par reply kiya gaya (agar wo bot nahi hai)
    2) Pehla non-bot mention (Pappu bot ke alawa)
    3) Default: message ka author
    """
    try:
        # Reply target
        if message.reference:
            try:
                ref = await message.fetch_reference()
                if ref and ref.author and not ref.author.bot and ref.author != bot.user:
                    return ref.author
            except Exception:
                pass

        # Mention target
        for u in message.mentions:
            if u.bot or u == bot.user:
                continue
            return u
    except Exception:
        pass

    # Fallback
    return message.author
# ---------- PART 3: Roasts, profanity markers, language helpers, send_long_message ----------

# LIGHT roasts (safe)
SAFE_ROASTS = [
    "{name}, tera code dekh ke mera debugger bhi confuse ho gaya. 😂",
    "{name}, pehle chai pe lele, phir bug hunt karein. 😄",
    "{name}, thoda soch ke bol, abhi toh argument ka bhi breakpoint lag gaya. 😉",
    "{name}, tu try/except ke bina hi jee raha hai kya? 😆"
]

# RAW BRUTAL roasts (ye TU bhar dega apni gaali se)
# yahan jitni marzi lines add kar sakta hai, sirf {name} mat hataana
PROFANE_ROASTS = [
    "{name} chal nikal dalle apne baap ko mat sikha.",
    "{name} teri maa ki chut kam bol",
    "{name} kam bol bhosdike.",
    "{name} tere jese mere jhaant saaf krte hai.",
    "{name} Nikal be lowde.",
    "{name} Kahi aur ja k gaand marwa bhosdike."
    "{name} lowde k baal tu bhi bolega ab.",
    "{name} Teri maa ki chut bhadwe.",
    "{name} bhosdike sach mei tu ijjat k layak nhi hai.",
    "{name} Aandu gaandu nhi bolne lge ab.",
    "{name} Ja beta ja tera gaand marwane ka tym ho gya hai.",
    "{name} Aaja mc tatte saaf krde mere.",
    "{name} Apne Baap ko ese bolega bete ab.",
    "{name} Har baar gaand marwane mere paas hi q aata hai tu??.",
    "{name} jhaant k baal chup ho na.",
    "{name} iske muh mei koi loda deke chup kro."
    "{name} beta mene muh khola toh yahi chud jaega tu.",
    "{name} Gaandu hai tu smjha bhosdike.",
    "{name} lowde k baal Nikal yha se."
]

# Profanity detection keywords (customize on-host). Avoid protected-class slurs.
PROFANE_KEYWORDS = [
    "chutiya", "chuitya", "chutiye",
    "madarchod", "maderchod", "mc",
    "bhosd", "bhosdi", "bhosdike",
    "bc", "bsdk", "gandu", "gaand",
    "randi", "harami", "launde", "lode", "Teri maa ki chut", "behan k lode", "mkc"
]

# Language strictness: per your request english_lock == True => always English; False => only Hinglish
def choose_language_for_reply(_: str) -> str:
    return "en" if RUNTIME_SETTINGS.get("english_lock", False) else "hi"


# Devanagari detection (not used for strict mode but kept if needed)
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def choose_roast(name: str, profane: bool = False) -> str:
    # MIXED style (funny + savage + straight) – option D
    if profane:
        return random.choice(PROFANE_ROASTS).format(name=name)
    return random.choice(SAFE_ROASTS).format(name=name)


async def send_long_message(channel: discord.abc.Messageable, text: str):
    if not text:
        await channel.send("Papa ji, reply thoda khali sa aa gaya, dobara try karo.")
        return
    max_len = 1900
    for i in range(0, len(text), max_len):
        await channel.send(text[i:i + max_len])
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
            out.append(f"{it.get('title', '')}\n{it.get('snippet', '')}\n{it.get('link', '')}")
        return "\n\n".join(out)
    except Exception:
        return ""


def perform_live_search(query: str) -> str:
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
    mode = RUNTIME_SETTINGS.get("mode", "funny")
    tone = {
        "funny": "masti + light roast",
        "angry": "short + savage",
        "serious": "calm + informative",
        "flirty": "playful",
        "sarcastic": "sarcastic",
        "bhaukaal": "mafia-tone",
        "coder": "technical"
    }.get(mode, "masti + light roast")

    base_rules = (
        "You are a Discord bot named Pappu on a private Indian server. "
        "The word 'Pappu' is ONLY your own name. Never call any user 'Pappu'. "
        "Call users by their username or 'bhai'/'yaar'. "
    )

    if owner_flag:
        base_rules += (
            f"The current user is your creator and owner, called '{CREATOR_NICK}'. "
            "Always address them as 'Papa Ji' (or their set nickname) in the reply, "
            "not by their raw username. "
        )
    else:
        base_rules += (
            f"Your creator is '{CREATOR_NICK}', referred to as 'Papa Ji'. "
            "But do not assume every user is them. "
        )

    if lang == "hi":
        lang_preamble = (
            base_rules
            + "Reply in Hinglish (Hindi+English mix). Tone: "
            + tone
            + ". Keep answers short (2–6 lines) and avoid long lectures."
        )
    else:
        lang_preamble = (
            base_rules
            + "Reply in English. Tone: "
            + tone
            + ". Keep answers short (2–6 lines) and avoid long lectures."
        )

    prompt = f"""{lang_preamble}

User message: {user_text}

Answer concisely in chat style. If additional info from web is provided, you may use it.
Avoid monologues; keep it crisp and readable for Discord.
"""
    return prompt
# ---------- PART 5: Simplify previous reply + Hybrid ask_pappu ----------

async def simplify_previous_reply(
    user: discord.abc.User,
    original_message: discord.Message,
    instruction_text: str,
    channel: discord.abc.Messageable
):
    """
    Jab koi user Pappu ke kisi reply par 'isko asaan/simple way me bata' type
    reply kare, to yeh helper usi answer ka easy + short version nikalta hai.
    """
    owner_flag = is_owner(user)
    lang = choose_language_for_reply(original_message.content)

    if lang == "hi":
        intro = (
            "You are simplifying your OWN previous reply for the same user. "
            "Reply again in VERY easy, short Hinglish (Hindi + English mix). "
            "Use at most 3–4 short lines. No extra explanations."
        )
    else:
        intro = (
            "You are simplifying your OWN previous reply for the same user. "
            "Reply again in VERY easy, short English. "
            "Use at most 3–4 short lines. No extra explanations."
        )

    prompt = f"""{intro}

Original reply you sent earlier:
\"\"\"{original_message.content}\"\"\"

User's new message asking to simplify:
\"\"\"{instruction_text}\"\"\"

Now respond with a simpler, shorter version of your original reply, following the style rules.
"""

    if model is not None:
        try:
            async with channel.typing():
                resp = model.generate_content(prompt)
                out = getattr(resp, "text", None)
                if not out:
                    out = "Thoda simple version nahi bana paaya, Papa Ji. Ek baar fir se pooch lo."
                await send_long_message(channel, out)
                return
        except Exception as e:
            await channel.send(f"Gemini error/timeout while simplifying: {e}")
            return

    # Fallback: original reply ko hi thoda trim karke bhej do
    txt = original_message.content or ""
    if len(txt) > 400:
        txt = txt[:350] + "..."
    await send_long_message(channel, txt)


# ✅ NEW: Detailed expansion helper
async def expand_previous_reply(
    user: discord.abc.User,
    original_message: discord.Message,
    instruction_text: str,
    channel: discord.abc.Messageable
):
    """
    Jab user bole 'thoda detail me samjha' type reply,
    to Pappu apne hi pichhle reply ko zyada DETAIL me explain kare.
    """
    lang = choose_language_for_reply(original_message.content)

    if lang == "hi":
        intro = (
            "You are expanding your OWN previous reply for the same user. "
            "Explain the SAME concept in more DETAIL in clear Hinglish. "
            "Use around 6–10 short lines, add 1–2 simple real-life examples. "
            "Avoid extra jokes; focus on understanding."
        )
    else:
        intro = (
            "You are expanding your OWN previous reply for the same user. "
            "Explain the SAME concept in more DETAIL in clear English. "
            "Use around 6–10 short lines, add 1–2 simple real-life examples. "
            "Avoid extra jokes; focus on understanding."
        )

    prompt = f"""{intro}

Original reply you sent earlier:
\"\"\"{original_message.content}\"\"\"

User's new message asking for more detail:
\"\"\"{instruction_text}\"\"\"

Now respond with a more detailed explanation of your original reply, following the style rules.
"""

    if model is not None:
        try:
            async with channel.typing():
                resp = model.generate_content(prompt)
                out = getattr(resp, "text", None)
                if not out:
                    out = "Detail me samjhate waqt thoda issue aaya, Papa Ji. Ek baar fir se try kar lo."
                await send_long_message(channel, out)
                return
        except Exception as e:
            await channel.send(f"Gemini error/timeout while expanding: {e}")
            return

    # Fallback: original reply hi bhej do (at least kuch toh mile)
    txt = original_message.content or ""
    await send_long_message(channel, txt)


async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = is_owner(user)
    name = get_nice_name(user)

    # strict language choice per owner's english_lock setting
    lang = choose_language_for_reply(text)  # 'en' or 'hi'

    # improved follow-up resolution using short context
    ctx = get_context(user.id)
    short_followups = [
        "naam", "name",
        "bta", "bata",
        "aur", "or", "bhi",
        "wahi", "same",
        "desh", "country",
        "phir", "fir", "next",
        "ek aur"
    ]
    if ctx and len(text.split()) <= 8 and any(w in text.lower() for w in short_followups):
        items = ctx.get("items") or []
        if items:
            text = f"{ctx.get('last_query')} — items: {', '.join(items[:6])} — follow-up: {text}"
        else:
            text = f"{ctx.get('last_query')} — follow-up: {text}"

    # determine if user likely wants live info
    triggers = [
        "search", "kab", "aaj", "news", "release", "lyrics",
        "khabar", "price", "brand", "date", "kab aayega"
    ]
    wants_live = any(t in (text or "").lower() for t in triggers)

    search_summary = ""
    if wants_live:
        search_summary = perform_live_search(text)
        if not search_summary:
            await send_long_message(channel, "Papa ji, live-search keys/config missing ya result nahi mila.")
            return

    # If Gemini model present, prefer it
    if model is not None:
        prompt = build_normal_prompt(user.display_name, text, owner_flag, lang)
        if search_summary:
            prompt += f"\nSearch results for you to optionally use:\n{search_summary}\n\n"

        # If announcement, slightly change instruction
        if is_announcement:
            announce_intro = (
                "Now write a Discord announcement. "
                "Give a bold-style title line (using ** ** around it) and 3–6 short bullet points. "
            )
            prompt = announce_intro + "\n\n" + prompt

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
                    subj = "general"
                    if "extract_subject_from_text" in globals():
                        try:
                            subj = extract_subject_from_text(text)  # type: ignore
                        except Exception:
                            pass
                    set_context(user.id, subj, text, items=items)

                await send_long_message(channel, out)
                return
        except Exception as e:
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
    if any(k in text for k in ["delete", "del", "uda", "hata", "remove"]) and any(
        k in text for k in ["last", "pichla", "pichle"]
    ):
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

    # unmute/mute/kick/ban/unban logic (story vs command)
    # UNMUTE
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

    # MUTE
    if "mute" in text and "unmute" not in text:
        if not target_member:
            # no direct mention => treat as baat-cheet, not command
            await message.channel.send(
                "Samajh gaya Papa ji, kisi ka mute scene chal raha hai. "
                "Agar mujhe mute karwana ho to @mention ke saath bolo. 🙂"
            )
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

    # KICK
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

    # BAN
    if "ban" in text and "unban" not in text:
        if not target_member:
            # yahan ab Pappu ulta "mention karo" nahi bolega – sirf samjhega ki info di gayi hai
            await message.channel.send(
                "Samajh gaya Papa ji, kisi ko ban kiya gaya hai ya ban ki baat ho rahi hai. "
                "Agar mujhe kisi ko ban karwana ho to @mention ke saath bolna. 🙂"
            )
            return True
        try:
            await guild.ban(target_member)
            await message.channel.send(f"{target_member} ko ban kar diya.")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return True

    # UNBAN
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

    # owner-requested insult (ye HI dusro ko roast karega, baaki auto nahi)
    if is_owner(message.author) and any(
        kw in text
        for kw in [
            "gali de", "gaali de",
            "gali do", "gaali do",
            "gali bhej", "gaali bhej"
        ]
    ):
        if not target_member:
            await message.channel.send("Kisko insult bhejna hai @mention karo.")
            return True
        prof = RUNTIME_SETTINGS.get("allow_profanity", False)
        roast = choose_roast(get_nice_name(target_member), profane=prof)
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

    # ---------- SUPER FOLLOW-UP HANDLER (Detail expansion on reply) ----------
    if (
        message.reference
        and message.reference.resolved
        and message.reference.resolved.author == bot.user
    ):
        detail_keywords = [
            "detail", "details", "thoda detail", "thodi detail",
            "zyada detail", "aur detail", "deep me", "deep mein",
            "in depth", "zyada smjha", "zyada samjha"
        ]
        if any(k in content_lower for k in detail_keywords):
            original = message.reference.resolved
            await expand_previous_reply(message.author, original, content, message.channel)
            await bot.process_commands(message)
            return

    # owner_dm_only enforcement
    if RUNTIME_SETTINGS.get("owner_dm_only", False) and not is_owner(message.author):
        return

    # ---------------------------------------
    # STEP 1: reply-par "isko simple/asan way me bta" detection
    # ---------------------------------------
    simplify_keywords = [
        "simple", "simpler", "easy", "easy way",
        "asan", "aasan", "aasaan",
        "short", "chhota", "chota",
        "aasaan way", "asan way"
    ]
    ref_msg = None
    if message.reference:
        try:
            ref_msg = await message.fetch_reference()
        except Exception:
            ref_msg = None

        if ref_msg and ref_msg.author == bot.user:
            if any(k in content_lower for k in simplify_keywords):
                await simplify_previous_reply(message.author, ref_msg, content, message.channel)
                await bot.process_commands(message)
                return

    # ---------------------------------------
    # STEP 2: Invocation detection
    #  - mention
    #  - "pappu" in text
    #  - reply to Pappu (bina naam likhe)
    # ---------------------------------------
    invoked = bot.user.mentioned_in(message) or ("pappu" in content_lower)
    if not invoked and ref_msg and ref_msg.author == bot.user:
        invoked = True

    if invoked:
        clean_text = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        lowered = clean_text.lower()

        # Owner secret admin try first
        if is_owner(message.author):
            handled_secret = await handle_secret_admin(message, clean_text)
            if handled_secret:
                await bot.process_commands(message)
                return

        # -----------------------------------
        # Creator / Owner questions
        # -----------------------------------
        creator_triggers = [
            "kisne banaya", "kisne tumhe banaya", "kisne tume banaya",
            "who made you", "who created you", "creator kaun",
            "developer kaun", "programmer kaun",
            "owner kaun", "tumhara owner", "tumhara malik",
            "papa kaun", "pappa kaun", "papa ji kaun"
        ]
        if any(kw in lowered for kw in creator_triggers):
            await message.channel.send(
                f"Mujhe mere creator {CREATOR_NICK} ne banaya hai – "
                f"yahi mere 'Papa Ji' hain is server pe. 😎"
            )
            await bot.process_commands(message)
            return

        # -----------------------------------
        # AUTO-RETALIATE ON INSULTS – MODE A
        #  sirf jab Pappu ko gaali di ho
        # -----------------------------------
        has_profanity = any(k in lowered for k in PROFANE_KEYWORDS)
        insult_to_bot = False

        if has_profanity:
            # Case 1: reply to Pappu ke message pe gaali
            if ref_msg and ref_msg.author == bot.user:
                insult_to_bot = True
            # Case 2: text me 'pappu' + gaali, aur kisi aur user ka @mention nahi
            elif "pappu" in lowered and not message.mentions:
                insult_to_bot = True

        if has_profanity and insult_to_bot:
            if RUNTIME_SETTINGS.get("allow_profanity", False):
                # self-defense: gaali dene wala banda = author
                roaster_name = get_nice_name(message.author)
                roast = choose_roast(roaster_name, profane=True)
                await message.channel.send(roast)
                await bot.process_commands(message)
                return
            else:
                await message.channel.send("Papa ji, shishtachar rakho. Aise mat bolo.")
                await bot.process_commands(message)
                return

        # -----------------------------------
        # Normal chat
        # -----------------------------------
        if not clean_text or clean_text.lower() in ["pappu", "pappu?", "pappu!", "pappu bot"]:
            name = get_nice_name(message.author)
            await message.channel.send(f"Haan {name}, bol kya scene hai? 😎")
        else:
            await ask_pappu(message.author, clean_text, False, message.channel)

    await bot.process_commands(message)


# Simple commands
@bot.command(name="hello")
async def hello_cmd(ctx):
    name = get_nice_name(ctx.author)
    await ctx.send(f"Namaste {name}! 🙏 Main Pappu Programmer hu.")


@bot.command(name="ask")
async def ask_cmd(ctx, *, question: str):
    await ask_pappu(ctx.author, question, False, ctx.channel)
# ---------- PART 8: Run + alias + final save ----------
# compatibility alias so older callsites keep working
handle_owner_nl_admin = handle_secret_admin

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing in .env")
        sys.exit(1)
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        save_persistent_state()