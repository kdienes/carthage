# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging
from sqlalchemy.orm import Session
from hadron.inventory.admin import models
from ..dependency_injection import inject, Injector, InjectionKey
from .database import *
from ..utils import when_needed
from ..image import setup_task, SetupTaskMixin
from ..vm import VM
from ..machine import Machine
from ..container import Container
import carthage.hadron_layout
from carthage import base_injector

logger = logging.getLogger('carthage')

@inject(
    injector = Injector)
def provide_networks(injector, session):
    for n in session.query(models.Network):
        if len(n.locations) == 0: continue
        try:
            site_injector = injector(Injector)
            hn = when_needed(HadronNetwork, n, injector = site_injector)
            site_injector.add_provider(site_network_key, hn)
            base_injector.add_provider(InjectionKey(HadronNetwork,
                                                netid = n.netid), hn)
            for s in session.query(models.Slot).filter(models.Slot.location_id.in_(l.id for l in n.locations)):
                if s.hostname is None: continue
                try:
                    m = provide_slot(s, injector = site_injector, session = session)
                    if 'router' in [r.name for r in  s.roles]:
                        try:
                            site_injector.add_provider(site_router_key, m)
                        except Exception: pass
                except Exception:
                    logger.exception('Error adding slot{}'.format(s.fqdn()))
        except Exception:
            logger.exception("Error adding network {}".format(n))

class RouterMixin(SetupTaskMixin):

    @setup_task('ansible_all')
    @inject(database = carthage.hadron.database_key)
    async def run_ansible_all(self, database):
        async with self.machine_running:
            await self.ssh_online()
            self.ssh('modprobe nf_conntrack_ipv4')
            self.ssh('ls /proc/sys/net/netfilter')
            await database.ssh('-A',
                           'cd /hadron-operations/ansible && ansible-playbook',
                           '-iinventory',
                           '-l{}'.format(self.name),
                           '-eansible_host={}'.format(self.ip_address),
                           'commands/all.yml',
                           _bg = True, _bg_exc = False)
        
vm_roles = {'router',
            'desktop',
            'desktop-ingest',
            'ingest',
            'videowall',
            'workstation'}

mixin_map = {
    'router': RouterMixin
    }



def provide_slot(s, *, session, injector):
    injector = injector(Injector)
    injector.add_provider(InjectionKey('this_slot'), s)
    base = Container
    role_names = set(r.name for r in s.roles)
    if role_names & vm_roles:
        base = VM
    mixins = []
    for r in role_names:
        if r in mixin_map and mixin_map[r] not in mixins:
            mixins.append(mixin_map[r])
    if 'router' in role_names:
        network_config = carthage.hadron_layout.router_network_config
    else: network_config = carthage.hadron_layout.site_network_config
    class HadronMachine(base, *mixins):
        if 'router' in role_names:
            ip_address = "192.168.101.{}".format(s.network.netid)
        else: ip_address = s.full_ip
        
    if s.item:
        s.item.machine #lazy load
    machine =  when_needed(HadronMachine,
                       name = s.fqdn(),
                             network_config = network_config,
                             injector = injector)
    base_injector.add_provider(InjectionKey(Machine, host = s.fqdn()), machine)
    return machine