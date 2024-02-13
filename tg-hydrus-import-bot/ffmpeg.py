import subprocess
from typing import Literal, cast
import ffmpy

OutPresetName = Literal["x264", "x265", "x265-gpu"]

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
    out_codecs = {
        "x264": "h264",
        "x265": "libx265 -preset medium -x265-params crf=25",
        "x265-gpu": "hevc_nvenc -preset p7 -tune hq -cq:v 25",
    }
    ff = ffmpy.FFmpeg(
        global_options = '-hide_banner -loglevel quiet -nostats',
        inputs = {
            'pipe:0': f'-f {input_format}'
        },
        outputs = {
            'pipe:1': f'-c:v {out_codecs[output_codec]} -f mp4 -movflags frag_keyframe+empty_moov'
        }
    )
    stdout, _ = cast(tuple[bytes, bytes], ff.run(input_data=raw, stdout=subprocess.PIPE))
    return stdout
