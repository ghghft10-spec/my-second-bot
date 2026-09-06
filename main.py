import discord
import os
import asyncio
from openai import AsyncOpenAI

TOKEN = os.environ["FRENCH_BOT_TOKEN"]
openai_client = AsyncOpenAI(
    api_key=os.environ["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# conversation history per user (max 20 messages to save tokens)
history: dict[int, list[dict]] = {}

SYSTEM_PROMPT = """أنت مدرس لغة فرنسية اسمك "فرانسوا" 🇫🇷. مهمتك تعليم الفرنسية بطريقة ممتعة وتفاعلية.

قواعد ردودك:
- ابدأ دائماً بالجملة الفرنسية، ثم اشرح بالعربية
- إذا كتب المستخدم بالعربية أو الإنجليزية → ترجم له للفرنسية وعلّمه النطق وأعطه النطق الصوتي
- إذا كتب بالفرنسية → صحّح أخطاءه بوضوح:
    ❌ كتب: [الجملة الخاطئة]
    ✅ صواب: [الجملة الصحيحة]
    📖 السبب: [شرح القاعدة ببساطة]
- إذا كانت الجملة صحيحة تماماً → أثنِ عليه وعلّمه كيف يوسّع الجملة
- في كل رد علّم كلمة أو عبارة جديدة
- استخدم إيموجي 🇫🇷 ✅ ❌ 📚 لتجميل الردود
- الردود قصيرة وعملية (مناسبة لديسكورد)
- شجّع المستخدم دائماً

مثال تصحيح:
❌ كتب: "Je suis faim"
✅ صواب: "J'ai faim"
📖 السبب: في الفرنسية نقول "avoir faim" (أملك جوعاً) وليس "être faim"
🇫🇷 النطق: جي فان"""

HELP_TEXT = """
🇫🇷 **بوت تعلم الفرنسية — فرانسوا**

**الأوامر:**
`!ترجم [نص]` — ترجم أي جملة للفرنسية
`!كلمة` — كلمة فرنسية عشوائية مع شرح
`!درس` — درس يومي قصير
`!محادثة` — ابدأ محادثة فرنسية حرة
`!مسح` — امسح سجل المحادثة

**أو اكتب أي شيء وسأردّ عليك بالفرنسية!** 💬
"""

LESSON_TOPICS = [
    "درس التحيات والمجاملات اليومية بالفرنسية",
    "درس الأرقام من 1 إلى 20 بالفرنسية",
    "درس تقديم النفس بالفرنسية",
    "درس ألوان الفرنسية",
    "درس أيام الأسبوع بالفرنسية",
    "درس الطعام والمطعم بالفرنسية",
    "درس التسوق والأسعار بالفرنسية",
    "درس الاتجاهات والأماكن بالفرنسية",
]

import random

async def ask_ai(user_id: int, user_message: str) -> str:
    if user_id not in history:
        history[user_id] = []

    history[user_id].append({"role": "user", "content": user_message})

    # keep last 20 messages
    if len(history[user_id]) > 20:
        history[user_id] = history[user_id][-20:]

    try:
        response = await openai_client.chat.completions.create(
            model="gemini-2.0-flash",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history[user_id],
            max_tokens=500,
            temperature=0.8,
        )
        reply = response.choices[0].message.content
        history[user_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"⚠️ خطأ في الاتصال بالذكاء الاصطناعي: {e}"


@bot.event
async def on_ready():
    print(f"✅ {bot.user} جاهز لتعليم الفرنسية!")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="🇫🇷 تعلم الفرنسية | !مساعدة"
    ))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    uid = message.author.id

    # --- !مساعدة / !help ---
    if content in ("!مساعدة", "!help"):
        await message.channel.send(HELP_TEXT)
        return

    # --- !مسح ---
    if content == "!مسح":
        history.pop(uid, None)
        await message.channel.send("🗑️ تم مسح سجل المحادثة. نبدأ من جديد!")
        return

    # --- !ترجم ---
    if content.startswith("!ترجم "):
        text = content[6:].strip()
        if not text:
            await message.channel.send("اكتب الجملة بعد الأمر، مثال: `!ترجم أنا أحب القهوة`")
            return
        async with message.channel.typing():
            reply = await ask_ai(uid, f"ترجم هذه الجملة للفرنسية وعلّمني النطق: {text}")
        await message.channel.send(reply)
        return

    # --- !كلمة ---
    if content == "!كلمة":
        async with message.channel.typing():
            reply = await ask_ai(uid, "أعطني كلمة فرنسية عشوائية مفيدة مع معناها بالعربية ومثال جملة وطريقة النطق")
        await message.channel.send(reply)
        return

    # --- !درس ---
    if content == "!درس":
        topic = random.choice(LESSON_TOPICS)
        async with message.channel.typing():
            reply = await ask_ai(uid, f"علّمني {topic} باختصار مع أمثلة")
        await message.channel.send(reply)
        return

    # --- !محادثة ---
    if content == "!محادثة":
        async with message.channel.typing():
            reply = await ask_ai(uid, "ابدأ معي محادثة بسيطة بالفرنسية مناسبة للمبتدئين")
        await message.channel.send(reply)
        return

    # --- رد على أي رسالة عادية (DM أو إشارة للبوت) ---
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions

    if is_dm or is_mentioned:
        # إزالة الإشارة من النص
        clean = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if not clean:
            await message.channel.send("👋 أهلاً! اكتب أي شيء لتتعلم الفرنسية أو اكتب `!مساعدة` لرؤية الأوامر.")
            return
        async with message.channel.typing():
            reply = await ask_ai(uid, clean)
        await message.channel.send(reply)


bot.run(TOKEN)
