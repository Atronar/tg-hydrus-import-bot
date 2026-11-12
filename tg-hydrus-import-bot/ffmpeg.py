import subprocess
from typing import Literal, cast
import ffmpy
from loguru import logger
from config import CONF

OutPresetName = Literal["x264-fast", "x264", "x265", "x265-gpu"]

# Предварительно заданные параметры для функции
_OUT_CODECS = {
    "x264-fast": "libx264 -preset veryfast -crf 23",
    "x264": "libx264 -preset slow -crf 23",
    "x265": "libx265 -preset medium -x265-params crf=25 -strong-intra-smoothing=0",
    "x265-gpu": "hevc_nvenc -preset p6 -tune hq -cq:v 25",
}


_FFMPEG_LOGLEVELS = {
    "TRACE": "trace",
    "DEBUG": "debug",
    "INFO": "info",
    "SUCCESS": "verbose",
    "WARNING": "warning",
    "ERROR": "error",
    "CRITICAL": "fatal",
}

# Числовые значения стандартных уровней loguru (5, 10, 20, 25, 30, 40, 50)
_FFMPEG_INT_LOGLEVELS = {
    logger.level(name).no: ffmpeg_level
    for name, ffmpeg_level in _FFMPEG_LOGLEVELS.items()
}


def _ffmpeg_loglevel(level: int|str) -> str:
    """Переводит уровень логирования loguru в уровень ffmpeg.

    Конфиг допускает как имена уровней, так и числа. Неизвестный
    уровень заменяется на info, чтобы импорт модуля не падал.
    """
    if isinstance(level, str):
        return _FFMPEG_LOGLEVELS.get(level, "info")
    return _FFMPEG_INT_LOGLEVELS.get(level, "info")


_FFMPEG_GLOBAL_OPTIONS = [
    "-hide_banner",     # Не выводить баннер
    f"-loglevel {_ffmpeg_loglevel(CONF.LOG_LEVEL)}",  # Не выводить лог
    "-nostdin",         # Не ждать ввода
    "-y",               # Перезапись без подтверждения
    "-nostats",         # Не выводить прогресс
    "-hwaccel auto"     # аппаратное ускорение входного потока
]

def get_io_mp4(
    raw: bytes,
    input_format: str | None = None,
    output_codec: OutPresetName = "x264-fast"
) -> bytes:
    """
    Конвертация байтового видеоконтента в формат mp4

    Parameters
    ----------
    raw : bytes
        Видео в байтовом представлении
        
    input_format: str | None
        Формат, из которого производится конвертация.
        None — ffmpeg сам определяет формат по содержимому.
        
    output_codec: "x264-fast", "x264", "x265", "x265-gpu"
        Кодек, используемый для конвертации

    Returns
    -------
    bytes
        Возвращается видео, перекодированное в формат mp4, в байтовом представлении
    """
    ff = ffmpy.FFmpeg(
        global_options = _FFMPEG_GLOBAL_OPTIONS,
        inputs = {
            # При input_format=None ffmpeg сам разбирает поток с pipe:0
            'pipe:0': f'-f {input_format}' if input_format else ''
        },
        outputs = {
            'pipe:1': f'-c:v {_OUT_CODECS[output_codec]} -f mp4 -movflags frag_keyframe+empty_moov+default_base_moof'
        }
    )
    # ffmpy сам выбрасывает FFRuntimeError при ненулевом коде возврата.
    # Ненулевой stderr не является ошибкой: ffmpeg пишет туда предупреждения.
    stdout, _ = cast(
        tuple[bytes, bytes],
        ff.run(input_data=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    )
    return stdout
