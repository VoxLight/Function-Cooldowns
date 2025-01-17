import asyncio
import datetime
from enum import Enum

import pytest

from cooldowns import (
    DynamicCooldown,
    CooldownBucket,
    dynamic_cooldown,
    DynamicCooldownTimesPer,
)
from cooldowns.buckets import _HashableArguments
from cooldowns.exceptions import DynamicCallableOnCooldown, NonExistent


@pytest.mark.asyncio
async def test_cooldown():
    # .2s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=0.2)).time()
    cooldown = DynamicCooldown(1, target_time, CooldownBucket.args)

    async with cooldown:
        with pytest.raises(DynamicCallableOnCooldown):
            async with cooldown:
                pass

        await asyncio.sleep(0.3)  # Cooldown 'length'
        # This tests that cooldowns get reset
        async with cooldown:
            pass

@pytest.mark.asyncio
async def test_get_bucket():
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    cooldown = DynamicCooldown(1, target_time)
    hashed_args = cooldown.get_bucket(1, 2, three=3, four=4)
    assert hashed_args == _HashableArguments(1, 2, three=3, four=4)


@pytest.mark.asyncio
async def test_cooldown_decor_simple():
    # Can be called once every second
    # Default bucket is ALL arguments
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(1, target_time, bucket=CooldownBucket.all)
    async def test_func(*args, **kwargs) -> (tuple, dict):
        return args, kwargs

    # Call it once, so its on cooldown after this
    data = await test_func(1, two=2)
    assert data == ((1,), {"two": 2})

    with pytest.raises(DynamicCallableOnCooldown):
        # Since this uses the same arguments
        # as the previous call, it comes under
        # the same bucket, and thus gets rate-limited
        await test_func(1, two=2)

    # Shouldn't error as it comes under the
    # bucket _HashableArguments(1) rather then
    # the bucket _HashableArguments(1, two=2)
    # which are completely different
    await test_func(1)


@pytest.mark.asyncio
async def test_cooldown_args():
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(1, target_time, bucket=CooldownBucket.args)
    async def test_func(*args, **kwargs) -> (tuple, dict):
        return args, kwargs

    data = await test_func(1, two=2)
    assert data == ((1,), {"two": 2})

    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(1)

    await test_func(2)


@pytest.mark.asyncio
async def test_cooldown_kwargs():
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(1, target_time, bucket=CooldownBucket.kwargs)
    async def test_func(*args, **kwargs) -> (tuple, dict):
        return args, kwargs

    data = await test_func(1, two=2)
    assert data == ((1,), {"two": 2})

    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(two=2)

    await test_func(two=3)


@pytest.mark.asyncio
async def test_custom_buckets():
    class CustomBucket(Enum):
        first_arg = 1

        def process(self, *args, **kwargs):
            if self is CustomBucket.first_arg:
                # This bucket is based ONLY off
                # of the first argument passed
                return args[0]


    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(1, target_time, bucket=CustomBucket.first_arg)
    async def test_func(*args, **kwargs):
        pass

    await test_func(1, 2, 3)

    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(1)

    await test_func(2)


@pytest.mark.asyncio
async def test_stacked_cooldowns():
    # Can call ONCE time_period second using the same args
    # Can call TWICE time_period second using the same kwargs
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(1, target_time, bucket=CooldownBucket.args)
    @dynamic_cooldown(2, target_time, bucket=CooldownBucket.kwargs)
    async def test_func(*args, **kwargs) -> (tuple, dict):
        return args, kwargs

    await test_func(2, one=1)
    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(2)

    # Args don't matter, its a kwargs based CooldownBucketProtocol
    await test_func(1, two=2)
    await test_func(two=2)
    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(two=2)


def test_sync_cooldowns():
    with pytest.raises(RuntimeError):
        # Cant use sync functions
        # 1s in the future
        target_time = datetime.datetime.now()
        target_time = (target_time + datetime.timedelta(seconds=1)).time()
        @dynamic_cooldown(1, 1, bucket=CooldownBucket.args)
        def test_func(*args, **kwargs) -> (tuple, dict):
            return args, kwargs


@pytest.mark.asyncio
async def test_checks():
    """Ensures the check works as expected"""
    # Only apply cooldowns if the first arg is 1
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(
        1, target_time, bucket=CooldownBucket.args, check=lambda *args, **kwargs: args[0] == 1
    )
    async def test_func(*args, **kwargs) -> (tuple, dict):
        return args, kwargs

    await test_func(1, two=2)
    await test_func(2)
    await test_func(tuple())
    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(1)


@pytest.mark.asyncio
async def test_async_checks():
    """Ensures the check works as expected with async methods"""
    # Only apply cooldowns if the first arg is 1
    async def mock_db_check(*args, **kwargs):
        # You can do database calls here or anything
        # since this is an async context
        return args[0] == 1

    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(1, target_time, bucket=CooldownBucket.args, check=mock_db_check)
    async def test_func(*args, **kwargs) -> (tuple, dict):
        return args, kwargs

    await test_func(1, two=2)
    await test_func(2)
    await test_func(tuple())
    with pytest.raises(DynamicCallableOnCooldown):
        await test_func(1)


@pytest.mark.asyncio
async def test_cooldown_clearing():
    # 1s in the future
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    cooldown: DynamicCooldown = DynamicCooldown(1, target_time, CooldownBucket.all)

    assert not cooldown._cache

    r_1 = cooldown.get_bucket(1, 1)
    assert isinstance(r_1, _HashableArguments)

    # Test both specific and global clearing
    _bucket: DynamicCooldownTimesPer = cooldown._get_cooldown_for_bucket(r_1)
    assert isinstance(_bucket, DynamicCooldownTimesPer)
    assert cooldown._cache

    cooldown.clear(r_1)
    assert not cooldown._cache

    _bucket_2: DynamicCooldownTimesPer = cooldown._get_cooldown_for_bucket(r_1)
    assert isinstance(_bucket_2, DynamicCooldownTimesPer)
    assert cooldown._cache

    cooldown.clear()
    assert not cooldown._cache

    # Test 'in-use' buckets arent cleared
    _bucket_3: DynamicCooldownTimesPer = cooldown._get_cooldown_for_bucket(r_1)
    assert isinstance(_bucket_3, DynamicCooldownTimesPer)
    assert cooldown._cache

    assert not _bucket_3.has_cooldown
    _bucket_3.current -= 1
    assert _bucket_3.has_cooldown

    cooldown.clear()
    assert cooldown._cache


@pytest.mark.asyncio
async def test_remaining():
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(2, target_time, CooldownBucket.all)
    async def test():
        pass

    _cooldown: DynamicCooldown = getattr(test, "_cooldowns")[0]
    assert _cooldown.remaining_calls() == 2
    await test()
    assert _cooldown.remaining_calls() == 1
    await test()
    assert _cooldown.remaining_calls() == 0
    with pytest.raises(DynamicCallableOnCooldown):
        await test()


@pytest.mark.asyncio
async def test_bucket_cleaner():
    # We have like 5 seconds to get this right
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(2, target_time, CooldownBucket.all)
    async def test():
        pass

    _cooldown: DynamicCooldown = getattr(test, "_cooldowns")[0]
    _cooldown._cache_clean_eagerness = 1
    assert not _cooldown._cache
    await test()
    assert _cooldown._cache
    await asyncio.sleep(2)
    assert not _cooldown._cache

@pytest.mark.asyncio
async def test_get_cooldown_times_per():
    target_time = datetime.datetime.now()
    target_time = (target_time + datetime.timedelta(seconds=1)).time()
    @dynamic_cooldown(2, target_time, CooldownBucket.all)
    async def test():
        pass

    _cooldown: DynamicCooldown = getattr(test, "_cooldowns")[0]

    assert _cooldown.get_cooldown_times_per(_cooldown.get_bucket()) is None
    await test()
    assert _cooldown.get_cooldown_times_per(_cooldown.get_bucket()) is not None
