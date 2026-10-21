import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bot.downloader import download_video, prepare_telegram_video


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class YouTubeTests(unittest.IsolatedAsyncioTestCase):
    async def test_webm_download_returns_streamable_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'Short [Dr_5Spw9z94].webm')
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=102x152:duration=0.5',
                '-f', 'lavfi', '-i', 'sine=duration=0.5', '-c:v', 'libvpx-vp9',
                '-c:a', 'libopus', str(source),
            ], check=True)
            from bot.instagram import run_process

            async def process(*args):
                if args[0] == 'yt-dlp':
                    return 0, b'', b''
                return await run_process(*args)

            with patch('bot.downloader.tempfile.mkdtemp', return_value=directory), \
                    patch('bot.downloader.run_process', side_effect=process):
                result = await download_video('https://youtube.com/shorts/Dr_5Spw9z94')
            self.assertTrue(result['success'], result)
            self.assertEqual(result['title'], 'Short')
            output = Path(result['file_path'])
            self.assertEqual(output.suffix, '.mp4')
            probe = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output),
            ]))
            self.assertEqual({s['codec_name'] for s in probe['streams']}, {'h264', 'aac'})
            video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height'], video['pix_fmt']), (102, 152, 'yuv420p'))
            data = output.read_bytes()
            self.assertLess(data.index(b'moov'), data.index(b'mdat'))

    async def test_compatible_silent_mp4_is_remuxed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'source.mp4')
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=100x150:duration=0.5',
                '-c:v', 'libx264', str(source),
            ], check=True)
            output = await prepare_telegram_video(str(source))
            def frames(path):
                return subprocess.check_output([
                    'ffmpeg', '-v', 'error', '-i', str(path), '-f', 'framemd5', '-',
                ])
            self.assertEqual(frames(source), frames(output))

    async def test_conversion_failure_does_not_return_webm(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'broken.webm').write_bytes(b'invalid')
            with patch('bot.downloader.tempfile.mkdtemp', return_value=directory), \
                    patch('bot.downloader.run_process', return_value=(0, b'', b'')), \
                    patch('bot.downloader.prepare_telegram_video', side_effect=RuntimeError('conversion failed')):
                result = await download_video('https://youtube.com/shorts/Dr_5Spw9z94')
            self.assertFalse(result['success'])
            self.assertIsNone(result['file_path'])


class DownloadErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('bot.downloader.tempfile.mkdtemp', return_value=directory), \
                    patch('bot.downloader.run_process', return_value=(1, b'', b'WARNING: HTTP Error 429: Too Many Requests')) as process:
                result = await download_video('https://youtube.com/shorts/Dr_5Spw9z94')
            process.assert_awaited_once()
            self.assertFalse(result['success'])
            self.assertIn('rate-limiting', result['error'])

    async def test_final_error_survives_long_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('bot.downloader.tempfile.mkdtemp', return_value=directory), \
                    patch('bot.downloader.run_process', return_value=(1, b'', b'WARNING: ' + b'x' * 600 + b'\nERROR: Video unavailable')):
                result = await download_video('https://youtube.com/shorts/Dr_5Spw9z94')
            self.assertEqual(result['error'], 'Video unavailable')
