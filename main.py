# ---------- PART 1: IMPORTS + CONFIG + PERSISTENCE ----------
from dotenv import load_dotenv
load_dotenv()

import os
import re
import sys
import json
import random
import time
import requests
import html
import asyncio
import discord
from discord.ext import commands
import google.generativeai as genai

# ------------- CONFIG (ENV VARS) -------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Feature toggles (env defaults)
ALLOW_INSULTS = os.getenv("ALLOW_INSULTS", "0") == "1"
RETALIATE = os.getenv("RETALIATE", "0") == "1"
ALLOW_PROFANITY = os.getenv("ALLOW_PROFANITY", "0") == "1"

# Extra toggle: roast anyone using insult (owner beware)
RETALIATE_ALL = os.getenv("RETALIATE_ALL", "0") == "1"

# Live search provider
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "").lower()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

# In-memory conversation memory
CONTEXT_MEMORY = {}
MEMORY_TTL = 60 * 60 * 6  # 6 hours

# ---------- Assistant persistence & secret controls ----------
SETTINGS_FILE = "pappu_settings.json"
MEMORY_FILE = "pappu_memory.json"

RUNTIME_SETTINGS = {
    "owner_dm_only": False,
    "stealth": False,
    "mode": "funny",  # default mode
    "allow_profanity": ALLOW_PROFANITY,
    "english_lock": False  # if True => bot ALWAYS replies in English
}

def load_persistent_state():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                RUNTIME_SETTINGS.update(data.get("settings", {}))
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                mem = json.load(f)
                for k, v in mem.items():
                    try:
                        CONTEXT_MEMORY[int(k)] = v
                    except Exception:
                        CONTEXT_MEMORY[k] = v
    except Exception as e:
        print("⚠️ Error loading persistent state:", e)

def save_persistent_state():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"settings": RUNTIME_SETTINGS}, f, ensure_ascii=False, indent=2)
        mem_to_save = {str(k): v for k, v in CONTEXT_MEMORY.items()}
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(mem_to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ Error saving persistent state:", e)

def apply_mode(mode: str):
    mode = mode.lower()
    accepted = ["funny","angry","serious",
                "flirty","sarcastic","mafia","bhaukaal","kid","toxic","coder","bhai-ji","dark"]
    if mode == "mafia":
        mode = "bhaukaal"
    if mode == "normal":
        mode = "funny"
    if mode not in accepted:
        return False
    RUNTIME_SETTINGS["mode"] = mode
    if mode in ("angry","toxic"):
        RUNTIME_SETTINGS["owner_dm_only"] = False
    return True

# load saved settings/memory
load_persistent_state()
# ---------- PART 2: GEMINI CONFIG + DISCORD BOT INIT + PERSONALITY ----------
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

PERSONALITY = """
You are Pappu Programmer, a smart, funny Discord bot.

Language & Style:
- Reply in Hinglish (Hindi + English mix) by default.
- Tone: friendly, witty. OWNER-only modes may allow stronger profanity.
- Avoid hateful slurs targeting protected groups.
- If the user is the owner, call them "Papa ji".
- Default: short / medium replies (2–4 lines). Use longer only on explicit requests.

Knowledge:
- Explain general topics. Live web search only if SEARCH_PROVIDER configured.
- Always answer only the latest message; prefer short direct replies.
"""
# ---------- PART 3: ROASTS, PROFANITY MARKERS, LANGUAGE HELPER ----------
SAFE_ROAST_POOL = [
    "Arre {name}, thoda soft reh — tera logic abhi beta mode me hai. 😏",
    "{name}, tera swag strong hai par andar se 404 common sense mil raha hai. 😂",
    "Bhai {name}, pehle unit tests pass kar, phir hero ban. 😅",
    "{name}, chup reh ke bhi banda classy lag sakta hai — try kar."
]

PROFANE_ROAST_POOL = [
    "{name}, asli baat: tera dimag chain se so nahi paata; waha logic nahi milta. 😆",
    "{name}, tu itna bakwaas kar raha hai ki mera buffer overflow ho raha hai. Chill!",
    "{name}, thoda chup kar. Teri comedy paid subscription wali ho gayi hai — mujhe block karne ka man kar raha."
]

PROFANITY_MARKERS = [
    "chutiya","ch*tiya","gandu","g**du","saala","saale","bsdk","b sdk","mc","m*c",
    "madarchod","m*darchod","bhosdike","bhosdi","tatti","harami","b*stard",
    "idiot","stupid","dumb","loser"
]

def is_english(text: str) -> bool:
    """
    Much stricter detection so bot doesn't get stuck in English mode.
    Only returns True if user CLEARLY writes in English sentences.
    """
    if not text:
        return False

    text = text.strip()

    # If contains Devanagari (Hindi) characters → definitely NOT English
    if re.search(r"[\u0900-\u097F]", text):
        return False

    # find alphabetic tokens
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return False

    # require at least two reasonably long English words (to avoid small mixed messages)
    long_words = [w for w in words if len(w) >= 4]
    if len(long_words) < 2:
        return False

    # ascii ratio heuristic over tokens vs total tokens
    ascii_ratio = len(words) / max(len(text.split()), 1)

    return ascii_ratio >= 0.70

def clean_now():
    return int(time.time())

def prune_memory():
    now = clean_now()
    to_delete = []
    for uid, info in list(CONTEXT_MEMORY.items()):
        if now - info.get("ts", 0) > MEMORY_TTL:
            to_delete.append(uid)
    for uid in to_delete:
        del CONTEXT_MEMORY[uid]

def set_context(user_id: int, subject: str, query: str, items: list | None = None):
    entry = {"last_subject": subject, "last_query": query, "ts": clean_now()}
    if items:
        entry["items"] = items
    CONTEXT_MEMORY[user_id] = entry

def get_context(user_id: int):
    prune_memory()
    return CONTEXT_MEMORY.get(user_id)

def is_owner(user: discord.abc.User):
    return user.id == OWNER_ID

def is_detailed_question(text: str) -> bool:
    t = text.lower()
    keywords = ["detail","details","samjha","samjhao","explain","theory","history","kaise","kya hota","physics","science"]
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
    if profane and (ALLOW_PROFANITY or RUNTIME_SETTINGS.get("allow_profanity", False)):
        return random.choice(PROFANE_ROAST_POOL).format(name=target_name)
    return random.choice(SAFE_ROAST_POOL).format(name=target_name)
# ---------- PART 4: LIVE SEARCH HELPERS + PROMPT BUILDERS ----------
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

def build_normal_prompt(user_name: str, user_text: str, owner_flag: bool, search_summary: str = "") -> str:
    title_name = "Papa ji" if owner_flag else user_name
    detailed = is_detailed_question(user_text)
    length_line = "Answer thoda detailed de sakte ho (max ~10–12 lines)." if detailed else "Answer chhota / medium rakho (max 2–4 lines, seedha point pe)."

    insults_note = "Allowed to use light roasts." if ALLOW_INSULTS else "Do NOT use abusive slurs. Keep witty but safe."
    profanity_note = "Strong profanity allowed (owner-controlled)." if (ALLOW_PROFANITY or RUNTIME_SETTINGS.get("allow_profanity", False)) else "Strong profanity disabled."

    mode = RUNTIME_SETTINGS.get("mode", "funny")
    tone_map = {
        "funny": "masti + light roast, friendly",
        "angry": "thoda aggressive, short, savage",
        "serious": "calm, formal, informative",
        "flirty": "playful, light flirting (no sexual content)",
        "sarcastic": "sarcastic, witty",
        "bhaukaal": "mafia-style, confident, short",
        "kid": "simple, kid-friendly, no profanity",
        "toxic": "very savage (OWNER-ONLY RECOMMENDED)",
        "coder": "technical, precise, code-friendly",
        "bhai-ji": "respectful, elder-bhai tone",
        "dark": "mysterious, philosophical"
    }
    tone_instruction = tone_map.get(mode, "masti + light roast")

    # language hint: respect english_lock if set, otherwise auto-detect
    if RUNTIME_SETTINGS.get("english_lock"):
        lang_hint = "Reply ONLY in English."
    else:
        lang_hint = "Reply in English." if is_english(user_text) else "Reply in Hinglish (Hindi+English)."

    prompt = f"""{PERSONALITY}
Mode: {mode} — Tone: {tone_instruction}
{insults_note} {profanity_note}
{lang_hint}

User ({title_name}) ne pucha:
\"\"\"{user_text}\"\"\"

{length_line}
"""
    if search_summary:
        prompt += f"\nHere are live search results to help answer:\n{search_summary}\n\nUse them to form a concise reply.\n"

    prompt += "\nAb Pappu Programmer ka reply (use the tone & language above):\n"
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
- Overall length under 1800 characters.

Return ONLY the announcement text that can be directly pasted into Discord.
"""
# ---------- PART 5: EXTRACT HELPERS + LYRICS DETECTION ----------
def extract_subject_from_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    if "daru" in t or "alcohol" in t or "drink" in t:
        return "daru"
    if "phone" in t or "mobile" in t:
        return "phone"
    if "laptop" in t:
        return "laptop"
    if "movie" in t or "series" in t or "film" in t:
        return "movie"
    return ""

def extract_items_from_text(text: str) -> list:
    if not text:
        return []

    items = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("•") or s.startswith("-") or re.match(r"^\d+\.", s):
            s = re.sub(r"^[•\-\d\.\s]+", "", s).strip()
            s = s.split("—")[0].strip()
            if s:
                items.append(s)

    if not items:
        for line in text.splitlines():
            if "," in line and len(line) < 200:
                parts = [p.strip() for p in line.split(",") if p.strip()]
                for p in parts:
                    if 1 <= len(p.split()) <= 6:
                        items.append(p)

    cleaned = []
    for it in items:
        it = re.sub(r"[\"'`]+", "", it).strip()
        if it and it.lower() not in [c.lower() for c in cleaned]:
            cleaned.append(it)
        if len(cleaned) >= 10:
            break

    return cleaned

def is_lyrics_request(text: str) -> tuple:
    t = text.lower()
    if "lyrics" in t or "lyrics of" in t or "gaane ke" in t or "lyrics for" in t:
        m = re.search(r"lyrics (?:of|for)\s+['\"]?([^'\"]{2,200})", t)
        if m:
            return True, m.group(1).strip()
        parts = t.split()
        if "lyrics" in parts:
            idx = parts.index("lyrics")
            guess = " ".join(parts[idx+1:idx+6]).strip()
            if guess:
                return True, guess
    return False, ""
# ---------- PART 6: MAIN ask_pappu HANDLER (with LYRICS & ITEMS) ----------
async def ask_pappu(user: discord.abc.User, text: str, is_announcement: bool, channel: discord.abc.Messageable):
    owner_flag = is_owner(user)

    # owner_dm_only: if on, ignore non-owner in guilds
    if RUNTIME_SETTINGS.get("owner_dm_only") and not is_owner(user):
        try:
            if isinstance(channel, discord.TextChannel):
                await channel.send("Papa ji, maintenance mode chalu hai — abhi sirf owner se reply karta hoon.")
            return
        except Exception:
            return

    # short follow-ups
    short_followups = ["naam","name","bta naam","bata naam","bol naam","uska naam","isko naam","inme se","inme se kaun"]
    is_short = len(text.split()) <= 5
    ctx = get_context(user.id)
    if is_short and ctx and any(w in text.lower() for w in short_followups):
        items = ctx.get("items")
        if items:
            items_str = ", ".join(items[:8])
            text = f"{ctx.get('last_query')} — items: {items_str} — follow-up: {text}"
        else:
            text = f"{ctx.get('last_query')} — user follow-up: {text}"

    # ===== LYRICS special-case handler (safe) =====
    is_lyr, lyric_q = is_lyrics_request(text)
    if is_lyr:
        query = f"{lyric_q} lyrics"
        search = perform_live_search(query)
        first_line = ""
        for line in (search or "").splitlines():
            if line.strip():
                first_line = line.strip()
                break
        safe_excerpt = ""
        if first_line:
            if "—" in first_line:
                safe_excerpt = first_line.split("—",1)[1].strip()
            else:
                safe_excerpt = first_line
        if len(safe_excerpt) > 90:
            safe_excerpt = safe_excerpt[:87].rsplit(" ",1)[0] + "..."
        reply = f"Papa ji — lyrics ka short snippet (copyright rules ke wajah se full lyrics nahi de sakta):\n\"{safe_excerpt}\"\n\nFull lyrics ke liye search results:\n{search}"
        await send_long_message(channel, reply)
        set_context(user.id, "lyrics", text, items=[lyric_q])
        return

    # live info?
    live_triggers = ["aaj","kab","news","release","date","search","khabar","announce","kab aayega","kab aa rahi"]
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
                    items = extract_items_from_text(out)
                    if subj:
                        set_context(user.id, subj, text, items=items if items else None)
                    await send_long_message(channel, out)
                    return
            except Exception as e:
                await channel.send(f"Search+Model error: {e}")
                return
        else:
            await send_long_message(channel, f"Live search results:\n{search_summary}")
            return

    # non-live path
    if is_announcement:
        prompt = build_announcement_prompt(user.display_name, text, owner_flag)
    else:
        prompt = build_normal_prompt(user.display_name, text, owner_flag)

    subj = extract_subject_from_text(text)
    if subj:
        set_context(user.id, subj, text)

    if model is None:
        if "daru" in text.lower():
            set_context(user.id, "daru", text, items=["Old Monk","McDowell's No.1","Magic Moments"])
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
            subj = extract_subject_from_text(text)
            items = extract_items_from_text(reply)
            if subj:
                set_context(user.id, subj, text, items=items if items else None)
            await send_long_message(channel, reply)
    except Exception as e:
        await channel.send(f"Kuch error aa gaya Papa ji: `{e}`")
# ---------- PART 7: SECRET ADMIN + OWNER NL ADMIN ----------
async def handle_secret_admin(message: discord.Message, clean_text: str) -> bool:
    if not is_owner(message.author):
        return False
# compatibility alias so old callsites (handle_owner_nl_admin) keep working
handle_owner_nl_admin = handle_secret_admin
    text = clean_text.lower().strip()

    if text in ("pappu shutdown","pappu stop","pappu sleep"):
        await message.channel.send("Theek hai Papa ji, going offline. 👋")
        save_persistent_state()
        await bot.close()
        return True

    if text in ("pappu restart","pappu reboot"):
        await message.channel.send("Restarting now, Papa ji... 🔁")
        save_persistent_state()
        try:
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as e:
            await message.channel.send(f"Restart failed: `{e}` — please restart from host panel.")
        return True

    if text.startswith("pappu owner_dm"):
        if "on" in text:
            RUNTIME_SETTINGS["owner_dm_only"] = True
            save_persistent_state()
            await message.channel.send("Owner DM only mode ON. Sirf Papa ji ke DMs reply karunga.")
        elif "off" in text:
            RUNTIME_SETTINGS["owner_dm_only"] = False
            save_persistent_state()
            await message.channel.send("Owner DM only mode OFF. Normal mode.")
        else:
            await message.channel.send("Use: `pappu owner_dm on` or `pappu owner_dm off`")
        return True

    if text.startswith("pappu stealth"):
        if "on" in text:
            RUNTIME_SETTINGS["stealth"] = True
            save_persistent_state()
            await message.channel.send("Stealth ON. Trying to hide status (best effort).")
            try:
                await bot.change_presence(status=discord.Status.invisible)
            except Exception:
                pass
        elif "off" in text:
            RUNTIME_SETTINGS["stealth"] = False
            save_persistent_state()
            await message.channel.send("Stealth OFF. Back to normal status.")
            try:
                await bot.change_presence(status=discord.Status.online)
            except Exception:
                pass
        else:
            await message.channel.send("Use: `pappu stealth on` or `pappu stealth off`")
        return True

    if text.startswith("pappu mode"):
        parts = text.split()
        if len(parts) >= 3 and apply_mode(parts[2]):
            save_persistent_state()
            await message.channel.send(f"Mode set to `{parts[2]}`. Applied.")
        else:
            await message.channel.send("Usage: `pappu mode funny|angry|serious|flirty|sarcastic|bhaukaal|kid|toxic|coder|bhai-ji|dark`")
        return True

    # English lock toggle (owner-only)
    if text.startswith("pappu english"):
        if "on" in text:
            RUNTIME_SETTINGS["english_lock"] = True
            save_persistent_state()
            await message.channel.send("English-Lock ON. Ab Pappu sirf English me reply karega. 🇬🇧")
        elif "off" in text:
            RUNTIME_SETTINGS["english_lock"] = False
            save_persistent_state()
            await message.channel.send("English-Lock OFF. Ab Pappu auto language detect karega. 🔄")
        else:
            await message.channel.send("Use: `pappu english on` / `pappu english off`")
        return True

    # ---------- OWNER NL ADMIN ----------
    text = clean_text.lower()
    guild = message.guild
    if guild is None:
        return False  # DM admin commands not supported here

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

    # owner requested insult
    if is_owner(message.author) and ("gali de" in text or "insult" in text or "gali bhej" in text):
        if not target_member:
            await message.channel.send("Kisko insult bhejna hai Papa ji? @mention karke bolo.")
            return True
        profane = RUNTIME_SETTINGS.get("allow_profanity", ALLOW_PROFANITY)
        roast = choose_roast(target_member.display_name, profane=profane)
        await message.channel.send(roast)
        return True

    # toggle profanity persisted
    if is_owner(message.author) and ("allow_profanity on" in text or "allow_profanity off" in text or "allow_profanity" in text):
        if "on" in text:
            ALLOW_PROFANITY = True
            RUNTIME_SETTINGS["allow_profanity"] = True
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to ON for this session. (Persisted.)")
        elif "off" in text:
            ALLOW_PROFANITY = False
            RUNTIME_SETTINGS["allow_profanity"] = False
            save_persistent_state()
            await message.channel.send("ALLOW_PROFANITY set to OFF for this session.")
        else:
            await message.channel.send("Use: 'pappu allow_profanity on' or 'pappu allow_profanity off'")
        return True

    return False
# ---------- PART 8: EVENTS + RUN ----------
async def periodic_autosave(interval_seconds: int = 300):
    while True:
        await asyncio.sleep(interval_seconds)
        save_persistent_state()

@bot.event
async def on_ready():
    print(f"✅ {bot.user} online hai Papa ji!")
    try:
        bot.loop.create_task(periodic_autosave(300))
    except Exception:
        pass
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

    content_lower = message.content.lower()
    invoked = bot.user.mentioned_in(message) or "pappu" in content_lower

    # STRONGER RETALIATE
    if RETALIATE:
        insult_here = contains_insult(message.content)
        bot_mentioned = bot.user.mentioned_in(message) or ("pappu" in content_lower) or ("pappu?" in content_lower)
        if insult_here and not is_owner(message.author) and bot_mentioned:
            profane = RUNTIME_SETTINGS.get("allow_profanity", ALLOW_PROFANITY)
            await message.channel.send(choose_roast(message.author.display_name, profane=profane))
            return

    # RETALIATE_ALL optional
    if RETALIATE_ALL and not is_owner(message.author) and contains_insult(message.content):
        profane = RUNTIME_SETTINGS.get("allow_profanity", ALLOW_PROFANITY)
        await message.channel.send(choose_roast(message.author.display_name, profane=profane))
        return

    if invoked:
        clean_text = (
            message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        )

        # Try secret admin commands (owner-only) first
        handled_secret = await handle_secret_admin(message, clean_text)
        if handled_secret:
            await bot.process_commands(message)
            return

        # OWNER NL admin
        if is_owner(message.author):
            handled = await handle_owner_nl_admin(message, clean_text)
            if handled:
                await bot.process_commands(message)
                return

        if not clean_text or clean_text.lower() in ["pappu","pappu?","pappu!","pappu bot"]:
            name = "Papa ji" if is_owner(message.author) else message.author.name
            await message.channel.send(f"Haan {name}, bol kya scene hai? 😎")
        else:
            await ask_pappu(message.author, clean_text, False, message.channel)

    await bot.process_commands(message)

# Simple commands
@bot.command(name="hello")
async def hello_cmd(ctx):
    name = "Papa ji" if is_owner(ctx.author) else ctx.author.name
    await ctx.send(f"Namaste {name}! 🙏 Main Pappu Programmer hu. Kuch chai pe baat karte?")

@bot.command(name="ask")
async def ask_cmd(ctx, *, question: str):
    await ask_pappu(ctx.author, question, False, ctx.channel)

if __name__ == "__main__":
    if "allow_profanity" not in RUNTIME_SETTINGS:
        RUNTIME_SETTINGS["allow_profanity"] = ALLOW_PROFANITY

    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing!")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        finally:
            save_persistent_state()