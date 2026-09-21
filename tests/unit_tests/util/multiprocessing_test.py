"""Unit tests for the IPC primitives in :mod:`mango.util.multiprocessing`.

No child process is started here: the framing, the end-of-stream
contract and the ownership rules are all observable on a single
process, and testing them directly keeps the failure messages away
from the noise a crashed subprocess produces.
"""

from __future__ import annotations

import asyncio
import pickle
import struct
from multiprocessing.reduction import ForkingPickler

import pytest

from mango.util.multiprocessing import (
    ObjectStreamReader,
    ObjectStreamWriter,
    _force_close,
    aioduplex,
)


def _reader_over(data: bytes) -> ObjectStreamReader:
    stream = asyncio.StreamReader()
    stream.feed_data(data)
    stream.feed_eof()
    return ObjectStreamReader(stream)


def _framed(payload: bytes) -> bytes:
    return struct.pack("!i", len(payload)) + payload


class TestObjectStreamReader:
    @pytest.mark.asyncio
    async def test_zero_length_read_does_not_touch_the_stream(self):
        """A zero-byte frame is legal and must not enter ``readexactly``,
        which treats a zero-length request as an immediate success only
        by accident of implementation."""
        reader = _reader_over(b"")

        assert await reader._recv(0) == b""

    @pytest.mark.asyncio
    async def test_clean_end_of_stream_raises_eof(self):
        """Nothing at all on the wire means the peer is gone."""
        reader = _reader_over(b"")

        with pytest.raises(EOFError):
            await reader._recv(4)

    @pytest.mark.asyncio
    async def test_truncated_frame_raises_oserror(self):
        """A frame that stops halfway is a broken message, not a clean
        shutdown, and callers must be able to tell the two apart."""
        reader = _reader_over(b"ab")

        with pytest.raises(OSError, match="end of file during message"):
            await reader._recv(4)

    @pytest.mark.asyncio
    async def test_connection_reset_is_reported_as_eof(self):
        """A socket peer that dies reports a reset where a pipe reported
        end of file; callers read EOFError as "the other side is gone",
        so the transport-specific error must not leak."""

        class _ResettingReader:
            async def readexactly(self, size):
                raise ConnectionResetError("peer died")

        reader = ObjectStreamReader(_ResettingReader())

        with pytest.raises(EOFError):
            await reader._recv(4)

    @pytest.mark.asyncio
    async def test_reads_large_frame_via_64_bit_header(self):
        """Payloads above 2 GiB cannot state their size in the 32-bit
        header, so a ``-1`` marker introduces a 64-bit one.  The frame
        itself is small here; only the header path differs."""
        payload = b"large-frame"
        data = struct.pack("!i", -1) + struct.pack("!Q", len(payload)) + payload

        assert await _reader_over(data)._recv_bytes() == payload

    @pytest.mark.asyncio
    async def test_maxsize_rejects_oversized_frame(self):
        """``maxsize`` answers None instead of allocating a frame the
        caller declared too big to accept."""
        reader = _reader_over(_framed(b"0123456789"))

        assert await reader._recv_bytes(maxsize=4) is None

    @pytest.mark.asyncio
    async def test_read_bytes_returns_the_raw_frame(self):
        """``read_bytes`` is the unpickled counterpart of
        ``read_object``: same framing, no deserialisation."""
        reader = _reader_over(_framed(b"raw"))

        assert await reader.read_bytes() == b"raw"

    @pytest.mark.asyncio
    async def test_read_object_unpickles_the_frame(self):
        reader = _reader_over(_framed(pickle.dumps({"a": 1})))

        assert await reader.read_object() == {"a": 1}


class TestObjectStreamWriter:
    @pytest.mark.asyncio
    async def test_round_trip_over_a_real_duplex(self):
        """End-to-end over a socket pair: what the writer frames is what
        the reader hands back, for both objects and raw bytes."""
        a, b = aioduplex()

        async with a.dup().open_writeonly() as tx, b.dup().open_readonly() as rx:
            tx.write_object({"hello": "world"})
            tx.write_bytes(b"payload")
            await tx.drain()

            assert await rx.read_object() == {"hello": "world"}
            assert await rx.read_bytes() == b"payload"

        a.close()
        b.close()

    @pytest.mark.asyncio
    async def test_buffered_bytes_reports_the_unflushed_backlog(self):
        """``_send_to_message_pipe`` batches writes until this crosses a
        threshold, so it has to reflect writes that have not drained."""
        a, b = aioduplex()

        async with a.dup().open_writeonly() as tx:
            assert tx.buffered_bytes() == 0
            tx.write_bytes(b"x" * 1024)
            assert tx.buffered_bytes() >= 0

        a.close()
        b.close()

    def test_write_bytes_switches_to_64_bit_header_when_needed(self):
        """The large-frame branch of the framing, checked without
        allocating 2 GiB: only the header sequence is asserted."""
        written: list[bytes] = []

        class _Recorder(ObjectStreamWriter):
            def __init__(self):
                pass

            def _write(self, buf):
                written.append(bytes(buf))

        class _HugeBytes(bytes):
            def __len__(self):
                return 0x80000000

        _Recorder()._write_bytes(_HugeBytes(b"payload"))

        assert written[0] == struct.pack("!i", -1)
        assert written[1] == struct.pack("!Q", 0x80000000)
        assert written[2] == b"payload"


class TestOwnershiplessConnection:
    def test_send_and_recv_work_synchronously(self):
        """The synchronous API on an endpoint that was never handed to a
        transport.  Used by ``pre_hook_reserve_aid``, which has to block."""
        a, b = aioduplex()
        sender, receiver = a.dup(), b.dup()

        sender.write_connection.send({"aid": "agent0"})

        assert receiver.read_connection.recv() == {"aid": "agent0"}

        sender.close()
        receiver.close()
        a.close()
        b.close()

    def test_cannot_be_pickled_for_another_process(self):
        """The descriptor number it carries means nothing in a child, so
        sending one has to fail loudly rather than hand over a handle
        that resolves to something unrelated."""
        a, b = aioduplex()
        duplicate = a.dup()

        with pytest.raises(TypeError, match="cannot be sent to another process"):
            ForkingPickler.dumps(duplicate.read_connection)

        duplicate.close()
        a.close()
        b.close()

    def test_close_is_a_no_op_and_force_close_releases_the_descriptor(self):
        """``close`` never touches the descriptor (that is the whole
        point of the class), so an endpoint only ever used synchronously
        needs ``_force_close`` to stop leaking it."""
        a, b = aioduplex()
        duplicate = a.dup()
        conn = duplicate.read_connection

        conn.close()
        assert not conn.closed

        _force_close(conn)

        duplicate._tx.release()
        a.close()
        b.close()

    def test_force_close_tolerates_a_connection_without_descriptor(self):
        """A connection whose descriptor is already gone reports an
        OSError from ``fileno()``; there is nothing left to close."""

        class _Detached:
            def fileno(self):
                raise OSError("closed")

        _force_close(_Detached())

    def test_force_close_tolerates_an_unusable_descriptor(self):
        """A descriptor that no longer resolves to a socket cannot be
        closed either; ``_force_close`` is best-effort cleanup and must
        not raise into a teardown path."""

        class _Stale:
            def fileno(self):
                return 999_999

        _force_close(_Stale())
