"""
Utility classes for handling multiprocessing in mango, especially focusing on IPC in an asyncio context.

The package contains two different variants of async pipes for IPC: duplex, and non-duplex pipes. For creating
these pipes, use aiopipe() or aioduplex(). The idea of the code is based on the pypi package 'aiopipe'.

The endpoints are backed by a connected socket pair rather than an anonymous
OS pipe.  A socket pair behaves the same on POSIX and on Windows, carries the
same length-prefixed framing as :class:`multiprocessing.connection.Connection`,
and is handed to a child process by :mod:`multiprocessing` itself, which
duplicates the underlying descriptor or handle for the target platform.

One endpoint must be used through *either* the asynchronous or the synchronous
API, never both: opening it hands its descriptor to an asyncio transport, which
puts the descriptor into non-blocking mode and then owns it.  A synchronous
:class:`~multiprocessing.connection.Connection` call on the same endpoint
afterwards raises :class:`BlockingIOError`, and on the read side competes with
the transport for the same bytes.  Call :meth:`AioDuplex.dup` to get an
independent endpoint when both styles are needed.

These pipes provide async compatible APIs, here a general example:

.. code-block:: python

    main, sub = aioduplex()
    with sub.detach() as sub:
        # start your process with sub as inherited pipe
    # open() hands the descriptors to asyncio, so open a dup, not the original
    async with main.dup().open() as (rx, tx):
        item = await rx.read_object()
        tx.write_object()
        ...

Further there are internal connection objects, which can be used if a synchronous access outside of the
asyncio loop is necessary: 'main.write_connection, main.read_connection'. Note, that you can't use
'write_connection' if the pipe has been opened with 'open()', as this will lock the write access to the pipe.
For that case you could use 'open_readonly()',
"""

import asyncio
import os
import pickle
import socket
import struct
import sys
from contextlib import asynccontextmanager, contextmanager
from multiprocessing import reduction
from multiprocessing.connection import Connection
from multiprocessing.reduction import ForkingPickler


def _loads(buf):
    """Deserialise a frame from this channel.

    Every writer here dumps through :class:`ForkingPickler`, which subclasses the
    C pickler and so emits a plain pickle stream; dill's import-time extensions
    patch only the pure-Python pickler and never affect it. Reading with dill
    instead would cost several times as much on the small payloads that dominate
    and could not decode anything extra.
    """
    return pickle.loads(buf)


def _socket_from_descriptor(fd: int) -> socket.socket:
    """Rebuild a socket object from a bare descriptor.

    The family is left to be read back off the descriptor: socketpair() answers
    with AF_UNIX where that exists and emulates the pair over a loopback AF_INET
    connection on Windows, and naming the wrong family here would go unnoticed
    rather than raise.
    """
    return socket.socket(fileno=fd)


def _force_close(conn: Connection) -> None:
    """Close the descriptor behind *conn*, even if *conn* disclaims ownership.

    :class:`OwnershiplessConnection` never closes anything, on the assumption
    that an asyncio transport took the descriptor over. An endpoint that is only
    ever used synchronously has no such transport, so without this it would stay
    open until the process ends.
    """
    try:
        fd = conn.fileno()
    except OSError:
        return
    try:
        _socket_from_descriptor(fd).close()
    except OSError:
        pass


def _duplicate_handle(conn: Connection) -> int:
    """Duplicate the descriptor behind *conn* and return the copy.

    The caller owns the result.  ``os.dup`` cannot copy a Windows socket
    handle, so the socket API is used to duplicate it there.
    """
    if sys.platform == "win32":
        borrowed = _socket_from_descriptor(conn.fileno())
        try:
            duplicate = borrowed.dup()
        finally:
            # Hand the original handle back; ``borrowed`` must not close it.
            borrowed.detach()
        return duplicate.detach()
    return os.dup(conn.fileno())


def _tune(sock: socket.socket) -> socket.socket:
    """Disable Nagle where the pair is emulated over loopback TCP.

    Every write here is a whole message that the peer is waiting for, so holding
    a small one back to coalesce it with the next only adds delay.
    """
    if sock.family == socket.AF_INET:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    return sock


def _socket_for(conn: Connection) -> socket.socket:
    """Wrap *conn*'s descriptor in a socket object, which takes ownership.

    Only called for connections that do not own their descriptor (see
    :class:`OwnershiplessConnection`), so that closing the asyncio transport
    is the single close of that descriptor.
    """
    return _tune(_socket_from_descriptor(conn.fileno()))


def aiopipe(ctx=None) -> tuple["AioPipeReader", "AioPipeWriter"]:
    """Create a pair of connected endpoints, one read-, one write-side.

    :return: Reader-, Writer-Pair
    """
    rx, tx = socket.socketpair()
    for sock in (rx, tx):
        sock.setblocking(True)
        _tune(sock)
    return AioPipeReader(Connection(rx.detach())), AioPipeWriter(
        Connection(tx.detach())
    )


def aioduplex(ctx=None) -> tuple["AioDuplex", "AioDuplex"]:
    """Create a pair of pipe endpoints, both readable and writable (duplex).

    :return: AioDuplex-Pair
    """
    rxa, txa = aiopipe(ctx)
    rxb, txb = aiopipe(ctx)

    return AioDuplex(rxa, txb), AioDuplex(rxb, txa)


class AioPipeStream:
    """Stream-like wrapper for one endpoint, implementing 'async with open' and 'with detach'"""

    def __init__(self, conn: Connection):
        self.connection = conn
        self._closed = False
        self._adopted = False

    @asynccontextmanager
    async def open(self):
        assert not self._closed
        writer, stream = await self._open()
        try:
            yield stream
        finally:
            try:
                writer.close()
            except OSError:
                pass

            await asyncio.sleep(0)

    async def _open(self) -> tuple[asyncio.StreamWriter, object]:
        raise NotImplementedError()

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        sock = _socket_for(self.connection)
        self._adopted = True
        return await asyncio.open_connection(sock=sock, limit=2**32)

    def release(self) -> None:
        """Give up the endpoint's descriptor.

        A descriptor handed to a transport is closed by that transport; one that
        was only used synchronously has to be closed here.
        """
        if self._adopted:
            self._closed = True
            return
        if isinstance(self.connection, OwnershiplessConnection):
            _force_close(self.connection)
        else:
            self.connection.close()
        self._closed = True

    @contextmanager
    def detach(self):
        """Hand the endpoint to the child process.

        :mod:`multiprocessing` duplicates the descriptor into the child while
        the process starts, inside this block. Closing this side on the way out
        is what lets the child's reads see end of file once the child exits;
        leaving it to garbage collection makes that dependent on no stray
        reference surviving anywhere.
        """
        try:
            yield self
        finally:
            self.close()

    def _close(self):
        self._closed = True
        self.connection.close()

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

    async def _recv(self, size: int) -> bytes:
        # readexactly does the accumulating inside the stream's own buffer; the
        # hand-written read loop it replaces re-entered the reader once per
        # chunk and copied every chunk into a BytesIO on the way.
        if size == 0:
            return b""
        try:
            return await self._stream_reader.readexactly(size)
        except asyncio.IncompleteReadError as e:
            if not e.partial:
                raise EOFError from None
            raise OSError("got end of file during message") from None
        except (ConnectionResetError, BrokenPipeError) as e:
            # A socket peer that dies reports a reset where an anonymous pipe
            # reported a clean end of file. Callers read EOFError as "the other
            # side is gone", so keep that contract instead of leaking an error
            # that only this transport can produce.
            raise EOFError from e

    async def _recv_bytes(self, maxsize=None) -> bytes | None:
        (size,) = struct.unpack("!i", await self._recv(4))
        if size == -1:
            (size,) = struct.unpack("!Q", await self._recv(8))
        if maxsize is not None and size > maxsize:
            return None
        return await self._recv(size)

    async def read_object(self):
        buf = await self._recv_bytes()
        if buf is None:
            raise OSError("bad message length")
        return _loads(buf)

    async def read_bytes(self) -> bytes:
        return await self._recv_bytes()


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

    def buffered_bytes(self) -> int:
        """Bytes written but not yet handed to the OS."""
        return self._stream_writer.transport.get_write_buffer_size()

    async def drain(self):
        await self._stream_writer.drain()


class OwnershiplessConnection(Connection):
    """Subclass of the mp Connection, which marks it as ownershipless. Following
    this class won't close the descriptor under any circumstance on its own.

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
        return _loads(buf.getbuffer())


def _reject_ownershipless(conn):
    raise TypeError(
        "an OwnershiplessConnection cannot be sent to another process: it "
        "carries a descriptor number that means nothing there. Send the "
        "AioDuplex it came from before duplicating it instead."
    )


reduction.register(OwnershiplessConnection, _reject_ownershipless)


class AioPipeReader(AioPipeStream):
    """Reader which attaches its endpoint to an asyncio event loop, to enable
    asynchronous reading.
    """

    async def _open(self):
        reader, writer = await self._connect()
        return writer, ObjectStreamReader(reader)


class AioPipeWriter(AioPipeStream):
    """Writer which attaches its endpoint to an asyncio event loop, to enable
    asynchronous writing.
    """

    async def _open(self):
        _, writer = await self._connect()
        return writer, ObjectStreamWriter(writer)


class AioDuplex:
    """Combines AioPipeReader and AioPipeWriter in one class. Therfore, 'open'
    will open both streams, and 'detach' will detach both endpoints.
    """

    def __init__(self, rx: AioPipeReader, tx: AioPipeWriter):
        self._rx = rx
        self._tx = tx

    def dup(self):
        rf = _duplicate_handle(self._rx.connection)
        wf = _duplicate_handle(self._tx.connection)
        return AioDuplex(
            AioPipeReader(OwnershiplessConnection(rf)),
            AioPipeWriter(OwnershiplessConnection(wf)),
        )

    def close(self):
        self._rx.release()
        self._tx.release()

    @contextmanager
    def detach(self):
        with self._rx.detach(), self._tx.detach():
            yield self

    @asynccontextmanager
    async def open(
        self,
    ):
        async with self._rx.open() as rx, self._tx.open() as tx:
            yield rx, tx

    @asynccontextmanager
    async def open_readonly(self):
        async with self._rx.open() as rx:
            yield rx

    @asynccontextmanager
    async def open_writeonly(
        self,
    ):
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
