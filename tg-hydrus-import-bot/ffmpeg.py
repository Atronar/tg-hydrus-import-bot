import subprocess
from typing import Literal, cast
import ffmpy

OutPresetName = Literal["x264", "x265", "x265-gpu"]

# Предварительно заданные параметры для функции
_OUT_CODECS = {
    "x264": "libx264 -preset slow -crf 23",
    "x265": "libx265 -preset medium -x265-params crf=25 -strong-intra-smoothing=0",
    "x265-gpu": "hevc_nvenc -preset p6 -tune hq -cq:v 25",
}

_FFMPEG_GLOBAL_OPTIONS = [
    "-hide_banner",     # Не выводить баннер
    "-loglevel error",  # Не выводить лог
    "-nostdin",         # Не ждать ввода
    "-y",               # Перезапись без подтверждения
    "-nostats",         # Не выводить прогресс
    "-hwaccel auto"     # аппаратное ускорение входного потока
]

def get_io_mp4(
    raw: bytes,
    input_format: str = "rawvideo",
    output_codec: OutPresetName = "x264"
) -> bytes:
    """
    Конвертация байтового видеоконтента в формат mp4

    Parameters
    ----------
    raw : bytes
        Видео в байтовом представлении
        
    input_format: str
        Формат из которого производится конвертация
        
    output_codec: "x264", "x265", "x265-gpu"
        Кодек, используемый для конвертации

    Returns
    -------
    bytes
        Возвращается видео, перекодированное в формат mp4, в байтовом представлении
    """
    ff = ffmpy.FFmpeg(
        global_options = _FFMPEG_GLOBAL_OPTIONS,
        inputs = {
            'pipe:0': f'-f {input_format}'
        },
        outputs = {
            'pipe:1': f'-c:v {_OUT_CODECS[output_codec]} -f mp4 -movflags frag_keyframe+empty_moov+default_base_moof'
        }
    )
    stdout, stderr = cast(tuple[bytes, bytes], ff.run(input_data=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE))

    if stderr:
        raise RuntimeError(f"FFmpeg error: {stderr.decode(errors='ignore')}")

    return stdout
