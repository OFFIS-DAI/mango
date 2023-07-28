"""
Utility classes for handling multiprocessing in mango, especially focusing on IPC in an asyncio context.

The package contains two different variants of async pipes for IPC: duplex, and non-duplex pipes. For creating 
these pipes, use aiopipe() or aioduplex(). The idea of the code is based on the pypi package 'aiopipe'.

These pipes provide async compatible APIs, here a general example:

main, sub = aioduplex()
with sub.detach() as sub:
    # start your process with sub as inherited pipe
async with main.open() as (rx, tx):
    item = await rx.read_object()
    tx.write_object()
    ...

Further there are internal connection objects, which can be used if a synchronous access outside of the
asyncio loop is necessary: 'main.write_connection, main.read_connection'. Note, that you can't use
'write_connection' if the pipe has been opened with 'open()', as this will lock the write access to the pipe.
For that case you could use 'open_readonly()', 
"""
import os
import asyncio
import io
import struct
from typing import Tuple, Any, ContextManager, AsyncContextManager
from contextlib import contextmanager, asynccontextmanager

import dill, multiprocessing

"""
dill.Pickler.dumps, dill.Pickler.loads = dill.dumps, dill.loads
multiprocessing.reduction.ForkingPickler = dill.Pickler
multiprocessing.reduction.dump = dill.dump
"""

from multiprocessing.reduction import ForkingPickler
from multiprocessing.connection import Connection


def aiopipe() -> Tuple["AioPipeReader", "AioPipeWriter"]:
    """Create a pair of pipe endpoints, both readable and writable (duplex).

    :return: Reader-, Writer-Pair
    """
    rx, tx = os.pipe()
    return AioPipeReader(rx), AioPipeWriter(tx)


def aioduplex() -> Tuple["AioDuplex", "AioDuplex"]:
    """Create a pair of pipe endpoints, both readable and writable (duplex).

    :return: AioDuplex-Pair
    """
    rxa, txa = aiopipe()
    rxb, txb = aiopipe()

    return AioDuplex(rxa, txb), AioDuplex(rxb, txa)


class AioPipeStream:
    """Stream-like wrapper for a file descriptor, which implements 'async with open' and 'with detach'"""

    def __init__(self, fd):
        self._fd = fd
        self._closed = False

    @asynccontextmanager
    async def open(self):
        assert not self._closed
        transport, stream = await self._open()
        try:
            yield stream
        finally:
            try:
                transport.close()
            except OSError:
                pass

            await asyncio.sleep(0)

    async def _open(self) -> Tuple[asyncio.BaseTransport, Any]:
        raise NotImplementedError()

    @contextmanager
    def detach(self) -> ContextManager["AioPipeStream"]:
        try:
            os.set_inheritable(self._fd, True)
            yield self
        finally:
            self.close()
            self._closed = True

    def _close(self):
        try:
            pass
            # os.close(self._fd)
        except OSError:
            pass
        finally:
            self._closed = True

    def close(self):
        if not self._closed:
            self._close()

    def __del__(self):
        self.close()


class ObjectStreamReader:
    """Wraps a StreamReader to add the ability to read
    pickled objects from the stream
    """

    def __init__(self, stream_reader: asyncio.StreamReader) -> None:
        self._stream_reader = stream_reader

    async def _recv(self, size):
        buf = io.BytesIO()
        remaining = size
        while remaining > 0:
            chunk = await self._stream_reader.read(remaining)
            n = len(chunk)
            if n == 0:
                if remaining == size:
                    raise EOFError
                else:
                    raise OSError("got end of file during message")
            buf.write(chunk)
            remaining -= n
        return buf

    async def _recv_bytes(self, maxsize=None):
        buf = await self._recv(4)
        (size,) = struct.unpack("!i", buf.getvalue())
        if size == -1:
            buf = await self._recv(8)
            (size,) = struct.unpack("!Q", buf.getvalue())
        if maxsize is not None and size > maxsize:
            return None
        return await self._recv(size)

    async def read_object(self):
        buf = await self._recv_bytes()
        return dill.loads(buf.getbuffer())

    async def read_bytes(self):
        return (await self._recv_bytes()).getbuffer()


class ObjectStreamWriter:
    """Wraps a StreamWriter to add the ability to write
    objects to the stream
    """

    def __init__(self, stream_writer: asyncio.StreamWriter) -> None:
        self._stream_writer = stream_writer

    def _write(self, buf):
        self._stream_writer.write(buf)

    def _write_bytes(self, buf):
        n = len(buf)
        if n > 0x7FFFFFFF:
            pre_header = struct.pack("!i", -1)
            header = struct.pack("!Q", n)
            self._write(pre_header)
            self._write(header)
            self._write(buf)
        else:
            header = struct.pack("!i", n)
            if n > 16384:
                self._write(header)
                self._write(buf)
            else:
                self._write(header + buf)

    def write_object(self, object):
        comp = ForkingPickler.dumps(object, protocol=-1)
        # comp = gzip.compress(comp)
        self._write_bytes(comp)

    def write_bytes(self, buf):
        self._write_bytes(buf)

    async def drain(self):
        await self._stream_writer.drain()


class OwnershiplessConnection(Connection):
    """Subclass of the mp Connection, which marks it as ownershipless. Following
    this class won't close the fd under any circumstance on its own.

    :param Connection: Connection object
    :type Connection: multiprocessing.connection.Connection
    """

    def __del__(self):
        pass

    def _close(self, _close=None):
        pass

    def close(self):
        pass

    def send(self, obj):
        """Send a (picklable) object"""
        self._check_closed()
        self._check_writable()
        self._send_bytes(ForkingPickler.dumps(obj, protocol=-1))

    def recv(self):
        """Receive a (picklable) object"""
        self._check_closed()
        self._check_readable()
        buf = self._recv_bytes()
        return dill.loads(buf.getbuffer())


class AioPipeReader(AioPipeStream):
    """Reader for a pipe which can attach its file descriptor to an asyncio
    event loop, to enable asynchronous reading from the pipe fd.
    """

    def __init__(self, fd):
        super().__init__(fd)
        self.connection = OwnershiplessConnection(fd, writable=False)

    async def _open(self):
        rx = asyncio.StreamReader(limit=2**32)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(rx), os.fdopen(self._fd)
        )

        return transport, ObjectStreamReader(rx)


class AioPipeWriter(AioPipeStream):
    """Writer for a pipe which can attach its file descriptor to an asyncio
    event loop, to enable asynchronous writing to the pipe fd.
    """

    def __init__(self, fd):
        super().__init__(fd)
        self.connection = OwnershiplessConnection(fd, readable=False)

    async def _open(self):
        rx = asyncio.StreamReader(limit=2**32)
        transport, proto = await asyncio.get_running_loop().connect_write_pipe(
            lambda: asyncio.StreamReaderProtocol(rx), os.fdopen(self._fd, "w")
        )
        tx = asyncio.StreamWriter(transport, proto, rx, asyncio.get_running_loop())

        return transport, ObjectStreamWriter(tx)


class AioDuplex:
    """Combines AioPipeReader and AioPipeWriter in one class. Therfore, 'open'
    will open both streams, and 'detach' will detach both streams/fds.
    """

    def __init__(self, rx: AioPipeReader, tx: AioPipeWriter):
        self._rx = rx
        self._tx = tx

    @contextmanager
    def detach(self) -> ContextManager["AioDuplex"]:
        with self._rx.detach(), self._tx.detach():
            yield self

    @asynccontextmanager
    async def open(
        self,
    ) -> AsyncContextManager[Tuple["asyncio.StreamReader", "asyncio.StreamWriter"]]:
        async with self._rx.open() as rx, self._tx.open() as tx:
            yield rx, tx

    @asynccontextmanager
    async def open_readonly(self) -> AsyncContextManager[Tuple["asyncio.StreamReader"]]:
        async with self._rx.open() as rx:
            yield rx

    @asynccontextmanager
    async def open_writeonly(
        self,
    ) -> AsyncContextManager[Tuple["asyncio.StreamWriter"]]:
        async with self._tx.open() as tx:
            yield tx

    @property
    def read_connection(self) -> Connection:
        return self._rx.connection

    @property
    def write_connection(self) -> Connection:
        return self._tx.connection


class PipeToWriteQueue:
    """Helper class to make a aio pipe imitate the write/put
    part of asyncio.Queue.
    """

    def __init__(self, pipe: AioDuplex) -> None:
        self._pipe = pipe

    def put_nowait(self, object):
        self._pipe.write_connection.send(object)

    def put(self, object):
        self._pipe.write_connection.send(object)
