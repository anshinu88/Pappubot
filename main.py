# -------------------- main.py (PART 1/8) --------------------
import os
import sys
import re
import json
import time
import random
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

# Discord imports (discord.py v2)
import discord
from discord.ext import commands, tasks
from discord import app_commands

# Optional Google generative ai (if installed & key present)
try:
    import google.generativeai as genai
except Exception:
    genai = None

load_dotenv()

# -------------------- CONFIG / ENV --------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0")) if os.getenv("LOG_CHANNEL_ID") else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# Files (host-side)
PERSIST_FILE = Path("pappu_state.json")
SLURS_FILE = "slurs.txt"          # one keyword/phrase per line (host-side)
ROASTS_STRONG_FILE = "roasts_strong.txt"  # optional host file for owner raw templates

# Bot setup
PREFIX = "!"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS)
tree = bot.tree  # for slash commands

# Configure Gemini if available
if GEMINI_API_KEY and genai is not None:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Warning: Gemini configure failed:", e)
# -------------------- main.py (PART 2/8) --------------------
# -------------------- RUNTIME SETTINGS + PERSISTENCE --------------------
RUNTIME_SETTINGS: Dict[str, Any] = {
    "owner_dm_only": False,
    "stealth": False,
    "english_lock": False,
    "allow_profanity": False,
    "auto_retaliate": False,
    "auto_retaliate_cooldown": 60,
    "mode": "funny",
    "memory": {}
}

def save_persistent_state():
    try:
        PERSIST_FILE.write_text(json.dumps(RUNTIME_SETTINGS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("Warning: failed saving persistent state:", e)

def load_persistent_state():
    global RUNTIME_SETTINGS
    try:
        if PERSIST_FILE.exists():
            data = json.loads(PERSIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                RUNTIME_SETTINGS.update(data)
    except Exception as e:
        print("Warning: failed loading persistent state:", e)

load_persistent_state()

# -------------------- MODEL (optional) --------------------
model = None
if GEMINI_API_KEY and genai is not None:
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        print("Warning: Gemini model init failed:", e)
        model = None

# -------------------- CONTEXT MEMORY (in-memory + persisted) --------------------
CONTEXT_MEMORY: Dict[int, Dict[str, Any]] = {}
MEMORY_TTL = 60 * 60 * 6  # 6 hours TTL for simple memory
# -------------------- main.py (PART 3/8) --------------------
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

# -------------------- SLURS LOADER & STRONG ROASTS LOADER --------------------
def load_slurs():
    slurs = set()
    try:
        p = os.path.join(os.getcwd(), SLURS_FILE)
        with open(p, "r", encoding="utf8") as f:
            for line in f:
                w = line.strip()
                if not w or w.startswith("#"):
                    continue
                slurs.add(w.lower())
    except FileNotFoundError:
        pass
    except Exception as e:
        print("Error loading slurs:", e)
    return slurs

def load_strong_roasts():
    roasts = []
    try:
        p = os.path.join(os.getcwd(), ROASTS_STRONG_FILE)
        with open(p, "r", encoding="utf8") as f:
            for line in f:
                t = line.strip()
                if t and not t.startswith("#"):
                    roasts.append(t)
    except Exception:
        pass
    return roasts

PROFANE_KEYWORDS = load_slurs()
ROASTS_STRONG = load_strong_roasts()
# -------------------- main.py (PART 4/8) --------------------
# background periodic reload (optional)
@tasks.loop(minutes=5.0)
async def periodic_reload_slurs():
    global PROFANE_KEYWORDS
    PROFANE_KEYWORDS = load_slurs()

@periodic_reload_slurs.before_loop
async def before_reload():
    await bot.wait_until_ready()

periodic_reload_slurs.start()

# -------------------- ROASTS, MASKING, HELPERS --------------------
SAFE_ROASTS = [
    "{name}, tera logic dekh ke mere debugger ko chhutti leni padi. 😂",
    "{name}, thoda sambhal ke bol — tumhara drama unnecessary hai.",
    "{name}, pehle apna ego update kar, phir awaaz nikaal."
]

def mask_slur(slur: str) -> str:
    s = slur.strip()
    if len(s) <= 2:
        return s[0] + "*"*(len(s)-1) if len(s)>1 else "*"
    return s[0] + "*"*(len(s)-2) + s[-1]

def choose_roast(display_name: str, profane: bool = False, matched_slur: str = None) -> str:
    name = display_name or "bhai"
    if profane:
        if matched_slur:
            m = mask_slur(matched_slur)
            templates = [
                "Arre {name}, ab itna beizzati mat kar — log tumhe '{m}' bol rahe hain, soch le.",
                "{name}, tu sach mein {m} jaisa behave kar raha hai — thoda sambhal.",
                "Bhai {name}, {m} bol ke kitna girna hai tujhe? Thoda dignity rakh."
            ]
            return random.choice(templates).format(name=name, m=m)
        templates = [
            "{name}, ab band kar warna tera sach sach expose kar dunga.",
            "{name}, tera attitude itna low hai ki log ignore kar rahe hain."
        ]
        return random.choice(templates).format(name=name)
    else:
        return random.choice(SAFE_ROASTS).format(name=name)
# -------------------- main.py (PART 5/8) --------------------
# human-like roast sets (for extra customization)
HUMAN_SOFT_ROASTS = [
    "{name}, bhai thoda shaant ho ja — tera drama unnecessary hai.",
    "Oye {name}, bolne se pehle soch le, warna log hans ke kahenge 'arre yeh kya bol raha'.",
    "{name}, tera logic thoda weak lag raha hai — ek baar dimag on kar le.",
]

HUMAN_STRONG_ROASTS = [
    "{name}, tune jo bola usse lagta hai tumne life me thoda kam dekha hai.",
    "Bhai {name}, seriously — aise bol ke apni izzat mat gira.",
    "{name}, tera attitude itna cheap hai ki log seedha turn off kar dete hain.",
]

HUMAN_EXTRA_STRONG = [
    "{name}, tu itna bakwaas hai ki tujhe log free ka example samajh ke dikhate hain.",
    "{name}, sun le — aise bolta raha to sab tere upar hasenge, respect khatam."
]

def choose_human_roast(display_name: str, level: str = "soft", matched_word: str = None) -> str:
    name = display_name or "bhai"
    if level == "extra":
        tmpl = random.choice(HUMAN_EXTRA_STRONG)
    elif level == "strong":
        tmpl = random.choice(HUMAN_STRONG_ROASTS)
    else:
        tmpl = random.choice(HUMAN_SOFT_ROASTS)
    if matched_word:
        m = matched_word.strip()
        if len(m) > 2:
            masked = m[0] + ("*" * (len(m)-2)) + m[-1]
        else:
            masked = m[0] + "*"
        return f"{tmpl.format(name=name)} (log bol rahe: '{masked}')"
    return tmpl.format(name=name)

# -------------------- SEND LONG MESSAGE (chunked) --------------------
async def send_long_message(channel: discord.abc.Messageable, text: str):
    if not text:
        await channel.send("Papa ji, reply thoda khali sa aa gaya, dobara try karo.")
        return
    max_len = 1900
    for i in range(0, len(text), max_len):
        await channel.send(text[i:i+max_len])
        await asyncio.sleep(0.06)
# -------------------- main.py (PART 6/8) --------------------
# -------------------- LIVE SEARCH HELPERS (SERPAPI / GOOGLE CSE) --------------------
import requests

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
    if SERPAPI_KEY:
        s = perform_search_serpapi(query)
        if s:
            return s
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        s = perform_search_google(query)
        if s:
            return s
    return ""
# -------------------- main.py (PART 7/8) --------------------
# -------------------- HYBRID LLM ASK (Gemini primary, fallback to canned) --------------------
async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = (user.id == OWNER_ID)
    name = "Papa ji" if owner_flag else user.display_name
    lang = "en" if RUNTIME_SETTINGS.get("english_lock", False) else "hi"

    ctx = get_context(user.id)
    short_followups = ["naam","name","bta","bata","kaun","kis","kis ka"]
    if ctx and len(text.split()) <= 5 and any(w in text.lower() for w in short_followups):
        items = ctx.get("items") or []
        if items:
            text = f"{ctx.get('last_query')} — items: {', '.join(items[:6])} — follow-up: {text}"
        else:
            text = f"{ctx.get('last_query')} — follow-up: {text}"

    triggers = ["search","kab","aaj","news","release","lyrics","khabar","price","brand","date","kab aayega"]
    wants_live = any(t in (text or "").lower() for t in triggers)

    search_summary = ""
    if wants_live:
        search_summary = perform_live_search(text)
        if not search_summary:
            await send_long_message(channel, "Papa ji, live-search keys/config missing ya result nahi mila.")
            return

    if model is not None:
        prompt = build_normal_prompt(user.display_name, text, owner_flag, lang)
        if search_summary:
            prompt += f"\nSearch results:\n{search_summary}\n\nUse these to answer accurately.\n"
        if is_announcement:
            prompt = ("Write a Discord announcement in " + ("Hinglish." if lang=="hi" else "English.") +
                      " Provide bold title line and 3-6 bullets.\n\n") + prompt
        try:
            async with channel.typing():
                resp = model.generate_content(prompt)
                out = getattr(resp, "text", None)
                if not out:
                    out = search_summary or "Papa ji, thoda blank sa aa gaya. Dobara bhejo."
                # save small items to context
                items = []
                for line in out.splitlines():
                    l = line.strip()
                    if l and len(l.split()) <= 6 and len(l) < 120:
                        items.append(l)
                    if len(items) >= 8:
                        break
                if items:
                    set_context(user.id, "general", text, items=items)
                await send_long_message(channel, out)
                return
        except Exception as e:
            await channel.send(f"Gemini error/timeout: {e}. Falling back to simple reply.")

    if search_summary:
        await send_long_message(channel, f"Live search results:\n\n{search_summary}")
        return

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
# -------------------- main.py (PART 8/8) --------------------
# -------------------- ADMIN HELPERS: ensure Muted role + overwrites --------------------
async def ensure_muted_role_and_overwrites(guild: discord.Guild) -> Optional[discord.Role]:
    muted_role = discord.utils.get(guild.roles, name="Muted")
    if not muted_role:
        try:
            muted_role = await guild.create_role(name="Muted", reason="Created by Pappu for muting")
        except Exception as e:
            print("Could not create Muted role:", e)
            return None

    bot_member = guild.me
    if not bot_member:
        return muted_role

    overwrite = discord.PermissionOverwrite()
    overwrite.send_messages = False
    overwrite.add_reactions = False
    overwrite.speak = False
    overwrite.connect = False
    overwrite.send_tts_messages = False
    # apply on text channels
    for ch in guild.text_channels:
        try:
            await ch.set_permissions(muted_role, overwrite=overwrite, reason="Apply mute role restrictions")
        except Exception:
            pass
    return muted_role

# -------------------- OWNER / SECRET ADMIN HANDLER --------------------
async def handle_secret_admin(message: discord.Message, clean_text: str) -> bool:
    if message.author.id != OWNER_ID:
        return False

    global PROFANE_KEYWORDS, ROASTS_STRONG
    text = (clean_text or "").lower().strip()
    guild = message.guild

    # shutdown / restart
    if text in ("pappu shutdown","pappu stop","pappu sleep"):
        await message.channel.send("Theek hai Papa ji, going offline. 👋")
        save_persistent_state()
        try:
            await bot.close()
        except Exception:
            os._exit(0)
        return True

    if text in ("pappu restart","pappu reboot"):
        await message.channel.send("Restarting now, Papa ji... 🔁")
        save_persistent_state()
        try:
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as e:
            await message.channel.send(f"Restart failed: `{e}` — restart from panel.")
        return True

    # owner toggles: owner_dm, english, allow_profanity, auto_retaliate
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

    if text.startswith("pappu english"):
        if "on" in text:
            RUNTIME_SETTINGS["english_lock"] = True
            save_persistent_state()
            await message.channel.send("English-Lock ON.")
        elif "off" in text:
            RUNTIME_SETTINGS["english_lock"] = False
            save_persistent_state()
            await message.channel.send("English-Lock OFF.")
        else:
            await message.channel.send("Use: `pappu english on` / `pappu english off`")
        return True

    if "allow_profanity" in text:
        if "on" in text:
            RUNTIME_SETTINGS["allow_profanity"] = True
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to ON (owner-approved).")
        elif "off" in text:
            RUNTIME_SETTINGS["allow_profanity"] = False
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to OFF.")
        else:
            await message.channel.send("Use: `pappu allow_profanity on` / `pappu allow_profanity off`")
        return True

    if text.startswith("pappu auto_retaliate"):
        if "on" in text:
            RUNTIME_SETTINGS["auto_retaliate"] = True
            save_persistent_state()
            await message.channel.send("Auto-retaliate ON (owner-approved).")
        elif "off" in text:
            RUNTIME_SETTINGS["auto_retaliate"] = False
            save_persistent_state()
            await message.channel.send("Auto-retaliate OFF.")
        else:
            await message.channel.send("Use: `pappu auto_retaliate on` / `pappu auto_retaliate off`")
        return True

    if text.startswith("pappu auto_retaliate_cooldown"):
        parts = text.split()
        for p in parts:
            if p.isdigit():
                RUNTIME_SETTINGS["auto_retaliate_cooldown"] = int(p)
                save_persistent_state()
                await message.channel.send(f"Auto-retaliate cooldown set to {p} seconds.")
                return True
        await message.channel.send("Usage: `pappu auto_retaliate_cooldown 60`")
        return True

    # reload slurs
    if text.startswith("pappu reload_slurs"):
        PROFANE_KEYWORDS = load_slurs()
        await message.channel.send(f"Slurs reloaded — {len(PROFANE_KEYWORDS)} keywords loaded.")
        return True

    # reload strong roasts
    if text.startswith("pappu reload_roasts"):
        ROASTS_STRONG = load_strong_roasts()
        await message.channel.send(f"Strong roast templates reloaded — {len(ROASTS_STRONG)} templates loaded.")
        return True

    # ensure muted role + overwrites
    if text.startswith("pappu ensure_muted"):
        if guild is None:
            await message.channel.send("This command only works in a server.")
            return True
        r = await ensure_muted_role_and_overwrites(guild)
        if r:
            await message.channel.send("Muted role ensured + overwrites applied (where possible).")
        else:
            await message.channel.send("Could not ensure muted role (permissions issue).")
        return True

    # owner manual raw insult (explicit) - owner must type exact text or choose index from ROASTS_STRONG
    # usage examples:
    #  pappu raw_insult @user <text...>
    #  pappu raw_insult @user 0    -> uses ROASTS_STRONG[0]
    if ("raw_insult" in text) or (("gali de" in text) and ("owner" in text or "raw" in text)):
        if not message.mentions:
            await message.channel.send("Kisko raw insult bhejna hai? @mention karo aur phir slur/text likho.")
            return True
        target = message.mentions[0]
        rest = clean_text
        for m in message.mentions:
            rest = rest.replace(m.mention, "")
        rest = rest.strip()
        if rest.isdigit():
            idx = int(rest)
            if 0 <= idx < len(ROASTS_STRONG):
                raw = ROASTS_STRONG[idx]
            else:
                await message.channel.send("Invalid index for strong roasts.")
                return True
        elif rest:
            raw = rest  # owner-provided text
        else:
            await message.channel.send("Provide raw text or index.")
            return True
        try:
            await message.channel.send(raw)
        except Exception as e:
            await message.channel.send(f"Error sending raw insult: `{e}`")
        return True

    # If none matched
    return False

# -------------------- EVENTS + on_message (auto-retaliation + main flow) --------------------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online hai Papa ji!")
    # sync slash commands for this bot (owner only if needed)
    try:
        await tree.sync()
    except Exception:
        pass
    try:
        if RUNTIME_SETTINGS.get("stealth"):
            await bot.change_presence(status=discord.Status.invisible)
        else:
            await bot.change_presence(status=discord.Status.online)
    except Exception:
        pass