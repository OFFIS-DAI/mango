"""
Reproducible random streams keyed by stable names.

A :class:`SeedTree` holds one root seed and hands out independent
``numpy.random.Generator`` streams, one per *name*.  A stream depends only on
the root seed and its name, never on how many other streams exist or in which
order they were requested, so adding an agent to a world never changes the
random numbers another agent draws.

Example::

    from mango import SeedTree

    tree = SeedTree(42)
    space_rng = tree.rng("space")
    ev_rng = tree.rng("agent/ev_3")       # same as tree.child("agent", "ev_3").generator()
    tree.rng("space") is space_rng        # True: one cached stream per name
    replay = SeedTree.from_dict(tree.to_dict())
"""

from __future__ import annotations

import hashlib
import numbers
import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy

__all__ = ["SeedTree", "name_key"]

_PERSON = b"mango-seedtree"


def name_key(name: str) -> int:
    """Return the stable 64-bit key of a single name segment.

    The key is the big-endian integer of an 8-byte BLAKE2b digest of the UTF-8
    encoded name, so it is identical across processes, platforms and Python
    versions (unlike :func:`hash`).

    :param name: the name segment, e.g. ``"space"`` or ``"ev_3"``
    :return: an integer in ``[0, 2**64)``

    Example::

        name_key("space") == name_key("space")  # True, in every process
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be a str, got {type(name).__name__}")
    digest = hashlib.blake2b(name.encode(), digest_size=8, person=_PERSON).digest()
    return int.from_bytes(digest, "big")


def _as_non_negative_int(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{what} must be a non-negative int, got {value!r}")
    value = int(value)
    if value < 0:
        raise ValueError(f"{what} must be >= 0, got {value}")
    return value


def _segment_keys(names: Sequence[str | int]) -> tuple[int, ...]:
    keys: list[int] = []
    for name in names:
        if isinstance(name, str):
            segments = name.split("/")
            if any(segment == "" for segment in segments):
                raise ValueError(
                    f"Invalid stream name {name!r}: segments separated by '/' "
                    "must not be empty."
                )
            keys.extend(name_key(segment) for segment in segments)
        else:
            keys.append(_as_non_negative_int(name, "an integer name segment"))
    return tuple(keys)


def _stream_keys(names: Sequence[str | int]) -> tuple[int, ...]:
    if not names:
        raise TypeError("A stream needs at least one name segment.")
    return _segment_keys(names)


class SeedTree:
    """One root seed that hands out independent random streams keyed by stable names.

    A stream is identified by the root *entropy* plus a *spawn key*: one
    integer per name segment.  ``rng("agent/ev_3")`` therefore yields the same
    stream as ``child("agent", "ev_3").generator()``.  Streams of different
    names are statistically independent (they are derived through
    :class:`numpy.random.SeedSequence`), and a stream never depends on which
    other streams were requested before it.

    :meth:`rng` caches one ``Generator`` per name on this instance, so every
    caller asking for the same name shares (and advances) a single stream.
    :meth:`generator` returns a fresh, uncached generator instead.

    :param seed: ``None`` draws a 128-bit root entropy from the global
        :mod:`random` module (so ``random.seed(x)`` at the top of a script
        still makes the run reproducible; the drawn value is recorded by
        :meth:`to_dict`); an ``int >= 0`` is used as root entropy; a
        :class:`SeedTree` is copied (same identity, empty cache); a mapping is
        read with :meth:`from_dict`
    :param spawn_key: the spawn key of this node (only with an ``int`` or
        ``None`` seed); normally created through :meth:`child`

    Example::

        tree = SeedTree(42)
        rng = tree.rng("comm")
        delay = rng.normal(0.1, 0.01)
        replayed = SeedTree.from_dict(tree.to_dict())
    """

    __slots__ = ("_entropy", "_spawn_key", "_rngs", "_randoms")

    def __init__(
        self,
        seed: int | SeedTree | Mapping | None = None,
        *,
        spawn_key: Sequence[int] = (),
    ):
        if isinstance(seed, SeedTree | Mapping):
            if tuple(spawn_key):
                raise ValueError(
                    "spawn_key can only be given together with an int or None seed."
                )
            source = seed if isinstance(seed, SeedTree) else SeedTree.from_dict(seed)
            entropy, keys = source.entropy, source.spawn_key
        else:
            if seed is None:
                entropy = random.getrandbits(128)
            else:
                entropy = _as_non_negative_int(seed, "seed")
            keys = tuple(_as_non_negative_int(k, "spawn_key entry") for k in spawn_key)
        self._entropy: int = entropy
        self._spawn_key: tuple[int, ...] = keys
        self._rngs: dict[tuple[int, ...], numpy.random.Generator] = {}
        self._randoms: dict[tuple[int, ...], random.Random] = {}

    @property
    def entropy(self) -> int:
        """The root entropy (an ``int >= 0``)."""
        return self._entropy

    @property
    def spawn_key(self) -> tuple[int, ...]:
        """The spawn key of this node: one integer per name segment."""
        return self._spawn_key

    def child(self, *names: str | int) -> SeedTree:
        """Return the sub-tree for *names*.

        Each name segment appends one key to the spawn key; a ``str`` is split
        on ``/`` first (``child("a/b") == child("a", "b")``), an ``int >= 0`` is
        used as-is.  The child is a new instance with an empty cache.

        :param names: name segments
        :return: the child :class:`SeedTree`

        Example::

            ev_tree = tree.child("agent", "ev_3")
            ev_tree.rng("arrival")  # the stream of "agent/ev_3/arrival"
        """
        return SeedTree(self._entropy, spawn_key=self._spawn_key + _segment_keys(names))

    def seed_sequence(self) -> numpy.random.SeedSequence:
        """Return the :class:`numpy.random.SeedSequence` of this node.

        Example::

            seq = SeedTree(42).child("batch").seed_sequence()
            seq.spawn(4)  # e.g. seeds for a pool of worker processes
        """
        import numpy as np

        return np.random.SeedSequence(self._entropy, spawn_key=self._spawn_key)

    def generator(self) -> numpy.random.Generator:
        """Return a fresh, uncached ``Generator(PCG64)`` seeded by this node.

        Every call starts the stream from its beginning.

        Example::

            tree = SeedTree(42).child("agent", "ev_3")
            tree.generator().random() == tree.generator().random()  # True
        """
        import numpy as np

        return np.random.Generator(np.random.PCG64(self.seed_sequence()))

    def rng(self, *names: str | int) -> numpy.random.Generator:
        """Return the stream of *names* (cached: one ``Generator`` per stream).

        The segments are read like :meth:`child`: a ``str`` is split on ``/``,
        an ``int >= 0`` is used as-is, so ``rng("agent/ev_3")``,
        ``rng("agent", "ev_3")`` and ``rng("agent", name_key("ev_3"))`` all
        return the same cached object.  Pass ``name_key(name)`` to use a name
        that contains ``/`` as a single segment.

        :param names: one or more name segments, e.g. ``"space"`` or
            ``"agent/ev_3"``
        :return: the shared ``numpy.random.Generator`` of that stream

        Example::

            tree.rng("space").uniform(0, 10)
            tree.rng("space") is tree.rng("space")  # True
        """
        keys = _stream_keys(names)
        generator = self._rngs.get(keys)
        if generator is None:
            generator = SeedTree(
                self._entropy, spawn_key=self._spawn_key + keys
            ).generator()
            self._rngs[keys] = generator
        return generator

    def stdlib_random(self, *names: str | int) -> random.Random:
        """Return a :class:`random.Random` for *names* (cached per stream).

        For code that needs the standard-library API (``gauss``, ``choice``,
        ``shuffle``); it is seeded from state words of ``child(*names)`` that
        the numpy stream of the same name does not use, so both can be used
        side by side.

        :param names: one or more name segments, read like :meth:`rng`
        :return: the shared :class:`random.Random` of that name

        Example::

            r = tree.stdlib_random("market")
            r.gauss(0.0, 1.0)
            r.choice(["buy", "sell"])
        """
        import numpy as np

        keys = _stream_keys(names)
        instance = self._randoms.get(keys)
        if instance is None:
            words = (
                SeedTree(self._entropy, spawn_key=self._spawn_key + keys)
                .seed_sequence()
                .generate_state(8, dtype=np.uint64)[4:]
            )
            seed = sum(int(word) << (64 * i) for i, word in enumerate(words))
            instance = random.Random(seed)
            self._randoms[keys] = instance
        return instance

    def to_dict(self) -> dict:
        """Return the identity as plain, JSON-safe data.

        :return: ``{"entropy": int, "spawn_key": [int, ...]}``

        Example::

            import json

            text = json.dumps(SeedTree(42).to_dict())
            SeedTree.from_dict(json.loads(text)) == SeedTree(42)  # True
        """
        return {"entropy": self._entropy, "spawn_key": list(self._spawn_key)}

    @classmethod
    def from_dict(cls, data: Mapping) -> SeedTree:
        """Rebuild a tree from :meth:`to_dict` output.

        :param data: mapping with ``entropy`` and optionally ``spawn_key``
        :return: a :class:`SeedTree` with that identity

        Example::

            tree = SeedTree.from_dict({"entropy": 42, "spawn_key": []})
            tree == SeedTree(42)  # True
        """
        if "entropy" not in data:
            raise ValueError(
                f"A seed mapping needs an 'entropy' entry, got keys {sorted(data)}."
            )
        return cls(data["entropy"], spawn_key=tuple(data.get("spawn_key", ())))

    def __getstate__(self):
        return {"entropy": self._entropy, "spawn_key": self._spawn_key}

    def __setstate__(self, state):
        self._entropy = state["entropy"]
        self._spawn_key = tuple(state["spawn_key"])
        self._rngs = {}
        self._randoms = {}

    def __eq__(self, other) -> bool:
        if not isinstance(other, SeedTree):
            return NotImplemented
        return (self._entropy, self._spawn_key) == (other._entropy, other._spawn_key)

    def __hash__(self) -> int:
        return hash((self._entropy, self._spawn_key))

    def __repr__(self) -> str:
        if self._spawn_key:
            return f"SeedTree({self._entropy}, spawn_key={self._spawn_key})"
        return f"SeedTree({self._entropy})"
