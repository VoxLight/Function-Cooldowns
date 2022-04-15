from __future__ import annotations

import asyncio
import functools
import time
from asyncio.events import AbstractEventLoop, get_event_loop
from logging import getLogger
from typing import Callable, Optional, TypeVar, Dict, Union

from .cooldown_times_per import CooldownTimesPer
from .exceptions import NonExistent

from .utils import MaybeCoro, maybe_coro
from . import CooldownBucket
from .buckets import _HashableArguments
from .protocols import CooldownBucketProtocol

logger = getLogger(__name__)

T = TypeVar("T", bound=_HashableArguments)


def cooldown(
    limit: int,
    time_period: float,
    bucket: CooldownBucketProtocol,
    check: Optional[MaybeCoro] = lambda *args, **kwargs: True,
    *,
    cooldown_id: Optional[Union[int, str]] = None,
):
    """
    Wrap this Callable in a cooldown.

    Parameters
    ----------
    limit: int
        How many call's can be made in the time
        period specified by ``time_period``
    time_period: float
        The time period related to ``limit``
    bucket: CooldownBucketProtocol
        The :class:`Bucket` implementation to use
        as a bucket to separate cooldown buckets.
    check: Optional[MaybeCoro]
        A Callable which dictates whether or not
        to apply the cooldown on current invoke.

        If this Callable returns a truthy value,
        then the cooldown will be used for the current call.

        I.e. If you wished to bypass cooldowns, you
        would return False if you invoked the Callable.
    cooldown_id: Optional[Union[int, str]]
        Useful for resetting individual stacked cooldowns.
        This should be unique globally,
        behaviour is not guaranteed if not unique.


    Raises
    ------
    RuntimeError
        Expected the decorated function to be a coroutine
    CallableOnCooldown
        This call resulted in a cooldown being put into effect
    """
    _cooldown: Cooldown = Cooldown(limit, time_period, bucket, cooldown_id=cooldown_id)

    def decorator(func: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(func):
            raise RuntimeError("Expected `func` to be a coroutine")

        _cooldown._func = func
        attached_cooldowns = getattr(func, "_cooldowns", [])
        attached_cooldowns.append(_cooldown)
        setattr(func, "_cooldowns", attached_cooldowns)

        @functools.wraps(func)
        async def inner(*args, **kwargs):
            use_cooldown = await maybe_coro(check, *args, **kwargs)
            if not use_cooldown:
                return await maybe_coro(func, *args, **kwargs)

            async with _cooldown(*args, **kwargs):
                result = await func(*args, **kwargs)

            return result

        return inner

    return decorator


class Cooldown:
    """Represents a cooldown for any given :type:`Callable`."""

    def __init__(
        self,
        limit: int,
        time_period: float,
        bucket: Optional[CooldownBucketProtocol] = None,
        func: Optional[Callable] = None,
        *,
        cooldown_id: Optional[Union[int, str]] = None,
    ) -> None:
        """
        Parameters
        ----------
        limit: int
            How many call's can be made in the time
            period specified by ``time_period``
        time_period: float
            The time period related to ``limit``
        bucket: Optional[CooldownBucketProtocol]
            The :class:`Bucket` implementation to use
            as a bucket to separate cooldown buckets.

            Defaults to :class:`CooldownBucket.all`
        func: Optional[Callable]
            The function this cooldown is attached to
        cooldown_id: Optional[Union[int, str]]
            Useful for resetting individual stacked cooldowns.
            This should be unique globally,
            behaviour is not guaranteed if not unique.
        """
        bucket = bucket or CooldownBucket.all
        self.limit: int = limit
        self.time_period: float = time_period
        self.cooldown_id: Optional[Union[int, str]] = cooldown_id

        self._func: Optional[Callable] = func
        self._bucket: CooldownBucketProtocol = bucket
        self.loop: AbstractEventLoop = get_event_loop()
        self.pending_reset: bool = False
        self._last_bucket: Optional[_HashableArguments] = None

        self._cache: Dict[_HashableArguments, CooldownTimesPer] = {}

        # How long to sleep between attempt cache clean calls
        self._cache_clean_eagerness: int = 250
        self._clean_task = asyncio.create_task(self._keep_buckets_clear())

    async def __aenter__(self) -> "Cooldown":
        bucket: CooldownTimesPer = self._get_cooldown_for_bucket(self._last_bucket)
        async with bucket:
            return self

    async def __aexit__(self, *_) -> None:
        ...

    def __call__(self, *args, **kwargs):
        self._last_bucket = self.get_bucket(*args, **kwargs)
        return self

    def _get_cooldown_for_bucket(
        self, bucket: _HashableArguments, *, raise_on_create: bool = False
    ) -> CooldownTimesPer:
        try:
            return self._cache[bucket]
        except KeyError:
            if raise_on_create:
                raise NonExistent

            _bucket = CooldownTimesPer(self.limit, self.time_period, self)
            self._cache[bucket] = _bucket
            return _bucket

    def get_bucket(self, *args, **kwargs) -> _HashableArguments:
        """
        Return the given bucket for some given arguments.

        This uses the underlying :class:`CooldownBucket`
        and will return a :class:`_HashableArguments`
        instance which is inline with how Cooldown's function.

        Parameters
        ----------
        args: Any
            The arguments to get a bucket for
        kwargs: Any
            The keyword arguments to get a bucket for

        Returns
        -------
        _HashableArguments
            An internally correct representation
            of a bucket for the given arguments.

            This can then be used in :meth:`Cooldown.clear` calls.
        """
        data = self._bucket.process(*args, **kwargs)
        if self._bucket is CooldownBucket.all:
            return _HashableArguments(*data[0], **data[1])

        elif self._bucket is CooldownBucket.args:
            return _HashableArguments(*data)

        elif self._bucket is CooldownBucket.kwargs:
            return _HashableArguments(**data)

        return _HashableArguments(data)

    async def _keep_buckets_clear(self):
        while True:
            self.clear()
            await asyncio.sleep(self._cache_clean_eagerness)

    def clear(
        self, bucket: Optional[_HashableArguments] = None, *, force_evict: bool = False
    ) -> None:
        """
        Remove all un-needed buckets, this maintains buckets
        which are currently tracking cooldown's.

        Parameters
        ----------
        bucket: Optional[_HashableArguments]
            The bucket we wish to reset
        force_evict: bool
            If ``True``, delete all tracked cooldown's
            regardless of whether or not they are needed.

            I.e. reset this back to a fresh state.

        Notes
        -----
        You can get :class:`_HashableArguments` by
        using the :meth:`Cooldown.get_bucket` method.
        """
        if not bucket:
            # Reset all buckets
            for bucket in list(self._cache.keys()):
                self.clear(bucket, force_evict=force_evict)

        try:
            # Evict item from cache only if it
            # is not tracking anything
            _bucket: CooldownTimesPer = self._cache[bucket]
            if not _bucket.has_cooldown or force_evict:
                del self._cache[bucket]
        except KeyError:
            pass

    def remaining_calls(self, *args, **kwargs) -> int:
        """
        Given a :type:`Callable`, return the amount of remaining
        available calls before these arguments will result
        in the callable being rate-limited.

        Parameters
        ----------
        args
        Any arguments you will pass.
        kwargs
            Any key-word arguments you will pass.

        Returns
        -------
        int
            How many more times this :type:`Callable`
            can be called without being rate-limited.
        """
        bucket: _HashableArguments = self.get_bucket(*args, **kwargs)
        try:
            cooldown_times_per: CooldownTimesPer = self._get_cooldown_for_bucket(
                bucket, raise_on_create=True
            )
        except NonExistent:
            return self.limit

        return cooldown_times_per.current

    def __repr__(self) -> str:
        return f"Cooldown(limit={self.limit}, time_period={self.time_period}, func={self._func})"

    @property
    def bucket(self) -> CooldownBucketProtocol:
        return self._bucket

    @property
    def func(self) -> Optional[Callable]:
        return self._func
