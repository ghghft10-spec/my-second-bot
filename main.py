import discord
import os
import asyncio
import base64
import io
import json
import math
import tempfile
import time
import wave
import urllib.error
import urllib.request
import discord.ext.voice_recv as voice_recv
from gtts import gTTS

TOKEN = os.environ["FRENCH_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = "gemini-2.5-flash"

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# conversation history per user (max 20 messages to save tokens)
history: dict[int, list[dict]] = {}
voice_sessions: dict[int, "VoiceSession"] = {}
main_loop: asyncio.AbstractEventLoop | None = None

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
`!صورة` — أرفق صورة وسيترجم النص الموجود فيها
`!دخول` — ادخل القناة الصوتية وابدأ التدريب الصوتي
`!خروج` — غادر القناة الصوتية
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

def _gemini_request(payload: dict) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8"))
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(data.get("error", {}).get("message", "لم يصل رد من Gemini"))
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts).strip()


async def gemini_generate(
    prompt: str,
    *,
    image_bytes: bytes | None = None,
    image_mime: str = "image/jpeg",
    audio_bytes: bytes | None = None,
) -> str:
    parts: list[dict] = [{"text": prompt}]
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": image_mime,
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
        })
    if audio_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "audio/wav",
                "data": base64.b64encode(audio_bytes).decode("ascii"),
            }
        })
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }
    return await asyncio.to_thread(_gemini_request, payload)


async def ask_ai(user_id: int, user_message: str) -> str:
    if user_id not in history:
        history[user_id] = []

    history[user_id].append({"role": "user", "content": user_message})

    # keep last 20 messages
    if len(history[user_id]) > 20:
        history[user_id] = history[user_id][-20:]

    try:
        conversation = "\n".join(
            f"{item['role']}: {item['content']}" for item in history[user_id]
        )
        reply = await gemini_generate(
            f"{SYSTEM_PROMPT}\n\nسجل المحادثة:\n{conversation}\n\n"
            "أجب على آخر رسالة للمستخدم فقط."
        )
        history[user_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"⚠️ خطأ في الاتصال بالذكاء الاصطناعي: {e}"


class FrenchVoiceSink(voice_recv.AudioSink):
    """Collect short turns from one learner and let the monitor detect silence."""

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id
        self.buffer = bytearray()
        self.last_voice_at = 0.0
        self.processing = False

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None or getattr(user, "id", None) != self.user_id or not data.pcm:
            return

        # Discord PCM is signed 16-bit audio. A lightweight level estimate is
        # enough to distinguish speech from silence without extra libraries.
        samples = data.pcm[::4]
        level = sum(abs(int.from_bytes(samples[i:i + 2], "little", signed=True))
                    for i in range(0, len(samples) - 1, 2)) / max(1, len(samples) // 2)
        if level > 350:
            self.buffer.extend(data.pcm)
            self.last_voice_at = time.monotonic()

        # Limit one turn to about eight seconds.
        max_bytes = 48_000 * 2 * 2 * 8
        if len(self.buffer) > max_bytes:
            self.buffer = self.buffer[:max_bytes]
            self.last_voice_at = time.monotonic() - 
        def cleanup(self) -> None:
            self.buffer.clear()

class VoiceSession:
    def __init__(self, voice_client, text_channel, user_id: int):
        self.voice_client = voice_client
        self.text_channel = text_channel
        self.user_id = user_id
        self.sink = FrenchVoiceSink(user_id)
        self.monitor_task: asyncio.Task | None = None


def pcm_to_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(pcm)
    return output.getvalue()


def make_french_audio(text: str) -> str:
    path = tempfile.NamedTemporaryFile(prefix="french-", suffix=".mp3", delete=False).name
    gTTS(text=text[:700], lang="fr", slow=False).save(path)
    return path


async def process_voice_turn(session: VoiceSession, pcm: bytes) -> None:
    session.sink.processing = True
    try:
        wav = pcm_to_wav(pcm)
        transcript = await gemini_generate(
            "حوّل هذا التسجيل الصوتي إلى نص كما قيل بالضبط. "
            "قد يكون المتعلم يتكلم الفرنسية أو العربية. أعد النص فقط بدون شرح.",
            audio_bytes=wav,
        )
        if not transcript:
            return

        reply = await ask_ai(
            session.user_id,
            f"هذه رسالة صوتية من المتعلم: {transcript}\n"
            "صححها وعلّمه بطريقة قصيرة، وابدأ بالفرنسية ثم اشرح بالعربية.",
        )
        await session.text_channel.send(f"🎧 **سمعت:** {transcript}\n\n{reply}")

        voice_line = await gemini_generate(
            f"أنت مدرس فرنسية. بناءً على كلام المتعلم: {transcript}\n"
            f"وبناءً على رد المدرس: {reply}\n"
            "اكتب جملة أو جملتين بالفرنسية فقط لينطقها البوت بصوت واضح. "
            "لا تكتب العربية ولا الرموز ولا عناوين.",
        )
        audio_path = await asyncio.to_thread(make_french_audio, voice_line)
        if session.voice_client.is_connected():
            if session.voice_client.is_playing():
                session.voice_client.stop()

            def cleanup_audio(error):
                try:
                    os.unlink(audio_path)
                except FileNotFoundError:
                    pass
                if error:
                    print(f"⚠️ خطأ تشغيل الصوت: {error}")

            session.voice_client.play(
                discord.FFmpegPCMAudio(audio_path),
                after=cleanup_audio,
            )
    except Exception as error:
        await session.text_channel.send(f"⚠️ تعذر معالجة الصوت: {error}")
    finally:
        session.sink.processing = False


async def monitor_voice_session(guild_id: int, session: VoiceSession) -> None:
    while voice_sessions.get(guild_id) is session:
        await asyncio.sleep(0.4)
        sink = session.sink
        if (
            sink.buffer
            and sink.last_voice_at
            and time.monotonic() - sink.last_voice_at > 1.1
            and not sink.processing
        ):
            pcm = bytes(sink.buffer)
            sink.buffer.clear()
            asyncio.create_task(process_voice_turn(session, pcm))


async def translate_image_attachment(message: discord.Message, user_id: int) -> None:
    image = next(
        (
            attachment for attachment in message.attachments
            if (attachment.content_type or "").startswith("image/")
            or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ),
        None,
    )
    if image is None:
        await message.channel.send(
            "🖼️ أرفق صورة مع الأمر، مثال: `!صورة` ثم أرسل صورة تحتوي على نص."
        )
        return

    async with message.channel.typing():
        image_bytes = await image.read()
        result = await gemini_generate(
            "اقرأ النص الموجود في الصورة. ترجم النص إلى العربية، "
            "ثم اكتب النص الفرنسي الصحيح، واشرح النطق باختصار. "
            "إذا كان النص فرنسياً، صحح أخطاءه أولاً. "
            "إذا لم يوجد نص واضح، أخبرني بذلك.",
            image_bytes=image_bytes,
            image_mime=image.content_type or "image/jpeg",
        )
    await message.channel.send(f"🖼️ **ترجمة الصورة:**\n{result}")


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

    # --- !صورة ---
    if content == "!صورة" or content.startswith("!صورة "):
        await translate_image_attachment(message, uid)
        return

    # --- !دخول ---
    if content == "!دخول":
        if message.guild is None:
            await message.channel.send("استخدم هذا الأمر داخل سيرفر، وليس في رسالة خاصة.")
            return
        if message.author.voice is None or message.author.voice.channel is None:
            await message.channel.send("🎙️ ادخل قناة صوتية أولاً، ثم اكتب `!دخول`.")
            return

        old_session = voice_sessions.get(message.guild.id)
        if old_session:
            await message.channel.send("🎙️ جلسة صوتية موجودة بالفعل في هذا السيرفر.")
            return

        try:
            voice_client = await message.author.voice.channel.connect(
                cls=voice_recv.VoiceRecvClient
            )
            session = VoiceSession(voice_client, message.channel, uid)
            voice_sessions[message.guild.id] = session
            voice_client.listen(session.sink)
            session.monitor_task = asyncio.create_task(
                monitor_voice_session(message.guild.id, session)
            )
            await message.channel.send(
                "🎙️ دخلت القناة! تحدث بالفرنسية أو العربية، وسأصحح كلامك وأجيبك صوتياً. "
                "اكتب `!خروج` للمغادرة."
            )
        except Exception as error:
            await message.channel.send(f"⚠️ تعذر دخول القناة الصوتية: {error}")
        return

    # --- !خروج ---
    if content == "!خروج":
        if message.guild is None or message.guild.id not in voice_sessions:
            await message.channel.send("أنا لست في قناة صوتية حالياً.")
            return
        session = voice_sessions.pop(message.guild.id)
        if session.monitor_task:
            session.monitor_task.cancel()
        try:
            session.voice_client.stop_listening()
            await session.voice_client.disconnect(force=True)
        except Exception:
            pass
        await message.channel.send("👋 خرجت من القناة الصوتية. إلى اللقاء!")
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

