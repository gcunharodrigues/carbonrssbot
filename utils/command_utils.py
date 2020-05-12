import logging
import shlex

from telethon.tl.types import MessageEntityBotCommand
from telethon.tl.custom.message import Message

from .argumentparser import ArgumentParser, ArgumentParserExit
from . import cut_message_and_send, Namespace

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, List, Tuple, Text
    from telethon import events
    from telethon.tl.types import TypeMessageEntity


logger = logging.getLogger(__name__)


class Argument(str):
    """
    Simple string able to hold MessageEntity and pass it through ArgumentParser
    """
    def __init__(
        self, *args,
        cmd: 'Optional[CalledCommand]' = None,
        msg_entities: 'Optional[List[TypeMessageEntity]]' = None,
        start: 'Optional[int]' = None, end: 'Optional[int]' = None,
        **kwargs
    ):
        # super(Argument, self).__init__(*args, **kwargs)
        self.cmd = cmd
        self.msg_entities = list(msg_entities) if msg_entities is not None else []
        self.start, self.end = start, end


class Command(ArgumentParser):
    def __init__(self, prog: str, parents=[], add_help: bool = True, **kwargs):
        """
        Parses command `prog` from Telegram message using `argparser`

        Pass instance of this class as `func` argument for NewMessage event in event handler decorator
        It will handle commands in format `/prog` and `/prog@bot_username` and set `event.cmd` to corresponding `CalledCommand`
        It will NOT handle commands inside messages, just in start of message!

        :param prog:    command phrase/name
        :param type:    the same as in argparse.ArgumentParse, but..
        If you'll use type not created for this purpose you will lose MessageEntity information from arguments
        """
        super(Command, self).__init__(parents=parents, add_help=add_help, **kwargs)

        self.prog = prog if prog.startswith("/") else "/" + prog
        self._kwargs = kwargs

    def __call__(self, ev: 'events.NewMessage.Event'):
        me = ev.client.me
        username = None if me is None else me.username

        try:
            for ent, txt in ev.get_entities_text():
                if isinstance(ent, MessageEntityBotCommand) and ent.offset == 0 and (
                    txt == self.prog or (
                        username is not None and txt.endswith(f'@{username}') and
                        txt[:-len(username) - 1] == self.prog
                    )
                ):
                    ev.cmd = cmd = CalledCommand(self, txt, ev.message)

                    try:
                        cmd.parse_args()
                    except ArgumentParserExit:
                        # it was that command but it failed, let's show fail message and don't call cmd callback
                        ev.client.loop.create_task(cut_message_and_send(ev.reply, cmd.consume_messages()))
                        return False
                    return True
        except Exception as e:
            ev.client.loop.create_task(cut_message_and_send(ev.reply, f'{e.__class__.__name__}: {e!r}'))
            logger.debug('Error in parsing command', exc_info=True)
        return False


class CalledCommand(Command):
    # It's threadsafe!
    def __init__(self, cmd: Command, prog: str, msg: Message):
        super(CalledCommand, self).__init__(prog=prog, add_help=False, parents=[cmd], **cmd._kwargs)

        self.raw_args: 'Tuple[Argument, ...]' = ()
        self.bounds: 'Tuple[int, ...]' = ()
        self.args: 'Optional[Namespace]' = None
        self.text: 'Text' = msg.message
        self.msg = msg

    def convert_arg_line_to_args(self):
        start = len(self.prog)+1

        parser = shlex.shlex(self.text[start:])
        parser.whitespace_split = True
        parser.commenters = ''
        # same behaviour as shlex.split

        bounds = [start]
        args = []
        for arg in parser:
            _arg = Argument(arg)
            _arg.cmd = self
            _arg.start = bounds[-1]

            end = parser.instream.tell() + start
            _arg.end = end - 1
            bounds.append(end)

            args.append(_arg)

        self.raw_args = tuple(args)
        self.bounds = tuple(bounds)

        # matching of MessageEntities to arguments for pushing them through ArgumentParser
        # THEY WILL GET LOST WHEN YOU WILL MAKE ARGPARSE CAST THEM AND TYPE DOESN'T KNOW IT SHOULD CARE ABOUT IT!
        for entity in self.msg.entities:
            for a in args:
                entity_end = entity.offset + entity.length

                # start of entity in argument
                # end of entity in argument
                # start of argument in entity
                # end of argument in entity
                if a.start <= entity.offset <= a.end \
                    or a.start <= entity_end <= a.end \
                    or entity.offset <= a.start <= entity_end \
                    or entity.offset <= a.end <= entity_end \
                :
                    a.msg_entities.append(entity)

        return args

    def parse_args(self) -> Namespace:
        self.args = Namespace(
            super(CalledCommand, self).parse_args(
                self.convert_arg_line_to_args()
            ).__dict__
        )

        return self.args
