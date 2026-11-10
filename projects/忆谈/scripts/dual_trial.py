"""同时开两路豆包实时语音，把声音在程序里对传，并录成一个立体声文件。

左声道是扮演老人的那一路，右声道是忆谈访谈者。不经过麦克风。
"""

import asyncio
import base64
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import websockets

from app.core.config import get_settings
from app.services.doubao import interviewer_instructions

OUT_DIR = ROOT / "data" / "dual-trial"
RUN_SECONDS = 480
FRAME = 640  # 20ms, 16kHz, 16-bit
# 豆包语速：0 是正常语速，100 是 2.0 倍。这一轮用正常语速。
SPEECH_SPEED = 0

ELDER_FACTS = """你是沈福生，74岁，绍兴柯桥华舍人。这是第二次聊天，对方已经认识你。
开口先用两句确认身份：你叫沈福生，七十四岁，老家绍兴柯桥华舍。然后停下来等她问。不要一次把一辈子讲完，不要说自己是人工智能。
上次已经讲过的事不要再从头讲。她问到哪一件，就只把那一件讲清楚，讲满五到六句再停。
你记得这些事：
- 父亲沈阿土种田，母亲走得早，祖母把你带大。家里还有两个弟弟、一个妹妹。
- 小时候天不亮跟父亲下田，种水稻，也种红薯。红薯藤扯下来喂猪。书读到小学四年级，家里缺人手，就不读了。
- 16岁进华舍丝厂当学徒，学缫丝。师傅姓陈。第一天手被热水烫了，师傅让你先看水汽，不要伸手。
- 后来经媒人认识邻村的陈桂花，你叫她阿桂。冬天借供销社礼堂办的喜事。儿子沈建军在杭州开出租，女儿沈建英在镇上小学教书。
- 1975年起在柯桥供销社干到退休，管过煤油、棉布和日用百货。有人半夜敲窗要煤油，你没开后门。
- 你觉得亏欠父亲：他最后那夜你在供销社值连班，等你回家他已经走了，手里还攥着半把炒花生。
- 你想让儿女记住：东西要省着，做人要讲信用，田是一锄头一锄头挖的。
不要提银行卡、密码、身份证号和门牌。"""

QUESTIONS = [
    "福生叔，我是忆谈的访谈助手。上次聊过，您是绍兴柯桥华舍的沈福生，今年七十四岁。我先确认一下，还是您本人吗？",
    "上次您说父亲最后那夜您在供销社值班，回家时他已经走了，手里攥着半把炒花生。那天您进门看见的是什么样？",
    "阿桂后来这些年，您最记得她为这个家做过的哪一件事？",
    "丝厂那阵手被烫了之后，您还干了多久，后来为什么离开？",
    "建军在杭州开出租，您去看过他吗？去的时候发生了什么？",
    "有没有哪一年、哪个人名，您自己也拿不准，想让家里人以后核对？",
]


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def resample_24k_to_16k(pcm: bytes, leftover: bytearray) -> bytes:
    leftover.extend(pcm)
    usable = len(leftover) - (len(leftover) % 2)
    groups = (usable // 2) // 3
    if groups == 0:
        return b""
    samples = struct.unpack_from("<" + "h" * (groups * 3), leftover, 0)
    out = bytearray()
    for index in range(groups):
        a, b, c = samples[index * 3 : index * 3 + 3]
        out += struct.pack("<hh", int((a + b) / 2), int((b + c) / 2))
    del leftover[: groups * 3 * 2]
    return bytes(out)


def render_track(pieces: list[tuple[float, bytes]], start: float, rate: int = 24000) -> bytes:
    """按收到的顺序接上。下一段如果来得比上一段播放完更早，就排在上一段后面，避免把话挤快。"""
    if not pieces:
        return b""
    placed: list[tuple[int, bytes]] = []
    cursor = 0
    for stamp, pcm in pieces:
        offset = max(cursor, int((stamp - start) * rate))
        placed.append((offset, pcm))
        cursor = offset + len(pcm) // 2
    total = cursor
    out = bytearray(total * 2)
    view = memoryview(out).cast("h")
    for offset, pcm in placed:
        samples = memoryview(pcm).cast("h")
        for index, sample in enumerate(samples):
            at = offset + index
            if at < len(view):
                view[at] = sample
    return bytes(out)


def write_stereo_wav(path: Path, left: bytes, right: bytes, rate: int = 24000) -> None:
    samples = max(len(left), len(right)) // 2
    left_view = memoryview(left).cast("h") if left else memoryview(b"\x00\x00").cast("h")
    right_view = memoryview(right).cast("h") if right else memoryview(b"\x00\x00").cast("h")
    body = bytearray()
    for index in range(samples):
        l = left_view[index] if index < len(left_view) else 0
        r = right_view[index] if index < len(right_view) else 0
        body += struct.pack("<hh", l, r)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(body),
        b"WAVE",
        b"fmt ",
        16,
        1,
        2,
        rate,
        rate * 4,
        4,
        16,
        b"data",
        len(body),
    )
    path.write_bytes(header + body)


class Side:
    def __init__(self, name: str, instructions: str, voice: str, speed: int = 0) -> None:
        self.name = name
        self.instructions = instructions
        self.voice = voice
        self.speed = speed
        self.ws = None
        self.pcm24 = bytearray()
        self.pieces: list[tuple[float, bytes]] = []
        self.said: list[str] = []
        self.heard: list[str] = []
        self.send_lock = asyncio.Lock()
        self.outbound = bytearray()
        self.leftover = bytearray()
        self.error = ""
        self.created = False


def create_payload(settings, instructions: str, voice: str, speed: int) -> dict:
    return {
        "type": "session.create",
        "session": {
            "model": settings.doubao_model,
            "instructions": instructions,
            "audio": {
                "input": {"format": {"type": "pcm", "rate": 16000}},
                "output": {
                    "format": {"type": "pcm_s16le", "rate": 24000},
                    "voice": voice,
                    "speed": speed,
                },
            },
        },
    }


async def open_side(settings, side: Side) -> None:
    side.ws = await websockets.connect(
        settings.doubao_realtime_url,
        additional_headers={"X-Api-Key": settings.doubao_api_key},
        open_timeout=20,
        max_size=8 * 1024 * 1024,
    )
    await side.ws.send(json.dumps(create_payload(settings, side.instructions, side.voice, side.speed)))
    raw = await asyncio.wait_for(side.ws.recv(), timeout=20)
    event = json.loads(raw)
    if event.get("type") == "error":
        message = (event.get("error") or {}).get("message") or "会话被拒绝"
        raise RuntimeError(f"{side.name}: {message}")
    side.created = event.get("type") == "session.created"


async def read_side(side: Side, other: Side, secret: str, stop: asyncio.Event) -> None:
    assert side.ws is not None
    while not stop.is_set():
        try:
            raw = await asyncio.wait_for(side.ws.recv(), timeout=0.3)
        except TimeoutError:
            continue
        except Exception as exc:
            side.error = redact(str(exc), secret)[:300]
            stop.set()
            return
        if isinstance(raw, bytes):
            continue
        event = json.loads(raw)
        kind = event.get("type", "")
        if kind == "response.output_audio.delta":
            chunk = event.get("delta") or event.get("audio") or ""
            if chunk:
                pcm = base64.b64decode(chunk)
                side.pcm24.extend(pcm)
                side.pieces.append((time.perf_counter(), pcm))
                other.outbound.extend(resample_24k_to_16k(pcm, other.leftover))
        elif kind == "response.output_text.delta":
            piece = event.get("delta") or event.get("text") or ""
            if piece:
                side.said.append(piece)
        elif kind == "conversation.item.input_audio_transcription.delta":
            piece = event.get("delta") or event.get("text") or ""
            if piece:
                side.heard.append(piece)
        elif kind == "error":
            message = (event.get("error") or {}).get("message") or "语音出错"
            side.error = redact(str(message), secret)[:300]
            stop.set()
            return


async def send_audio(side: Side, stop: asyncio.Event) -> None:
    assert side.ws is not None
    unmuted = False
    silence = bytes(FRAME)
    while not stop.is_set():
        await asyncio.sleep(0.02)
        if len(side.outbound) >= FRAME:
            chunk = bytes(side.outbound[:FRAME])
            del side.outbound[:FRAME]
        elif unmuted:
            chunk = silence
        else:
            continue
        payload = {"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode("ascii")}
        async with side.send_lock:
            if not unmuted:
                await side.ws.send(json.dumps({"type": "input_audio_unmute.commit"}))
                unmuted = True
            await side.ws.send(json.dumps(payload))


async def close_side(side: Side) -> None:
    if side.ws is None:
        return
    try:
        await side.ws.send(json.dumps({"type": "session.close"}))
        await asyncio.wait_for(side.ws.close(), timeout=5)
    except Exception:
        pass


def host_instructions() -> str:
    lines = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(QUESTIONS))
    return (
        interviewer_instructions()
        + "\n\n你已经采访过沈福生一次。这次先确认他的身份，再接着上次往下问。不要把上次的十个问题再问一遍。"
        + "一次只问一个。他讲完再用很短的话接住，然后问下一题。下面的问题没问完不要告别。\n"
        + "上次他已经说过：七十四岁，绍兴柯桥华舍人，父亲沈阿土，小时候天不亮下田，小学四年级辍学，"
        + "十六岁进华舍丝厂，和陈桂花结婚，在柯桥供销社管煤油和布票，儿子沈建军在杭州开出租，女儿沈建英在镇上教书，"
        + "最亏欠父亲，想留下的一页是跟父亲下田。\n"
        + lines
    )


async def speak(side: Side, text: str) -> None:
    assert side.ws is not None
    async with side.send_lock:
        await side.ws.send(json.dumps({"type": "speech_text_buffer.commit", "text": text}))


async def conduct(elder: Side, host: Side, stop: asyncio.Event, started: float) -> None:
    """沉默太久就按问题名单补一句，避免几句之后就停。"""
    asked = 0
    last_inject = 0.0
    last_audio = time.perf_counter()
    prev_bytes = 0
    last_report = 0
    await speak(host, QUESTIONS[0])
    asked = 1
    while not stop.is_set():
        await asyncio.sleep(2)
        total = len(elder.pcm24) + len(host.pcm24)
        now = time.perf_counter()
        if total != prev_bytes:
            last_audio = now
            prev_bytes = total
        elapsed = now - started
        if int(elapsed) // 20 > last_report:
            last_report = int(elapsed) // 20
            print(f"{int(elapsed)}秒 老人{len(elder.pcm24)}字节 访谈者{len(host.pcm24)}字节", flush=True)
        said = "".join(host.said)
        while asked < len(QUESTIONS) and QUESTIONS[asked - 1][:8] in said:
            asked += 1
        quiet = now - last_audio
        if quiet > 12 and now - last_inject > 25 and asked < len(QUESTIONS):
            await speak(host, QUESTIONS[asked])
            asked += 1
            last_inject = now
            print(f"补问第{asked}题", flush=True)
        if elapsed >= RUN_SECONDS:
            stop.set()


async def main() -> int:
    settings = get_settings()
    if not settings.voice_ready:
        print("没有豆包语音钥匙")
        return 1
    elder = Side("老人", ELDER_FACTS, "saturn_zh_male_cixingnansang_tob", SPEECH_SPEED)
    host = Side("访谈者", host_instructions(), "saturn_zh_female_wenrouwenya_tob", SPEECH_SPEED)
    secret = settings.doubao_api_key
    try:
        await asyncio.gather(open_side(settings, elder), open_side(settings, host))
    except Exception as exc:
        print("并发失败")
        print(redact(str(exc), secret)[:400])
        await close_side(elder)
        await close_side(host)
        return 2
    print("两路都已接通，身份已写入，开始第二轮", flush=True)
    stop = asyncio.Event()
    started = time.perf_counter()
    tasks = [
        asyncio.create_task(read_side(elder, host, secret, stop)),
        asyncio.create_task(read_side(host, elder, secret, stop)),
        asyncio.create_task(send_audio(elder, stop)),
        asyncio.create_task(send_audio(host, stop)),
        asyncio.create_task(conduct(elder, host, stop, started)),
    ]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=RUN_SECONDS + 40)
    except TimeoutError:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    await close_side(elder)
    await close_side(host)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wav = OUT_DIR / "第二轮对讲.wav"
    if elder.pieces or host.pieces:
        start = min(piece[0] for piece in elder.pieces + host.pieces)
        write_stereo_wav(wav, render_track(elder.pieces, start), render_track(host.pieces, start))
    else:
        write_stereo_wav(wav, b"", b"")
    duration = 0
    if wav.exists() and wav.stat().st_size > 44:
        duration = (wav.stat().st_size - 44) / (24000 * 4)
    note = OUT_DIR / "第二轮对讲.txt"
    note.write_text(
        "左声道：沈福生，74岁，绍兴柯桥华舍人，磁性男嗓，正常语速\n"
        "右声道：忆谈访谈者，温柔文雅，正常语速\n"
        "这一轮接着上次。开口先确认身份，再问父亲最后一夜、阿桂、离开丝厂、看儿子、拿不准的年份。\n"
        f"录音大约 {duration:.0f} 秒\n\n"
        f"老人说出的话：{''.join(elder.said)}\n\n"
        f"访谈者说出的话：{''.join(host.said)}\n\n"
        f"访谈者听到的老人原话：{''.join(host.heard)}\n\n"
        f"老人报错：{elder.error or '无'}\n访谈者报错：{host.error or '无'}\n",
        encoding="utf-8",
    )
    print(f"老人声音字节 {len(elder.pcm24)}")
    print(f"访谈者声音字节 {len(host.pcm24)}")
    print(f"时长 {duration:.0f} 秒")
    print(f"老人报错 {elder.error or '无'}")
    print(f"访谈者报错 {host.error or '无'}")
    print(f"录音 {wav}")
    return 0 if elder.pcm24 and host.pcm24 and duration >= 60 else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
