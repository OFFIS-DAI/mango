import copy
import hashlib
import json
import pickle
import random

import numpy as np
import pytest

import mango
from mango.util.seeding import SeedTree, name_key


def test_name_key_is_stable_64_bit():
    assert name_key("space") == name_key("space")
    assert name_key("space") != name_key("comm")
    assert 0 <= name_key("space") < 2**64
    # pinned value: keys must never change across releases, or replays break
    assert name_key("space") == 0x2B57A37F11A24DFB
    digest = hashlib.blake2b(b"space", digest_size=8, person=b"mango-seedtree")
    assert name_key("space") == int.from_bytes(digest.digest(), "big")


def test_name_key_rejects_non_str():
    with pytest.raises(TypeError):
        name_key(3)


def test_int_seed_identity():
    tree = SeedTree(42)
    assert tree.entropy == 42
    assert tree.spawn_key == ()
    assert tree == SeedTree(42)
    assert hash(tree) == hash(SeedTree(42))
    assert tree != SeedTree(43)
    assert repr(tree) == "SeedTree(42)"


def test_numpy_integer_seed_is_accepted():
    assert SeedTree(np.int64(7)) == SeedTree(7)


@pytest.mark.parametrize("bad", [-1, 1.5, "42", True])
def test_invalid_seed_raises(bad):
    with pytest.raises((ValueError, TypeError)):
        SeedTree(bad)


def test_seed_none_draws_from_global_random():
    random.seed(1)
    first = SeedTree()
    random.seed(1)
    second = SeedTree()
    assert first.entropy == second.entropy
    assert first.entropy >= 0
    assert first.rng("x").random() == second.rng("x").random()


def test_seed_tree_argument_copies_identity_with_empty_cache():
    tree = SeedTree(5).child("a")
    tree_rng = tree.rng("x")
    copied = SeedTree(tree)
    assert copied == tree
    assert copied is not tree
    assert copied.rng("x") is not tree_rng


def test_mapping_seed_uses_from_dict():
    tree = SeedTree(9).child("a", 3)
    assert SeedTree(tree.to_dict()) == tree


def test_spawn_key_with_tree_seed_rejected():
    with pytest.raises(ValueError):
        SeedTree(SeedTree(1), spawn_key=(1,))


def test_child_appends_keys():
    tree = SeedTree(1)
    child = tree.child("agent", "ev_3", 7)
    assert child.spawn_key == (name_key("agent"), name_key("ev_3"), 7)
    assert child.entropy == 1
    assert tree.child("agent/ev_3") == tree.child("agent", "ev_3")


@pytest.mark.parametrize("bad", ["", "a//b", "/a", "a/"])
def test_empty_name_segments_rejected(bad):
    with pytest.raises(ValueError):
        SeedTree(1).rng(bad)


def test_negative_int_segment_rejected():
    with pytest.raises(ValueError):
        SeedTree(1).child(-1)


def test_seed_sequence_and_generator():
    tree = SeedTree(3).child("a")
    ss = tree.seed_sequence()
    assert isinstance(ss, np.random.SeedSequence)
    assert ss.entropy == 3
    assert tuple(ss.spawn_key) == (name_key("a"),)
    g1, g2 = tree.generator(), tree.generator()
    assert isinstance(g1, np.random.Generator)
    assert g1 is not g2
    assert g1.random() == g2.random()


def test_rng_is_cached_per_name():
    tree = SeedTree(3)
    assert tree.rng("space") is tree.rng("space")
    assert tree.rng("space") is not tree.rng("comm")


def test_rng_path_equals_child_stream():
    tree = SeedTree(11)
    expected = tree.child("agent", "ev_3").generator().random(5)
    assert np.array_equal(tree.rng("agent/ev_3").random(5), expected)


def test_different_names_give_different_streams():
    tree = SeedTree(11)
    assert tree.rng("a").random() != tree.rng("b").random()


def test_stream_independent_of_request_order():
    tree_a = SeedTree(21)
    tree_a.rng("other").random(100)
    value_a = tree_a.rng("target").random()
    tree_b = SeedTree(21)
    value_b = tree_b.rng("target").random()
    assert value_a == value_b


def test_same_seed_same_stream_different_seed_different_stream():
    assert SeedTree(1).rng("x").random() == SeedTree(1).rng("x").random()
    assert SeedTree(1).rng("x").random() != SeedTree(2).rng("x").random()


def test_stdlib_random_cached_and_reproducible():
    tree = SeedTree(4)
    r = tree.stdlib_random("dice")
    assert isinstance(r, random.Random)
    assert tree.stdlib_random("dice") is r
    assert SeedTree(4).stdlib_random("dice").random() == r.random()
    assert SeedTree(4).stdlib_random("other").random() != (
        SeedTree(4).stdlib_random("dice").random()
    )


def test_to_dict_is_json_safe_and_round_trips():
    tree = SeedTree(2**100 + 5).child("agent", "x")
    data = tree.to_dict()
    assert data == {"entropy": 2**100 + 5, "spawn_key": list(tree.spawn_key)}
    restored = SeedTree.from_dict(json.loads(json.dumps(data)))
    assert restored == tree
    assert restored.rng("y").random() == tree.rng("y").random()


def test_from_dict_requires_entropy():
    with pytest.raises(ValueError):
        SeedTree.from_dict({"spawn_key": []})


def test_from_dict_without_spawn_key():
    assert SeedTree.from_dict({"entropy": 8}) == SeedTree(8)


def test_pickle_and_copy_drop_cache():
    tree = SeedTree(13).child("w")
    tree.rng("x").random(10)
    restored = pickle.loads(pickle.dumps(tree))
    assert restored == tree
    # the cache is not pickled: the stream restarts from its beginning
    assert restored.rng("x").random() == SeedTree(13).child("w").rng("x").random()
    assert copy.deepcopy(tree) == tree


def test_eq_with_other_types():
    assert SeedTree(1) != 1
    assert SeedTree(1) != {"entropy": 1, "spawn_key": []}


def test_top_level_export():
    assert mango.SeedTree is SeedTree


def test_rng_accepts_several_segments_like_child():
    tree = SeedTree(11)
    assert tree.rng("agent", "ev_3") is tree.rng("agent/ev_3")
    assert tree.rng("agent", name_key("ev_3")) is tree.rng("agent/ev_3")
    single = tree.rng("agent", name_key("g/x"))
    assert single is not tree.rng("agent/g/x")
    assert single.random() == tree.child("agent", name_key("g/x")).generator().random()
    assert tree.stdlib_random("a", "b") is tree.stdlib_random("a/b")
    with pytest.raises(TypeError, match="at least one"):
        tree.rng()
    with pytest.raises(TypeError, match="at least one"):
        tree.stdlib_random()
