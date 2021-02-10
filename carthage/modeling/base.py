# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .implementation import *
from carthage.dependency_injection import * #type: ignore
import typing
import carthage.network

__all__ = []

class injector_access:

    '''Usage::

        val = injector_access("foo")

    At runtime, ``model_instance.val`` will be memoized to ``model_instance.injector.get_instance("foo")``.

    '''


    key: InjectionKey
    name: str

    def __init__(self, key):
        if not isinstance(key, InjectionKey):
            key = InjectionKey(key, _ready = False)
        if key.ready is None: key = InjectionKey(key, _ready = False)
        #: The injection key to be accessed.
        self.key = key
        # Our __call__method needs an injector
        inject(injector = Injector)(self)


    def __get__(self, inst, owner):
        res = inst.injector.get_instance(self.key)
        setattr(inst, self.name, res)
        return res

    def __set_name__(self, owner, name):
        self.name = name

    def __call__(self, injector: Injector):
        return injector.get_instance(self.key)
    def __repr__(self):
        return f'injector_access({repr(self.key)})'

__all__ += ['injector_access']

@inject(injector = Injector)
class InjectableModel(Injectable, metaclass = InjectableModelType):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for k,info in self.__class__.__initial_injections__.items():
            v, options = info
            try:
                self.injector.add_provider(k, v, **options)
            except Exception as e:
                raise RuntimeError(f'Failed registering {v} as provider for {k}') from e

        for cb in self.__class__._callbacks:
            cb(self)
            
__all__ += ['InjectableModel']

class NetworkModel(carthage.Network, InjectableModel, metaclass = ModelingContainer):

    def __init__(self, **kwargs):
        kwargs.update(gather_from_class(self, 'name', 'vlan_id'))
        super().__init__(**kwargs)
        if hasattr(self,'bridge_name'):
            self.ainjector.add_provider(carthage.network.BridgeNetwork,
                                        carthage.network.BridgeNetwork(self.bridge_name, delete_bridge = False))
            
__all__ += ['NetworkModel']

class NetworkConfigModel(InjectableModel,
                         carthage.network.NetworkConfig,
                         ):

    @modelmethod
    def add(cls, ns, interface, net, mac):
        def callback(inst):
            inst.add(interface, net, mac)
        cls._add_callback(ns, callback)

__all__ += ['NetworkConfigModel']

class ModelGroup(InjectableModel, metaclass = ModelingContainer): pass

class Enclave(InjectableModel, metaclass = ModelingContainer):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)

__all__ += ['ModelGroup', 'Enclave']

class MachineModelType(ModelingContainer):

    def __new__(cls, name, bases, ns, **kwargs):
        if '.' not in ns.get('name', "."):
            try:
                ns['name'] = ns['name']+ns['domain']
            except KeyError: pass
        return super().__new__(cls, name, bases, ns, **kwargs)
class MachineModel(InjectableModel, metaclass = MachineModelType):

    @classmethod
    def our_key(cls):
        return InjectionKey(MachineModel, host = cls.name)

__all__ += ['MachineModel']
