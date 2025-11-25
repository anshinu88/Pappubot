import os
import re
import discord
from discord.ext import commands
import google.generativeai as genai

# ------------- CONFIG (ENV VARS SE) -------------
DISCORD_TOKEN = os.getenv("MTQ0MjU0Nzc2Nzk0NjkwMzU3Mg.GSP1Aa.EGISSuFEOvyk1lBk4VpiW_w2nDfqwaeBqT-11o")
GEMINI_API_KEY = os.getenv("AIzaSyDa-Vke3GnRZtGxKg8UJExhhTBuFqGSQas")
OWNER_ID = int(os.getenv("1133366983870652476", "0"))
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
        length_line = "Answer chhota / medium rak
