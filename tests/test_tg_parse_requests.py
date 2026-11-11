"""Тесты обработки сообщений Telegram и отправки контента."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

from aiogram.types import MessageEntity
from PIL import Image

from tg_parse_requests import (
    MAX_FILE_SIZE,
    MAX_PHOTO_SIZE,
    _FFMPEG_EXECUTOR,
    _async_convert,
    _convert_video_to_mp4,
    _resize_image,
    _stream_content,
    answer_disabled_content,
    get_success_reply_str,
    get_tags_from_msg,
    get_tags_from_str,
    get_urls_from_entities,
    get_urls_from_msg,
    send_content_from_response,
)
from tests.helpers import FakeMessage, FakeResponse


class TagsTests(unittest.TestCase):
    def test_get_tags_from_str(self):
        self.assertEqual(
            get_tags_from_str("текст #foo_bar #baz"),
            ["foo bar", "baz"],
        )

    def test_get_tags_from_msg_text_and_caption(self):
        self.assertEqual(
            get_tags_from_msg(SimpleNamespace(text="#one", caption=None)),
            ["one"],
        )
        self.assertEqual(
            get_tags_from_msg(SimpleNamespace(text=None, caption="#two")),
            ["two"],
        )
        self.assertEqual(
            get_tags_from_msg(SimpleNamespace(text=None, caption=None)),
            [],
        )


class UrlsTests(unittest.TestCase):
    def test_get_urls_from_entities(self):
        text = "https://example.com/a and https://example.com/b"
        entities = [
            MessageEntity(type="url", offset=0, length=len("https://example.com/a")),
            MessageEntity(
                type="text_link",
                offset=len("https://example.com/a and "),
                length=len("https://example.com/b"),
                url="https://example.com/b",
            ),
        ]
        self.assertEqual(
            get_urls_from_entities(text, entities),
            ["https://example.com/a", "https://example.com/b"],
        )

    def test_get_urls_from_entities_uses_utf16_offsets(self):
        text = "😀 https://example.com"
        entity = MessageEntity(
            type="url",
            offset=3,  # эмодзи занимает две UTF-16 единицы
            length=len("https://example.com"),
        )
        self.assertEqual(
            get_urls_from_entities(text, [entity]),
            ["https://example.com"],
        )

    def test_get_urls_from_entities_deduplicates(self):
        entities = [
            MessageEntity(type="url", offset=0, length=len("example.com")),
            MessageEntity(type="url", offset=0, length=len("example.com")),
        ]
        self.assertEqual(
            get_urls_from_entities("example.com", entities),
            ["https://example.com"],
        )

    def test_get_urls_from_msg_prefers_caption(self):
        msg = SimpleNamespace(
            caption="https://example.com/caption",
            caption_entities=[
                MessageEntity(
                    type="url",
                    offset=0,
                    length=len("https://example.com/caption"),
                )
            ],
            text="https://example.com/text",
            entities=[
                MessageEntity(
                    type="url",
                    offset=0,
                    length=len("https://example.com/text"),
                )
            ],
        )
        self.assertEqual(
            get_urls_from_msg(msg),
            ["https://example.com/caption"],
        )


class ContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_disabled_content(self):
        msg = FakeMessage(message_id=42)
        await answer_disabled_content(msg, "VideoNote")
        self.assertEqual(len(msg.answers), 1)
        text, kwargs = msg.answers[0]
        self.assertIn("video_note", text)
        self.assertIn("Отключено", text)
        self.assertEqual(kwargs["reply_to_message_id"], 42)
        self.assertEqual(kwargs["parse_mode"], "HTML")

    async def test_stream_content_reads_chunks(self):
        response = FakeResponse(chunks=[b"abc", b"def"])
        self.assertEqual(await _stream_content(response, 10), b"abcdef")

    async def test_stream_content_stops_on_limit(self):
        response = FakeResponse(chunks=[b"abc", b"def"])
        self.assertIsNone(await _stream_content(response, 5))

    async def test_send_document_without_length(self):
        msg = FakeMessage(message_id=7)
        response = FakeResponse(
            content=b"payload",
            headers={"Content-Type": "application/octet-stream"},
            chunks=[b"payload"],
        )
        await send_content_from_response(response, msg, "file.bin")
        self.assertEqual(len(msg.media), 1)
        kind, file, kwargs = msg.media[0]
        self.assertEqual(kind, "document")
        self.assertEqual(file.data, b"payload")
        self.assertEqual(kwargs["reply_to_message_id"], 7)

    async def test_send_photo(self):
        msg = FakeMessage()
        response = FakeResponse(
            content=b"jpeg",
            headers={
                "Content-Type": "image/jpeg",
                "Content-Length": "4",
            },
        )
        await send_content_from_response(response, msg, "image.jpg")
        self.assertEqual(msg.media[0][0], "photo")
        self.assertEqual(msg.media[0][1].data, b"jpeg")

    async def test_send_audio(self):
        msg = FakeMessage()
        response = FakeResponse(
            content=b"mp3",
            headers={
                "Content-Type": "audio/mp3",
                "Content-Length": "3",
            },
        )
        await send_content_from_response(response, msg, "audio.mp3")
        self.assertEqual(msg.media[0][0], "audio")

    async def test_rejects_too_large_file(self):
        msg = FakeMessage()
        response = FakeResponse(
            content=b"x",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(MAX_FILE_SIZE + 1),
            },
        )
        self.assertIsNone(await send_content_from_response(response, msg, "big.bin"))
        self.assertEqual(msg.media, [])


class ExecutorTests(unittest.TestCase):
    def test_ffmpeg_executor_is_thread_based(self):
        self.assertIsInstance(_FFMPEG_EXECUTOR, ThreadPoolExecutor)


class AsyncConvertTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_function(self):
        self.assertEqual(await _async_convert(lambda value: value + 1, 1), 2)


class MediaTests(unittest.TestCase):
    def test_mp4_passthrough_does_not_call_ffmpeg(self):
        content = b"small mp4"
        self.assertIs(_convert_video_to_mp4(content, "video/mp4"), content)

    def test_resize_image_returns_small_image_unchanged(self):
        content = b"small"
        self.assertIs(_resize_image(content, MAX_PHOTO_SIZE), content)

    def test_resize_image_returns_none_for_invalid_data(self):
        self.assertIsNone(_resize_image(b"not an image", 1))

    def test_resize_image_produces_jpeg(self):
        # Ширина больше 10000, поэтому функция не будет увеличивать изображение.
        source = BytesIO()
        Image.new("RGB", (12000, 100), (10, 20, 30)).save(source, format="PNG")
        content = source.getvalue()
        max_size = len(content) // 2
        resized = _resize_image(content, max_size)
        self.assertIsNotNone(resized)
        with Image.open(BytesIO(resized)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), 10000)
        self.assertLessEqual(len(resized), max_size)

    def test_resize_image_preserves_aspect_ratio(self):
        # Для целочисленных размеров точное равенство пропорций невозможно:
        # допускаем отклонение не более одного пикселя по каждой стороне.
        for width, height in (
            (1200, 800),
            (800, 1200),
            (1001, 333),
            (333, 1001),
        ):
            with self.subTest(size=(width, height)):
                source = BytesIO()
                Image.frombytes(
                    "RGB",
                    (width, height),
                    os.urandom(width * height * 3),
                ).save(source, format="PNG")
                content = source.getvalue()
                resized = _resize_image(content, max_size=50000)
                self.assertIsNotNone(resized)
                with Image.open(BytesIO(resized)) as image:
                    new_width, new_height = image.size

                self.assertLessEqual(new_width, width)
                self.assertLessEqual(new_height, height)
                self.assertLessEqual(
                    abs(new_width * height - new_height * width),
                    max(width, height),
                    f"{width}x{height} -> {new_width}x{new_height}",
                )

    def test_resize_image_clamps_small_dimension_to_one(self):
        # При сильном уменьшении int(height * scale) может стать нулём.
        # max(1, ...) должен оставить сторону равной одному пикселю.
        for width, height in ((10000, 2), (2, 10000)):
            with self.subTest(size=(width, height)):
                source = BytesIO()
                Image.frombytes(
                    "RGB",
                    (width, height),
                    os.urandom(width * height * 3),
                ).save(source, format="PNG")
                content = source.getvalue()
                resized = _resize_image(content, max_size=1000)
                self.assertIsNotNone(resized)
                with Image.open(BytesIO(resized)) as image:
                    new_width, new_height = image.size

                self.assertGreaterEqual(new_width, 1)
                self.assertGreaterEqual(new_height, 1)
                self.assertEqual(min(new_width, new_height), 1)

    def test_resize_image_does_not_upscale(self):
        source = BytesIO()
        Image.frombytes("RGB", (5000, 1), os.urandom(15000)).save(
            source, format="PNG"
        )
        content = source.getvalue()
        resized = _resize_image(content, len(content) - 1)
        self.assertIsNotNone(resized)
        with Image.open(BytesIO(resized)) as image:
            self.assertEqual(image.size, (5000, 1))

    def test_resize_image_converts_rgba_to_jpeg(self):
        source = BytesIO()
        Image.new("RGBA", (400, 400), (255, 0, 0, 128)).save(
            source, format="PNG"
        )
        content = source.getvalue()
        resized = _resize_image(content, len(content) // 2)
        self.assertIsNotNone(resized)
        with Image.open(BytesIO(resized)) as image:
            self.assertEqual(image.format, "JPEG")


class ReplyTests(unittest.TestCase):
    def test_success_reply_with_int_size(self):
        reply = get_success_reply_str("photo", "ok", 1024)
        self.assertIn("photo", reply)
        self.assertIn("ok", reply)
        self.assertEqual(len(reply.splitlines()), 3)

    def test_success_reply_with_iterable_sizes(self):
        reply = get_success_reply_str("photo", "ok", [1024, 2048])
        self.assertEqual(len(reply.splitlines()), 4)

    def test_success_reply_without_size(self):
        self.assertEqual(get_success_reply_str("photo", "ok"), "Тип: photo.\nok")


if __name__ == "__main__":
    unittest.main()
