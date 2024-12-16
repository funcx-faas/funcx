import inspect
import random
import sys
from unittest.mock import patch

import globus_compute_sdk.serialize.concretes as concretes
import globus_compute_sdk.serialize.facade as facade
import pytest
from globus_compute_sdk.errors import (
    DeserializationError,
    SerdeError,
    SerializationError,
)
from globus_compute_sdk.serialize.base import SerializationStrategy
from globus_compute_sdk.serialize.facade import ComputeSerializer

# length of serializer identifier
ID_LEN = 3


def foo(x, y=3):
    return x * y


def double_first_argument(func):
    """
    Test decorator that will double the first argument if third is True
    """

    def wrapper_double_first_argument(a, b, c):
        return func(a * 2, b, c) if c else func(a, b, c)

    return wrapper_double_first_argument


@double_first_argument
def decorated_add(a, b, c):
    """
    Third argument is ignored, only for decorator usage
    """
    return a + b


def check_deserialized_foo(f):
    p1, p2 = inspect.signature(f).parameters.items()

    param_1: inspect.Parameter = p1[1]
    param_2: inspect.Parameter = p2[1]

    assert isinstance(param_1, inspect.Parameter)
    assert isinstance(param_2, inspect.Parameter)
    assert "x" == param_1.name
    assert "y" == param_2.name
    assert 3 == param_2.default


def check_deserialized_bar(f):
    p1, p2 = inspect.signature(f).parameters.items()

    param_1: inspect.Parameter = p1[1]
    param_2: inspect.Parameter = p2[1]

    assert isinstance(param_1, inspect.Parameter)
    assert isinstance(param_2, inspect.Parameter)
    assert "a" == param_1.name
    assert "b" == param_2.name
    assert 5 == param_2.default


def check_serialize_deserialize_foo(concrete_cls, pre_serialized=None):
    serialized_foo = concrete_cls.serialize(foo)
    deserialized_foo = concrete_cls.deserialize(serialized_foo)
    check_deserialized_foo(deserialized_foo)

    if pre_serialized:
        deserialized_foo = concrete_cls.deserialize(pre_serialized)
        check_deserialized_foo(deserialized_foo)


def check_serialize_deserialize_bar(concrete_cls, pre_serialized=None):
    def inner_bar(a, b=5):
        return a * 5

    serialized_bar = concrete_cls.serialize(inner_bar)
    deserialized_bar = concrete_cls.deserialize(serialized_bar)
    check_deserialized_bar(deserialized_bar)

    if pre_serialized:
        deserialized_bar = concrete_cls.deserialize(pre_serialized)
        check_deserialized_bar(deserialized_bar)


def check_serialize_deserialize_add(concrete_cls, pre_serialized=None):
    serialized_add = concrete_cls.serialize(decorated_add)
    deserialized_add = concrete_cls.deserialize(serialized_add)
    assert 10 == deserialized_add(3, 4, True)
    assert 7 == deserialized_add(3, 4, False)

    if pre_serialized:
        deserialized_add = concrete_cls.deserialize(pre_serialized)
        assert 10 == deserialized_add(3, 4, True)
        assert 7 == deserialized_add(3, 4, False)


def test_base64_data():
    jb = concretes.DillDataBase64()

    d = jb.serialize(([2], {"y": 10}))
    assert "00" == d.split("\n")[0]
    args, kwargs = jb.deserialize(d)
    assert args[0] == 2
    assert kwargs["y"] == 10

    # Depends on running this test in non 3.7 and passing
    py37_serial_data = "00\ngANdcQBLAmF9cQFYAQAAAHlxAksKc4ZxAy4=\n"

    args, kwargs = jb.deserialize(py37_serial_data)
    assert args[0] == 2
    assert kwargs["y"] == 10


def test_pickle_deserialize():
    jb = concretes.PickleCode()

    with pytest.raises(NotImplementedError):
        jb.serialize(foo)

    # Serialized via 3.7, should work in other versions
    serialized_foo_pickle = "02\ngANjdGVzdF9zZXJpYWxpemF0aW9uCmZvbwpxAC4="

    deserialized_foo = jb.deserialize(serialized_foo_pickle)
    check_deserialized_foo(deserialized_foo)


@pytest.mark.skipif(
    not ((3, 7) < sys.version_info < (3, 8) and (3, 9) < sys.version_info < (3, 11)),
    reason=(
        "mismatched python version serialization is suspect, and currently fails"
        " on 3.8 and 3.11"
    ),
)
def test_code_dill():
    """
    This is our default primary path going forward, and it should handle
    the most methods.
    """
    pre_serialized_foo_37 = "01\ngANjdGVzdF9zZXJpYWxpemF0aW9uCmZvbwpxAC4=\n"
    check_serialize_deserialize_foo(concretes.DillCode(), pre_serialized_foo_37)
    pre_serialized_bar_37 = (
        "01\ngANjZGlsbC5fZGlsbApfY3JlYXRlX2Z1bmN0aW9uCnEAKGNkaWxsLl9kaWxsCl9"
        "jcmVhdGVfY29kZQpxAShLAksASwJLAktTQwh8AGQBFABTAHECTksFhnEDKVgBAAAAYX"
        "EEWAEAAABicQWGcQZYRwAAAC9Vc2Vycy9sZWkvZ2xvYi9mdW5jWC9mdW5jeF9zZGsvd"
        "GVzdHMvaW50ZWdyYXRpb24vdGVzdF9zZXJpYWxpemF0aW9uLnB5cQdYCQAAAGlubmVy"
        "X2JhcnEISzhDAgABcQkpKXRxClJxC2N0ZXN0X3NlcmlhbGl6YXRpb24KX19kaWN0X18"
        "KaAhLBYVxDE50cQ1ScQ59cQ99cRAoWA8AAABfX2Fubm90YXRpb25zX19xEX1xElgMAA"
        "AAX19xdWFsbmFtZV9fcRNYMgAAAGNoZWNrX3NlcmlhbGl6ZV9kZXNlcmlhbGl6ZV9iY"
        "XIuPGxvY2Fscz4uaW5uZXJfYmFycRR1hnEVYi4=\n"
    )
    check_serialize_deserialize_bar(
        concretes.DillCode(), pre_serialized=pre_serialized_bar_37
    )
    pre_serialized_add_37 = (
        "01\n"
        "gANjZGlsbC5fZGlsbApfY3JlYXRlX2Z1bmN0aW9uCnEAKGNkaWxsLl9kaWxsCl9jcmVhdGVfY29k\n"
        "ZQpxAShLA0sASwNLBEsTQyB8AnIUiAB8AGQBFAB8AXwCgwNTAIgAfAB8AXwCgwNTAHECTksChnED\n"
        "KVgBAAAAYXEEWAEAAABicQVYAQAAAGNxBodxB1hHAAAAL1VzZXJzL2xlaS9nbG9iL2Z1bmNYL2Z1\n"
        "bmN4X3Nkay90ZXN0cy9pbnRlZ3JhdGlvbi90ZXN0X3NlcmlhbGl6YXRpb24ucHlxCFgdAAAAd3Jh\n"
        "cHBlcl9kb3VibGVfZmlyc3RfYXJndW1lbnRxCUsNQwIAAXEKWAQAAABmdW5jcQuFcQwpdHENUnEO\n"
        "Y3Rlc3Rfc2VyaWFsaXphdGlvbgpfX2RpY3RfXwpoCU5jZGlsbC5fZGlsbApfY3JlYXRlX2NlbGwK\n"
        "cQ9OhXEQUnERhXESdHETUnEUfXEVfXEWKFgPAAAAX19hbm5vdGF0aW9uc19fcRd9cRhYDAAAAF9f\n"
        "cXVhbG5hbWVfX3EZWDwAAABkb3VibGVfZmlyc3RfYXJndW1lbnQuPGxvY2Fscz4ud3JhcHBlcl9k\n"
        "b3VibGVfZmlyc3RfYXJndW1lbnRxGnWGcRtiY2J1aWx0aW5zCmdldGF0dHIKcRxjZGlsbApfZGls\n"
        "bApxHVgIAAAAX3NldGF0dHJxHmNidWlsdGlucwpzZXRhdHRyCnEfh3EgUnEhaBFYDQAAAGNlbGxf\n"
        "Y29udGVudHNxImgAKGgBKEsDSwBLA0sCS0NDCHwAfAEXAFMAcSNYPQAAAAogICAgVGhpcmQgYXJn\n"
        "dW1lbnQgaXMgaWdub3JlZCwgb25seSBmb3IgZGVjb3JhdG9yIHVzYWdlCiAgICBxJIVxJSloBGgF\n"
        "aAaHcSZoCFgNAAAAZGVjb3JhdGVkX2FkZHEnSxJDAgAFcSgpKXRxKVJxKmN0ZXN0X3NlcmlhbGl6\n"
        "YXRpb24KX19kaWN0X18KaCdOTnRxK1JxLH1xLX1xLihYBwAAAF9fZG9jX19xL2gkaBd9cTB1hnEx\n"
        "YodxMlIwLg==\n"
    )
    pre_serialized_add_310 = (
        "01\n"
        "gASVxAIAAAAAAACMCmRpbGwuX2RpbGyUjBBfY3JlYXRlX2Z1bmN0aW9ulJOUKGgAjAxfY3JlYXRl\n"
        "X2NvZGWUk5QoSwNLAEsASwNLBEsTQyB8AnIKiAB8AGQBFAB8AXwCgwNTAIgAfAB8AXwCgwNTAJRO\n"
        "SwKGlCmMAWGUjAFilIwBY5SHlIxHL1VzZXJzL2xlaS9nbG9iL2Z1bmNYL2Z1bmN4X3Nkay90ZXN0\n"
        "cy9pbnRlZ3JhdGlvbi90ZXN0X3NlcmlhbGl6YXRpb24ucHmUjB13cmFwcGVyX2RvdWJsZV9maXJz\n"
        "dF9hcmd1bWVudJRLEkMCAAGUjARmdW5jlIWUKXSUUpRjdGVzdF9zZXJpYWxpemF0aW9uCl9fZGlj\n"
        "dF9fCmgMTmgAjAxfY3JlYXRlX2NlbGyUk5ROhZRSlIWUdJRSlH2UfZQojA9fX2Fubm90YXRpb25z\n"
        "X1+UfZSMDF9fcXVhbG5hbWVfX5SMPGRvdWJsZV9maXJzdF9hcmd1bWVudC48bG9jYWxzPi53cmFw\n"
        "cGVyX2RvdWJsZV9maXJzdF9hcmd1bWVudJR1hpRijAhidWlsdGluc5SMB2dldGF0dHKUk5SMC2Rp\n"
        "bGwuX3NoaW1zlIwFX2RpbGyUk5SMCF9zZXRhdHRylGggjAdzZXRhdHRylJOUh5RSlGgVjA1jZWxs\n"
        "X2NvbnRlbnRzlGgCKGgEKEsDSwBLAEsDSwJLQ0MIfAB8ARcAUwCUjD0KICAgIFRoaXJkIGFyZ3Vt\n"
        "ZW50IGlzIGlnbm9yZWQsIG9ubHkgZm9yIGRlY29yYXRvciB1c2FnZQogICAglIWUKWgKaAuMDWRl\n"
        "Y29yYXRlZF9hZGSUSxhDAgAFlCkpdJRSlGN0ZXN0X3NlcmlhbGl6YXRpb24KX19kaWN0X18KaC9O\n"
        "TnSUUpR9lH2UKIwHX19kb2NfX5RoLWgbfZR1hpRih5RSMC4=\n"
    )

    pre_serialized_add_39 = (
        "01\n"
        "gASVxAIAAAAAAACMCmRpbGwuX2RpbGyUjBBfY3JlYXRlX2Z1bmN0aW9ulJOUKGgAjAxfY3JlYXRl\n"
        "X2NvZGWUk5QoSwNLAEsASwNLBEsTQyB8AnIUiAB8AGQBFAB8AXwCgwNTAIgAfAB8AXwCgwNTAJRO\n"
        "SwKGlCmMAWGUjAFilIwBY5SHlIxHL1VzZXJzL2xlaS9nbG9iL2Z1bmNYL2Z1bmN4X3Nkay90ZXN0\n"
        "cy9pbnRlZ3JhdGlvbi90ZXN0X3NlcmlhbGl6YXRpb24ucHmUjB13cmFwcGVyX2RvdWJsZV9maXJz\n"
        "dF9hcmd1bWVudJRLEkMCAAGUjARmdW5jlIWUKXSUUpRjdGVzdF9zZXJpYWxpemF0aW9uCl9fZGlj\n"
        "dF9fCmgMTmgAjAxfY3JlYXRlX2NlbGyUk5ROhZRSlIWUdJRSlH2UfZQojA9fX2Fubm90YXRpb25z\n"
        "X1+UfZSMDF9fcXVhbG5hbWVfX5SMPGRvdWJsZV9maXJzdF9hcmd1bWVudC48bG9jYWxzPi53cmFw\n"
        "cGVyX2RvdWJsZV9maXJzdF9hcmd1bWVudJR1hpRijAhidWlsdGluc5SMB2dldGF0dHKUk5SMC2Rp\n"
        "bGwuX3NoaW1zlIwFX2RpbGyUk5SMCF9zZXRhdHRylGggjAdzZXRhdHRylJOUh5RSlGgVjA1jZWxs\n"
        "X2NvbnRlbnRzlGgCKGgEKEsDSwBLAEsDSwJLQ0MIfAB8ARcAUwCUjD0KICAgIFRoaXJkIGFyZ3Vt\n"
        "ZW50IGlzIGlnbm9yZWQsIG9ubHkgZm9yIGRlY29yYXRvciB1c2FnZQogICAglIWUKWgKaAuMDWRl\n"
        "Y29yYXRlZF9hZGSUSxhDAgAFlCkpdJRSlGN0ZXN0X3NlcmlhbGl6YXRpb24KX19kaWN0X18KaC9O\n"
        "TnSUUpR9lH2UKIwHX19kb2NfX5RoLWgbfZR1hpRih5RSMC4=\n"
    )

    pre_serialized = pre_serialized_add_37
    mismatch_serialized = pre_serialized_add_39

    # dill.dumps()/loads() does not deserialize functions serialized with some
    #     other python versions.  Some info:
    #     https://github.com/cloudpipe/cloudpickle/issues/293
    # TODO figure out a better way to deserialize mismatched versions
    #     or possibly give up on trying to deserialize mismatched versions
    # For now, just test pre-generated strings for 3.7, 3.9, and 3.10, and
    #     force version upgrades to fail in tox until a known string is
    #     generated in check_serialize_deserialize_add() above
    major = sys.version_info.major
    minor = sys.version_info.minor
    if major == 3 and minor == 9:
        pre_serialized = pre_serialized_add_39
        mismatch_serialized = pre_serialized_add_37
    elif major == 3 and minor == 10:
        pre_serialized = pre_serialized_add_310
    elif major == 3 and minor > 10:
        # Failing probably better than forgetting about version mismatches
        # in the future
        pytest.fail(f"Pre-serialized string needed for py{major}.{minor}")

    check_serialize_deserialize_add(concretes.DillCode(), pre_serialized)

    # dill code doesn't handle mismatched py versions
    deserialized_add = concretes.DillCode().deserialize(mismatch_serialized)
    assert 10 == deserialized_add(3, 4, True)


def test_code_text_inspect():
    check_serialize_deserialize_foo(concretes.DillCodeTextInspect())


def test_code_dill_source():
    check_serialize_deserialize_foo(concretes.DillCodeSource())

    # dill code source doesn't handle the indents of bar
    check_serialize_deserialize_bar(concretes.DillCodeSource())


def test_overall():
    check_serialize_deserialize_foo(ComputeSerializer())
    check_serialize_deserialize_bar(ComputeSerializer())


def test_combined_serialize_fail():
    single_source = concretes.DillCodeSource()

    # built-in functions do not serialize with most strategies
    with pytest.raises(TypeError):
        single_source.serialize(max)

    combined = concretes.CombinedCode()
    combined_serialized_func = combined.serialize(max)
    deserialized = combined.deserialize(combined_serialized_func)

    assert deserialized(1, 4) == 4


@patch(
    "globus_compute_sdk.serialize.concretes.DillCode.serialize",
    side_effect=Exception("block this"),
)
def test_combined_serialize_all_fail(code_mock):
    # max() only works with DillCode.  Disabling that should trigger
    # a failure when no methods work
    combined = concretes.CombinedCode()
    with pytest.raises(SerializationError) as exc_info:
        combined.serialize(max)
    assert "No serialization methods were successful" in str(exc_info.value)


def test_serialize_deserialize_combined():
    f = decorated_add
    combined = concretes.CombinedCode()
    single_source = concretes.DillCodeSource()
    single_code = concretes.DillCode()
    combined_serialized_func = combined.serialize(f)
    source_serialized_func = single_source.serialize(f)
    code_serialized_func = single_code.serialize(f)

    # Deserializing decorated functions does not raise exception,
    # only when one attempts to run it
    # TODO find example that fails on Category 2 (deserial) for DillCodeSource
    # the new default
    # with pytest.raises(Exception) as e:
    deserial_source = single_source.deserialize(source_serialized_func)

    # Only when attempting to run it
    with pytest.raises(NameError):
        assert 11 == deserial_source(3, 5, 2)
        # pass

    code_deserial = single_code.deserialize(code_serialized_func)
    assert 14 == code_deserial(4, 6, 3)

    # assumes DillCodeSource is first strategy that CombinedSerializer tries
    deserialized = combined.deserialize(combined_serialized_func)
    with pytest.raises(NameError):
        _ = deserialized(3, 5, 2)

    alternate_deserialized = combined.deserialize(combined_serialized_func, variation=2)
    assert alternate_deserialized != deserialized


def test_compute_serializer_defaults():
    serializer = ComputeSerializer()

    assert (
        serializer.serialize("something non-callable")[:ID_LEN]
        == concretes.DEFAULT_STRATEGY_DATA.identifier
    )

    assert (
        serializer.serialize(foo)[:ID_LEN] == concretes.DEFAULT_STRATEGY_CODE.identifier
    )


@pytest.mark.parametrize("strategy", concretes.SELECTABLE_STRATEGIES)
def test_selectable_serialization(strategy):
    if strategy.for_code:
        serializer = ComputeSerializer(strategy_code=strategy())
        data = foo
    else:
        serializer = ComputeSerializer(strategy_data=strategy())
        data = "foo"
    ser_data = serializer.serialize(data)
    assert ser_data[:ID_LEN] == strategy.identifier


@pytest.mark.parametrize("strategy", concretes.SELECTABLE_STRATEGIES)
def test_selectable_serialization_enforces_for_code(strategy):
    with pytest.raises(SerdeError) as pyt_exc:
        ComputeSerializer(strategy_code=strategy(), strategy_data=strategy())

    if strategy.for_code:
        e = "is a code serialization strategy, expected a data strategy"
    else:
        e = "is a data serialization strategy, expected a code strategy"
    assert e in str(pyt_exc)


def test_serializer_errors_on_unknown_strategy():
    class NewStrategy(SerializationStrategy):
        identifier = "aa\n"
        for_code = True

        def serialize(self, data):
            pass

        def deserialize(self, payload):
            pass

    strategy = NewStrategy()

    with pytest.raises(SerdeError):
        ComputeSerializer(strategy_code=strategy)

    NewStrategy.for_code = False

    with pytest.raises(SerdeError):
        ComputeSerializer(strategy_data=strategy)


@pytest.mark.parametrize(
    "strategy_code", (s for s in concretes.SELECTABLE_STRATEGIES if s.for_code)
)
@pytest.mark.parametrize(
    "strategy_data", (s for s in concretes.SELECTABLE_STRATEGIES if not s.for_code)
)
@pytest.mark.parametrize(
    "function, args, kwargs",
    [(foo, (random.random(),), {}), (foo, (random.random(),), {"y": random.random()})],
)
def test_check_strategies(strategy_code, strategy_data, function, args, kwargs):
    serializer = ComputeSerializer(
        strategy_code=strategy_code(), strategy_data=strategy_data()
    )

    new_fn, new_args, new_kwargs = serializer.check_strategies(
        function, *args, **kwargs
    )

    original_result = function(*args, **kwargs)
    new_result = new_fn(*new_args, **new_kwargs)

    assert original_result == new_result


@pytest.mark.parametrize("disallowed_strategy", concretes.SELECTABLE_STRATEGIES)
def test_allowed_deserializers(disallowed_strategy):
    allowlist = [
        strategy
        for strategy in concretes.SELECTABLE_STRATEGIES
        if strategy != disallowed_strategy
    ]

    assert allowlist, "expect to have at least one allowed deserializer"

    serializer = ComputeSerializer(allowed_deserializer_types=allowlist)
    payload = disallowed_strategy().serialize(
        foo if disallowed_strategy.for_code else "foo"
    )

    with pytest.raises(DeserializationError) as pyt_exc:
        serializer.deserialize(payload)
    assert f"serializer {disallowed_strategy.__name__} disabled" in str(pyt_exc)


@pytest.mark.parametrize(
    "allowlist",
    [
        [
            "globus_compute_sdk.serialize.concretes.DillCode",
            "globus_compute_sdk.serialize.JSONData",
        ],
        [f"{s.__module__}.{s.__qualname__}" for s in concretes.SELECTABLE_STRATEGIES],
    ],
)
def test_allowed_deserializers_imports_from_path(allowlist):
    serializer = ComputeSerializer(allowed_deserializer_types=allowlist)
    assert len(serializer.allowed_deserializer_types) == len(allowlist)


@pytest.mark.parametrize(
    "allowlist",
    [
        ["my_malicious_package.my_malicious_serializer"],
        ["invalid_path_1"],
        ["invalid path 2"],
        [""],
        [
            "globus_compute_sdk.serialize.concretes.DillCode",
            "my_malicious_package.my_malicious_serializer",
        ],
        [
            "globus_compute_sdk.serialize.concretes.DillCode",
            "invalid path",
        ],
        [
            "globus_compute_sdk.serialize.concretes.DillCode",
            "",
        ],
    ],
)
def test_allowed_deserializers_errors_on_invalid_import_path(allowlist):
    with pytest.raises(SerdeError) as pyt_exc:
        ComputeSerializer(allowed_deserializer_types=allowlist)
    assert "is not a valid path to a strategy" in str(pyt_exc)


@pytest.mark.parametrize(
    "allowlist",
    [
        [facade.AllowlistWildcard.CODE, facade.AllowlistWildcard.DATA],
        [facade.AllowlistWildcard.CODE, concretes.JSONData],
        [facade.AllowlistWildcard.CODE, "globus_compute_sdk.serialize.DillDataBase64"],
        (
            [facade.AllowlistWildcard.CODE]
            + [s for s in concretes.SELECTABLE_STRATEGIES if not s.for_code]
        ),
        [facade.AllowlistWildcard.DATA, concretes.CombinedCode],
        [facade.AllowlistWildcard.DATA, "globus_compute_sdk.serialize.DillCodeSource"],
        (
            [facade.AllowlistWildcard.DATA]
            + [s for s in concretes.SELECTABLE_STRATEGIES if s.for_code]
        ),
    ],
)
def test_allowed_deserializers_wildcards(allowlist):
    serializer = ComputeSerializer(allowed_deserializer_types=allowlist)
    parsedlist = serializer.allowed_deserializer_types

    if facade.AllowlistWildcard.CODE in allowlist:
        assert any(s.for_code for s in parsedlist)
        assert all(
            s in parsedlist for s in concretes.SELECTABLE_STRATEGIES if s.for_code
        )

    if facade.AllowlistWildcard.DATA in allowlist:
        assert any(not s.for_code for s in parsedlist)
        assert all(
            s in parsedlist for s in concretes.SELECTABLE_STRATEGIES if not s.for_code
        )


@pytest.mark.parametrize(
    "allowlist",
    [
        [facade.AllowlistWildcard.CODE, facade.AllowlistWildcard.DATA],
        [facade.AllowlistWildcard.CODE.value, facade.AllowlistWildcard.DATA],
        [facade.AllowlistWildcard.CODE, facade.AllowlistWildcard.DATA.value],
        [facade.AllowlistWildcard.CODE.value, facade.AllowlistWildcard.DATA.value],
    ],
)
def test_allowed_deserializers_wildcards_instance_or_value(allowlist):
    serializer = ComputeSerializer(allowed_deserializer_types=allowlist)
    parsedlist = serializer.allowed_deserializer_types

    assert all(s in parsedlist for s in concretes.SELECTABLE_STRATEGIES if s.for_code)
    assert all(
        s in parsedlist for s in concretes.SELECTABLE_STRATEGIES if not s.for_code
    )


@pytest.mark.parametrize(
    "allowlist",
    [
        *[
            [facade.AllowlistWildcard.CODE, s]
            for s in concretes.SELECTABLE_STRATEGIES
            if s.for_code
        ],
        *[
            [facade.AllowlistWildcard.DATA, s]
            for s in concretes.SELECTABLE_STRATEGIES
            if not s.for_code
        ],
    ],
)
def test_allowed_deserializers_wildcards_errors_on_duplicate(allowlist):
    with pytest.raises(SerdeError) as pyt_exc:
        ComputeSerializer(allowed_deserializer_types=allowlist)

    expected = f"Cannot mix '{allowlist[0]}' with specific deserializers"
    assert expected in str(pyt_exc)


@pytest.mark.parametrize(
    "allowlist",
    (
        [s for s in concretes.SELECTABLE_STRATEGIES if s.for_code],
        [s for s in concretes.SELECTABLE_STRATEGIES if not s.for_code],
        [
            f"{s.__module__}.{s.__qualname__}"
            for s in concretes.SELECTABLE_STRATEGIES
            if s.for_code
        ],
        [
            f"{s.__module__}.{s.__qualname__}"
            for s in concretes.SELECTABLE_STRATEGIES
            if not s.for_code
        ],
        [facade.AllowlistWildcard.CODE],
        [facade.AllowlistWildcard.DATA],
    ),
)
def test_allowed_deserializers_enforces_both_code_and_data(allowlist):
    with pytest.raises(SerdeError) as pyt_exc:
        ComputeSerializer(allowed_deserializer_types=allowlist)

    assert "at least one code and one data" in str(pyt_exc)


@pytest.mark.parametrize(
    "non_subclass",
    [
        type(
            "NonSubclassedStrategy",
            (),
            {
                "identifier": "aa\n",
                "for_code": True,
                "serialize": lambda self, data: None,
                "deserialize": lambda self, payload: None,
            },
        ),
        int,
        {"foo": "bar"},
    ],
)
def test_allowed_deserializers_enforces_subclass(non_subclass):
    with pytest.raises(SerdeError) as pyt_exc:
        ComputeSerializer(allowed_deserializer_types=[non_subclass])

    assert "deserializers must either be SerializationStrategies" in str(pyt_exc)


def test_allowed_deserializers_errors_on_unknown_strategy():
    class NewStrategy(SerializationStrategy):
        identifier = "aa\n"
        for_code = True

        def serialize(self, data):
            pass

        def deserialize(self, payload):
            pass

    with pytest.raises(SerdeError) as pyt_exc1:
        ComputeSerializer(allowed_deserializer_types=[NewStrategy])

    NewStrategy.for_code = False

    with pytest.raises(SerdeError) as pyt_exc2:
        ComputeSerializer(allowed_deserializer_types=[NewStrategy])

    for e in (pyt_exc1, pyt_exc2):
        assert "is not a known serialization strategy" in str(e)
