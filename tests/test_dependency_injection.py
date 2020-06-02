# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import dependency_injection
from carthage.dependency_injection import inject, InjectionKey, AsyncInjectable, inject_autokwargs
from carthage.utils import when_needed
from carthage.pytest import async_test

import asyncio, pytest

@pytest.fixture()
def injector():
    injector =  dependency_injection.Injector()
    injector.add_provider(asyncio.get_event_loop(), close = False)
    return injector

@pytest.fixture()
def a_injector(injector, loop):
    a_injector =  injector(dependency_injection.AsyncInjector, loop = loop)
    yield a_injector
    a_injector.close()

def test_injector_provides_self(injector):
    @inject(i = dependency_injection.Injector)
    def func(i):
        return i
    assert isinstance(injector(func), dependency_injection.Injector)


def test_injector_available(injector):
    assert isinstance(injector, dependency_injection.Injector)


def test_override_dependency(injector):
    k = dependency_injection.InjectionKey('some key')
    injector.add_provider(k,30)
    @inject(arg = k)
    def func(arg):
        assert arg == 20
    injector(func, arg = 20)
    # And make sure without the override the injector still provides the right thing
    @inject(i = k)
    def func2(i):
        assert i == 30
    injector(func2)

def test_override_replaces_subinjector(injector):
    class OverrideType: pass
    o1 = OverrideType()
    o2 = OverrideType()
    assert o1 is not o2
    @inject(o = OverrideType,
            i = dependency_injection.Injector)
    def func(i, o):
        assert o is o2
        assert injector is not i
        assert i.parent_injector is injector
    @inject(o = OverrideType)
    def func2(o):
        assert o is o1
    injector.add_provider(o1)
    injector(func, o = o2)
    injector(func2)




def test_injector_instantiates(injector):
    class SomeClass(dependency_injection.Injectable): pass
    @inject(s = SomeClass)
    def func(s):
        assert isinstance(s, SomeClass)
    injector.add_provider(SomeClass)
    injector(func)

def test_async_injector_construction(loop, injector):
    @inject(a = dependency_injection.AsyncInjector)
    def f(a):
        assert isinstance(a,dependency_injection.AsyncInjector)

    injector(f)


@async_test
async def test_construct_using_coro(a_injector, loop):
    async def coro():
        return 42
    k = dependency_injection.InjectionKey('run_coro')
    @inject(v = k)
    def f(v):
        assert v == 42
    a_injector.add_provider(k, coro)
    await a_injector(f)

@async_test
async def test_async_function(a_injector, loop):
    class Dependency(dependency_injection.Injectable): pass
    async def setup_dependency(): return Dependency()
    called = False
    @inject(d = Dependency)
    async def coro(d):
        assert isinstance(d, Dependency)
        nonlocal called
        called = True
    a_injector.add_provider(InjectionKey(Dependency), setup_dependency)
    await a_injector(coro)
    assert called is True



@async_test
async def test_async_ready(a_injector, loop):
    class AsyncDependency(dependency_injection.AsyncInjectable):
        async def async_ready(self):
            self.ready = True
            return self

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.ready = False
    @inject(r = AsyncDependency)
    def is_ready(r):
        assert r.ready
    await a_injector(is_ready, r = AsyncDependency)


def test_allow_multiple(injector):
    from carthage.config import ConfigLayout
    injector.add_provider(ConfigLayout, allow_multiple = True)
    s1 = dependency_injection.Injector(injector)
    s2 = dependency_injection.Injector(injector)
    assert s1 is not s2
    assert s1.parent_injector is injector
    assert s2.parent_injector is injector
    c1 = s1.get_instance(ConfigLayout)
    c2 = s2.get_instance(ConfigLayout)
    assert isinstance(c1, ConfigLayout)
    assert isinstance(c2, ConfigLayout)
    assert c1 is not c2
    c3 = injector.get_instance(ConfigLayout)
    assert isinstance(c3, ConfigLayout)
    assert c3 is not c1
    assert c3 is not c2


def test_allow_multiple_provider_at_root(injector):
    from carthage.config import ConfigLayout
    injector.add_provider(ConfigLayout, allow_multiple = True)
    s1 = dependency_injection.Injector(injector)
    s2 = dependency_injection.Injector(injector)
    assert s1 is not s2
    c3 = injector.get_instance(ConfigLayout)
    c1 = s1.get_instance(ConfigLayout)
    c2 = s2.get_instance(ConfigLayout)
    assert c3 is c1
    assert c2 is c3


def test_allow_multiple_false(injector):
    from carthage.config import ConfigLayout
    injector.add_provider(ConfigLayout, allow_multiple = False)
    s1 = dependency_injection.Injector(injector)
    s2 = dependency_injection.Injector(injector)
    assert s1 is not s2
    c1 = s1.get_instance(ConfigLayout)
    c2 = s2.get_instance(ConfigLayout)
    assert c1 is c2

@async_test
async def test_when_needed(a_injector, loop):
    class foo(dependency_injection.Injectable):

        def __init__(self):
            nonlocal called
            assert called is False
            called = True

    wn = when_needed(foo)
    i1 = InjectionKey('i1')
    i2 = InjectionKey('i2')
    called = False
    a_injector.add_provider(i1, wn)
    a_injector.add_provider(i2, wn)
    i1r = await a_injector.get_instance_async(i1)
    assert isinstance(i1r, foo)
    i2r = await a_injector.get_instance_async(i2)
    assert i2r is i1r
    assert await a_injector(wn) is i1r


@async_test
async def test_when_needed_override(a_injector, loop):
    k = dependency_injection.InjectionKey('foo')
    a_injector.add_provider(k, 20)
    @dependency_injection.inject(n = k)
    def func(n):
        assert n == 29
        return "foo"
    wn = when_needed(func, n = 29)
    assert await a_injector(wn) == "foo"


@async_test
async def test_when_needed_cancels(loop, a_injector):
    injector = await a_injector(dependency_injection.Injector)
    ainjector = injector(dependency_injection.AsyncInjector)
    cancelled = False
    k = dependency_injection.InjectionKey("bar")
    async def func():
        nonlocal cancelled
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError: cancelled = True
        return 39
    ainjector.add_provider(k, when_needed(func))
    loop.create_task(ainjector.get_instance_async(k))
    await asyncio.sleep(0.1)
    await dependency_injection.shutdown_injector(ainjector)
    assert cancelled is True


@async_test
async def test_async_ready_requires_return(a_injector):
    class AI(AsyncInjectable):

        async def async_ready(self):
            pass
    a_injector.add_provider(AI)
    with pytest.raises(TypeError):
        res = await a_injector.get_instance_async(AI)


def test_injectable_sets_dependencies(injector):
    "Test that the constructor for Injectable tries to store dependencies as instance variables"
    k = InjectionKey("test_key")
    @inject(foo = k)
    class i1(dependency_injection.Injectable): pass
    injector.add_provider(k, 33)
    i1_obj = injector(i1)
    assert i1_obj.foo == 33

def test_injectable_fails_on_unknown_args(injector):
    k = InjectionKey("test_key")
    @inject(foo = k)
    class i1(dependency_injection.Injectable): pass
    injector.add_provider(k, 33)
    with pytest.raises(TypeError):
        i1_obj = injector(i1, bar = 40)

def test_injectable_autokwargs(injector):
    k = InjectionKey("test_key")
    @inject_autokwargs(foo = k)
    class i1(dependency_injection.Injectable): pass
    injector.add_provider(k, 40)
    i1_obj = injector(i1)
    assert i1_obj.foo == 40
    with pytest.raises(TypeError):
        i1()


@async_test
async def test_injectable_inheritance(injector, a_injector):
    from carthage.dependency_injection import Injectable
    k1 = InjectionKey("k1")
    k2 = InjectionKey("k2")
    @inject_autokwargs(k1 = k1)
    class a(Injectable): pass
    @inject(k2 = k2)
    class b(a): pass
    @inject(k1 = None)
    class c(b):

        def __init__(self, **kwargs):
            super().__init__(k1 = 20, **kwargs)


    injector.add_provider(k1, 10)
    injector.add_provider(k2, 30)
    b_obj = injector(b)
    assert b_obj.k1 == 10
    assert b_obj.k2 == 30
    c_obj = injector(c)
    assert c_obj.k2 == 30
    assert c_obj.k1 == 20

    a_injector.add_provider(k1, 30)
    a_injector.add_provider(k2, 55)
    c_obj2 = await a_injector(c)
    assert c_obj2.k1 == 20


@async_test
async def test_injector_claiming(injector, a_injector):
    ainjector = a_injector
    i2 = injector(dependency_injection.Injector)
    assert i2.claimed_by is None
    assert i2.claim() is i2
    i3 = i2.claim()
    assert i2 is not i3
    assert i3.claimed_by
    class c(AsyncInjectable): pass
    c_obj = await ainjector(c)
    assert c_obj.injector.claimed_by() is c_obj
    ai2 = c_obj.ainjector.claim()
    assert ai2 is not c_obj.ainjector
    assert ai2.injector is not c_obj.ainjector.injector
    assert c_obj.ainjector.injector is c_obj.injector
    # If you are permitted to override injectors on a call to an
    # injector, interesting semantic questions come up; are you just
    # overriding the kwarg, or are you also overriding what the
    # subinjector will provide when asked to provide an injector.  If
    # so, you are violating the invarient that injectors always inject
    # themselves when asked for an injector.  In any case, if it ever
    # becomes possible to override the injector keyword, the following
    # test probably should pass for any reasonable semantics.
    with pytest.raises(dependency_injection.ExistingProvider):
        c2_obj = await ainjector(c, injector = i3)
        assert c2_obj.injector.claimed_by() is c2_obj
        assert c2_obj.injector.parent_injector is i3

def test_injection_key_copy():
    i1 = InjectionKey(int)
    assert i1 is InjectionKey(i1)
    i2 = InjectionKey(i1, optional = True)
    assert i2.optional
    assert i2 == i1
    assert i2 is not i1
