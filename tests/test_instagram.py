import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.downloader import download_video
from bot.handlers import process_and_upload, send_photo
from bot.instagram import download_instagram_photo, photo_details, render_photo, run_process, _fetch_photo
from pyrogram.errors import BadRequest


def photo_media():
    return {
        'media_type': 1,
        'image_versions2': {'candidates': [
            {'url': 'https://example.com/small.jpg', 'width': 100, 'height': 100},
            {'url': 'https://example.com/photo.jpg', 'width': 1080, 'height': 1350},
        ]},
        'caption': {'text': 'Photo caption'},
        'music_metadata': {'music_info': {
            'music_asset_info': {'progressive_download_url': 'https://example.com/audio.m4a'},
            'music_consumption_info': {'start_time_in_ms': 5000, 'duration_in_ms': 12000},
        }},
    }


class MetadataTests(unittest.TestCase):
    def test_photo_music_and_clip_timing(self):
        details = photo_details(photo_media())
        self.assertEqual(details['image_url'], 'https://example.com/photo.jpg')
        self.assertEqual(details['start'], 5)
        self.assertEqual(details['duration'], 12)
        self.assertEqual(details['title'], 'Photo caption')

    def test_legacy_photo_without_audio(self):
        details = photo_details({'__typename': 'XDTGraphImage', 'display_url': 'https://example.com/photo.jpg'})
        self.assertIsNone(details['audio_url'])
        self.assertEqual(details['duration'], 15)

    def test_no_thumbnail_for_video_or_carousel(self):
        for extra in ({'is_video': True}, {'video_versions': [{'url': 'video'}]},
                      {'carousel_media': [{}]}, {'edge_sidecar_to_children': {'edges': []}}):
            self.assertIsNone(photo_details(dict(photo_media(), **extra)))

    def test_invalid_timing_is_bounded(self):
        media = photo_media()
        media['music_metadata']['music_info']['music_consumption_info'] = {
            'start_time_in_ms': -5, 'duration_in_ms': float('inf'),
        }
        details = photo_details(media)
        self.assertEqual((details['start'], details['duration']), (0, 15))

    def test_fetch_photo_survives_audio_failure_and_uses_proxy(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        response = client.urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({'data': {
            'xdt_api__v1__media__shortcode__web_info': {'items': [photo_media()]},
        }}).encode()
        with tempfile.TemporaryDirectory() as directory, patch('bot.instagram.YoutubeDL') as ydl, \
                patch('bot.instagram._download_asset', side_effect=[None, OSError('audio unavailable')]):
            ydl.return_value.__enter__.return_value = client
            details = _fetch_photo('https://www.instagram.com/reel/ABC/', directory, 'socks5://localhost:1080', 'agent')
            self.assertIn('image_path', details)
            self.assertNotIn('audio_path', details)
            self.assertEqual(ydl.call_args.args[0]['proxy'], 'socks5://localhost:1080')


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_render_retains_image_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            details = {'image_path': 'photo.jpg', 'audio_path': 'audio.m4a',
                       'title': 'Title', 'start': 2, 'duration': 8}

            async def render(image, audio, output, start, duration):
                Path(output).write_bytes(b'video')
                self.assertEqual((start, duration), (2, 8))

            with patch('bot.instagram._fetch_photo', return_value=details), \
                    patch('bot.instagram.render_photo', side_effect=render):
                result = await download_instagram_photo('url', directory, None, 'agent')
            self.assertEqual(result['media_type'], 'video')
            self.assertEqual(result['fallback_image_path'], 'photo.jpg')
            self.assertTrue(Path(result['file_path']).exists())

    async def test_regular_video_skips_photo_fallback_and_partial_files(self):
        url = 'https://www.instagram.com/reel/ABC/'
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'unfinished.mp4.part').write_bytes(b'partial')
            Path(directory, 'Title [ABC].mp4').write_bytes(b'video')
            with patch('bot.downloader.normalise_url', return_value=url), \
                    patch('bot.downloader.tempfile.mkdtemp', return_value=directory), \
                    patch('bot.downloader.run_process', return_value=(0, b'', b'')), \
                    patch('bot.downloader.download_instagram_photo') as fallback:
                result = await download_video(url)
            self.assertEqual(result['media_type'], 'video')
            self.assertEqual(result['title'], 'Title')
            self.assertTrue(result['file_path'].endswith('Title [ABC].mp4'))
            fallback.assert_not_called()

    async def test_render_failure_returns_photo(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = str(Path(directory, 'photo.jpg'))
            Path(photo).write_bytes(b'image')
            details = {'image_path': photo, 'audio_path': 'audio.m4a', 'title': 'Title', 'start': 0, 'duration': 15}
            with patch('bot.instagram._fetch_photo', return_value=details), \
                    patch('bot.instagram.render_photo', side_effect=FileNotFoundError('ffmpeg')):
                result = await download_instagram_photo('url', directory, None, 'agent')
            self.assertEqual(result['media_type'], 'photo')
            self.assertEqual(result['file_path'], photo)

    async def test_missing_audio_returns_photo(self):
        details = {'image_path': 'photo.jpg', 'title': 'Title'}
        with patch('bot.instagram._fetch_photo', return_value=details), \
                patch('bot.instagram.render_photo') as render:
            result = await download_instagram_photo('url', '/tmp', None, 'agent')
        self.assertEqual(result['media_type'], 'photo')
        render.assert_not_called()

    async def test_instagram_download_failure_uses_photo_fallback(self):
        url = 'https://www.instagram.com/reel/ABC/'
        with patch('bot.downloader.normalise_url', return_value=url), \
                patch('bot.downloader.run_process', return_value=(1, b'', b'No video')), \
                patch('bot.downloader.download_instagram_photo', return_value={'success': True}) as fallback:
            result = await download_video(url, 'socks5://localhost:1080')
        directory = fallback.call_args.args[1]
        shutil.rmtree(directory)
        self.assertTrue(result['success'])
        self.assertEqual(fallback.call_args.args[2], 'socks5://localhost:1080')

    async def test_other_platform_failure_does_not_use_instagram(self):
        url = 'https://youtube.com/shorts/ABC'
        with patch('bot.downloader.normalise_url', return_value=url), \
                patch('bot.downloader.run_process', return_value=(1, b'', b'failed')), \
                patch('bot.downloader.download_instagram_photo') as fallback:
            result = await download_video(url)
        shutil.rmtree(result['temp_dir'])
        self.assertFalse(result['success'])
        fallback.assert_not_called()

    async def test_process_timeout_reaps_child(self):
        with self.assertRaises(asyncio.TimeoutError):
            await run_process('python3', '-c', 'import time; time.sleep(60)', timeout=0.05)


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_and_failed_video_upload_with_cleanup(self):
        for media_type in ('photo', 'video'):
            with self.subTest(media_type=media_type):
                directory = tempfile.mkdtemp()
                path = Path(directory, 'media.jpg')
                path.write_bytes(b'media')
                result = {'success': True, 'file_path': str(path), 'media_type': media_type,
                          'title': 'Title', 'download_url': 'url', 'temp_dir': directory,
                          'fallback_image_path': str(path)}
                message = SimpleNamespace(reply_text=AsyncMock(return_value=AsyncMock()),
                                          reply_video=AsyncMock(side_effect=BadRequest('bad video')),
                                          reply_photo=AsyncMock())
                with patch('bot.handlers.download_video', return_value=result):
                    await process_and_upload(message, 'https://instagram.com/reel/ABC/')
                message.reply_photo.assert_awaited_once()
                self.assertFalse(os.path.exists(directory))

    async def test_rejected_photo_sent_as_document(self):
        with tempfile.NamedTemporaryFile() as photo:
            message = SimpleNamespace(reply_photo=AsyncMock(side_effect=BadRequest('dimensions')),
                                      reply_document=AsyncMock())
            await send_photo(message, photo.name, 'caption')
            message.reply_document.assert_awaited_once()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class RenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_render_has_h264_aac_and_clip_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            image = str(Path(directory, 'photo.ppm'))
            # Odd dimensions exercise padding needed by H.264/yuv420p.
            Path(image).write_bytes(b'P6\n101 151\n255\n' + bytes([40, 100, 200]) * 101 * 151)
            audio = str(Path(directory, 'audio.wav'))
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', audio], check=True)
            output = str(Path(directory, 'output.mp4'))
            await render_photo(image, audio, output, start=1, duration=1)
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', output]))
            self.assertEqual({s['codec_name'] for s in probe['streams']}, {'h264', 'aac'})
            self.assertAlmostEqual(float(probe['format']['duration']), 1, delta=0.15)
            video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            self.assertEqual(video['width'] % 2, 0)
            self.assertEqual(video['height'] % 2, 0)

    async def test_audio_offset_past_end_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            image = str(Path(directory, 'photo.ppm'))
            Path(image).write_bytes(b'P6\n10 10\n255\n' + bytes([40, 100, 200]) * 100)
            audio = str(Path(directory, 'audio.wav'))
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.1', audio], check=True)
            with self.assertRaises(ValueError):
                await render_photo(image, audio, str(Path(directory, 'output.mp4')), start=10, duration=1)


if __name__ == '__main__':
    unittest.main()
