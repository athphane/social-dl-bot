from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.types import Chat, Message, User

from bot.access import is_user_allowed
from bot.handlers import MAX_FILE_SIZE, process_and_upload, register_handlers
from main import build_client


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_above_50mb_uses_mtproto_reply_and_cleans_up(self):
        directory = tempfile.mkdtemp()
        video = Path(directory, 'large.mp4')
        # Sparse file exercises the real size check without writing 51 MiB.
        with video.open('wb') as file:
            file.truncate(51 * 1024 * 1024)
        status = AsyncMock()
        client = SimpleNamespace(
            send_message=AsyncMock(return_value=status),
            send_video=AsyncMock(spec=Client.send_video),
        )
        message = Message(
            id=42, chat=Chat(id=-100123, type=ChatType.SUPERGROUP),
            client=client,
        )
        result = {
            'success': True, 'file_path': str(video), 'title': 'A <b>title</b>',
            'download_url': 'https://youtube.com/shorts/ABC', 'temp_dir': directory,
        }
        with patch('bot.handlers.download_video', return_value=result):
            await process_and_upload(message, result['download_url'])

        client.send_video.assert_awaited_once()
        args = client.send_video.await_args.kwargs
        self.assertEqual(args['video'], str(video))
        self.assertEqual(args['chat_id'], -100123)
        self.assertEqual(args['reply_parameters'].message_id, 42)
        self.assertTrue(args['supports_streaming'])
        self.assertEqual(args['parse_mode'], ParseMode.DISABLED)
        status.delete.assert_awaited_once()
        self.assertFalse(Path(directory).exists())

    async def test_oversized_video_is_rejected_before_upload(self):
        directory = tempfile.mkdtemp()
        video = Path(directory, 'too-large.mp4')
        with video.open('wb') as file:
            file.truncate(MAX_FILE_SIZE + 1)
        status = AsyncMock()
        message = SimpleNamespace(
            reply_text=AsyncMock(return_value=status), reply_video=AsyncMock(),
        )
        with patch('bot.handlers.download_video', return_value={
            'success': True, 'file_path': str(video), 'temp_dir': directory,
        }), patch('bot.handlers.asyncio.sleep', new_callable=AsyncMock):
            await process_and_upload(message, 'https://youtube.com/shorts/ABC')
        message.reply_video.assert_not_awaited()
        self.assertIn('2000 MiB', status.edit_text.await_args.args[0])
        self.assertFalse(Path(directory).exists())


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handlers = []
        self.client = SimpleNamespace(
            add_handler=self.handlers.append,
            me=SimpleNamespace(username='social_dl_bot'),
            get_chat_member=AsyncMock(),
            send_message=AsyncMock(),
        )
        register_handlers(self.client, socks5_proxy='socks5://localhost:1080')

    def message(self, text, *, user_id=123, chat_id=-1001837077491,
                chat_type=ChatType.SUPERGROUP, outgoing=False):
        return Message(
            id=42, text=text, from_user=User(id=user_id),
            chat=Chat(id=chat_id, type=chat_type), outgoing=outgoing,
            client=self.client,
        )

    async def dispatch(self, message):
        # Match the dispatcher's first matching handler behavior.
        for handler in self.handlers:
            if await handler.check(self.client, message):
                await handler.callback(self.client, message)
                return handler.callback.__name__

    async def test_group_link_and_addressed_manual_command(self):
        with patch('bot.handlers.process_and_upload', new_callable=AsyncMock) as upload:
            link = self.message('https://youtube.com/shorts/ABC')
            self.assertEqual(await self.dispatch(link), 'auto_download')
            upload.assert_awaited_once_with(link, link.text, 'socks5://localhost:1080')
            upload.reset_mock()
            command = self.message('/dl@social_dl_bot https://youtu.be/ABC')
            self.assertEqual(await self.dispatch(command), 'dl_command')
            upload.assert_awaited_once_with(command, 'https://youtu.be/ABC', 'socks5://localhost:1080')

    async def test_group_restrictions_and_ignored_senders(self):
        with patch('bot.handlers.process_and_upload', new_callable=AsyncMock) as upload:
            for options in (
                {'chat_id': -100999}, {'outgoing': True},
                {'user_id': 352665135}, {'user_id': 394234655},
            ):
                await self.dispatch(self.message('https://youtube.com/shorts/ABC', **options))
            upload.assert_not_awaited()

    async def test_private_links_and_commands_require_access(self):
        with patch('bot.handlers.is_user_allowed', return_value=False), \
                patch('bot.handlers.process_and_upload', new_callable=AsyncMock) as upload:
            for text in ('https://youtu.be/ABC', '/dl https://youtu.be/ABC'):
                await self.dispatch(self.message(text, chat_type=ChatType.PRIVATE, chat_id=123))
            upload.assert_not_awaited()
            self.assertEqual(self.client.send_message.await_count, 2)

        with patch('bot.handlers.is_user_allowed', return_value=True), \
                patch('bot.handlers.process_and_upload', new_callable=AsyncMock) as upload:
            for text in ('https://youtu.be/ABC', '/dl https://youtu.be/ABC'):
                await self.dispatch(self.message(text, chat_type=ChatType.PRIVATE, chat_id=123))
            self.assertEqual(upload.await_count, 2)

    async def test_allow_command_is_owner_only(self):
        with patch('bot.handlers.add_user', return_value=True) as add:
            await self.dispatch(self.message('/allow 456'))
            add.assert_not_called()
            await self.dispatch(self.message('/allow 456', user_id=352665135))
            add.assert_called_once_with(456)


class MembershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_mtproto_membership_statuses(self):
        client = SimpleNamespace(get_chat_member=AsyncMock())
        with patch('bot.access.OWNER_ID', 999), \
                patch('bot.access.ALLOWED_GROUP', -100123), \
                patch('bot.access._load_allowlist', return_value=set()):
            for status, is_member, expected in (
                (ChatMemberStatus.MEMBER, None, True),
                (ChatMemberStatus.ADMINISTRATOR, None, True),
                (ChatMemberStatus.OWNER, None, True),
                (ChatMemberStatus.RESTRICTED, True, True),
                (ChatMemberStatus.RESTRICTED, False, False),
                (ChatMemberStatus.LEFT, None, False),
                (ChatMemberStatus.BANNED, None, False),
            ):
                with self.subTest(status=status, is_member=is_member):
                    client.get_chat_member.return_value = SimpleNamespace(status=status, is_member=is_member)
                    self.assertEqual(await is_user_allowed(123, client), expected)


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_configures_bot_credentials_and_persistent_session(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, 'config.ini')
            config.write_text(
                '[pyrogram]\napi_id = 12345\napi_hash = app-hash\n'
                'bot_token = 123:bot-token\nworkdir = sessions\n'
                '[proxy]\nsocks5_proxy = socks5://localhost:1080\n'
            )
            with patch('main.Client', return_value=Mock()) as client, \
                    patch('main.register_handlers') as register:
                result = build_client(config)
            client.assert_called_once_with(
                'social_dl_bot', api_id=12345, api_hash='app-hash',
                bot_token='123:bot-token', workdir=str(Path(directory, 'sessions')), workers=1,
            )
            register.assert_called_once_with(result, socks5_proxy='socks5://localhost:1080')
            self.assertTrue(Path(directory, 'sessions').is_dir())

    async def test_missing_or_invalid_credentials_fail_without_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, 'config.ini')
            for text in (
                '[bot]\nbot_token = old-token\n',
                '[pyrogram]\napi_id = invalid\napi_hash = hash\nbot_token = token\n',
                '[pyrogram]\napi_id = 12345\nbot_token = token\n',
            ):
                config.write_text(text)
                with patch('main.Client') as client, self.assertRaises(ValueError):
                    build_client(config)
                client.assert_not_called()
