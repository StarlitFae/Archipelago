from __future__ import annotations
import asyncio
import logging
import typing
import enum
from json import JSONEncoder, JSONDecoder

import websockets


class JSONMessagePart(typing.TypedDict):
    type: typing.Optional[str]
    color: typing.Optional[str]
    text: typing.Optional[str]


def _scan_for_TypedTuples(obj: typing.Any) -> typing.Any:
    if isinstance(obj, tuple) and hasattr(obj, "_fields"):  # NamedTuple is not actually a parent class
        data = obj._asdict()
        data["class"] = obj.__class__.__name__
        return data
    if isinstance(obj, (tuple, list)):
        return tuple(_scan_for_TypedTuples(o) for o in obj)
    if isinstance(obj, dict):
        return {key: _scan_for_TypedTuples(value) for key, value in obj.items()}
    return obj


_encode = JSONEncoder(
    ensure_ascii=False,
    check_circular=False,
).encode


def encode(obj):
    return _encode(_scan_for_TypedTuples(obj))

from Utils import Version # for object hook
whitelist = {"NetworkPlayer", "NetworkItem", "Version"}

def _object_hook(o: typing.Any) -> typing.Any:
    if isinstance(o, dict):
        cls = o.get("class", None)
        if cls in whitelist:
            del (o["class"])
            return globals()[cls](**o)

    return o


decode = JSONDecoder(object_hook=_object_hook).decode


class Node:
    endpoints: typing.List
    dumper = staticmethod(encode)
    loader = staticmethod(decode)

    def __init__(self):
        self.endpoints = []
        super(Node, self).__init__()

    def broadcast_all(self, msgs):
        msgs = self.dumper(msgs)
        for endpoint in self.endpoints:
            asyncio.create_task(self.send_encoded_msgs(endpoint, msgs))

    async def send_msgs(self, endpoint: Endpoint, msgs: typing.Iterable[dict]):
        if not endpoint.socket or not endpoint.socket.open or endpoint.socket.closed:
            return
        msg = self.dumper(msgs)
        try:
            await endpoint.socket.send(msg)
        except websockets.ConnectionClosed:
            logging.exception(f"Exception during send_msgs, could not send {msg}")
            await self.disconnect(endpoint)

    async def send_encoded_msgs(self, endpoint: Endpoint, msg: str):
        if not endpoint.socket or not endpoint.socket.open or endpoint.socket.closed:
            return
        try:
            await endpoint.socket.send(msg)
        except websockets.ConnectionClosed:
            logging.exception("Exception during send_msgs")
            await self.disconnect(endpoint)

    async def disconnect(self, endpoint):
        if endpoint in self.endpoints:
            self.endpoints.remove(endpoint)


class Endpoint:
    socket: websockets.WebSocketServerProtocol

    def __init__(self, socket):
        self.socket = socket

    async def disconnect(self):
        raise NotImplementedError


class HandlerMeta(type):
    def __new__(mcs, name, bases, attrs):
        handlers = attrs["handlers"] = {}
        trigger: str = "_handle_"
        for base in bases:
            handlers.update(base.commands)
        handlers.update({handler_name[len(trigger):]: method for handler_name, method in attrs.items() if
                         handler_name.startswith(trigger)})

        orig_init = attrs.get('__init__', None)

        def __init__(self, *args, **kwargs):
            # turn functions into bound methods
            self.handlers = {name: method.__get__(self, type(self)) for name, method in
                             handlers.items()}
            if orig_init:
                orig_init(self, *args, **kwargs)

        attrs['__init__'] = __init__
        return super(HandlerMeta, mcs).__new__(mcs, name, bases, attrs)


class JSONtoTextParser(metaclass=HandlerMeta):
    def __init__(self, ctx: "MultiClient.Context"):
        self.ctx = ctx

    def __call__(self, input_object: typing.List[JSONMessagePart]) -> str:
        return "".join(self.handle_node(section) for section in input_object)

    def handle_node(self, node: JSONMessagePart):
        type = node.get("type", None)
        handler = self.handlers.get(type, self.handlers["text"])
        return handler(node)

    def _handle_color(self, node: JSONMessagePart):
        if node["color"] in color_codes:
            return color_code(node["color"]) + self._handle_text(node) + color_code("reset")
        else:
            logging.warning(f"Unknown color in node {node}")
            return self._handle_text(node)

    def _handle_text(self, node: JSONMessagePart):
        return node.get("text", "")

    def _handle_player_id(self, node: JSONMessagePart):
        player = node["text"]
        node["color"] = 'yellow' if player != self.ctx.slot else 'magenta'
        node["text"] = self.ctx.player_names[player]
        return self._handle_color(node)

    # for other teams, spectators etc.? Only useful if player isn't in the clientside mapping
    def _handle_player_name(self, node: JSONMessagePart):
        node["color"] = 'yellow'
        return self._handle_color(node)


color_codes = {'reset': 0, 'bold': 1, 'underline': 4, 'black': 30, 'red': 31, 'green': 32, 'yellow': 33, 'blue': 34,
               'magenta': 35, 'cyan': 36, 'white': 37, 'black_bg': 40, 'red_bg': 41, 'green_bg': 42, 'yellow_bg': 43,
               'blue_bg': 44, 'purple_bg': 45, 'cyan_bg': 46, 'white_bg': 47}


def color_code(*args):
    return '\033[' + ';'.join([str(color_codes[arg]) for arg in args]) + 'm'


def color(text, *args):
    return color_code(*args) + text + color_code('reset')


class CLientStatus(enum.IntEnum):
    CLIENT_UNKNOWN = 0
    # CLIENT_CONNECTED = 5 maybe?
    CLIENT_READY = 10
    CLIENT_PLAYING = 20
    CLIENT_GOAL = 30


class NetworkPlayer(typing.NamedTuple):
    team: int
    slot: int
    alias: str
    name: str


class NetworkItem(typing.NamedTuple):
    item: int
    location: int
    player: int
