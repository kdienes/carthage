# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextlib, logging , os, re
from pathlib import Path
from .dependency_injection import *
from .setup_tasks import *
from .config import ConfigLayout
from .image import ContainerImage, ContainerCustomization
from .machine import customization_task
from . import sh

logger = logging.getLogger('carthage')

file_re = re.compile(r'file:/+(/[^/]+.*)')

__all__ = []

def bind_args_for_mirror(mirror):
    match = file_re.match(mirror)
    if match:
        return [f'--bind={match.group(1)}']
    return []

__all__ += ['bind_args_for_mirror']

@inject_autokwargs(config_layout = ConfigLayout)
class DebianContainerCustomizations(ContainerCustomization):

    description = "Set up Debian for Carthage"
    
    @setup_task("Turn on networkd")
    async def turn_on_networkd(self):
        await self.container_command("systemctl", "enable", "systemd-networkd")

    @setup_task("Install python")
    async def install_python(self):
        bind_args = bind_args_for_mirror(self.config_layout.debian.stage1_mirror)
        if bind_args:
            # debootstrap apparently drops sources.list for file sources
            sources_list = Path(self.path)/"etc/apt/sources.list"
            with sources_list.open("wt") as f:
                mirror = self.config_layout.debian.mirror
                distribution = self.config_layout.debian.distribution
                f.write(f'deb {mirror} {distribution} main contrib non-free')
                
        await self.container_command(*bind_args,
                                     "apt", "update")
        await self.container_command(*bind_args,
                                     "apt-get", "-y", "install", "python3")
        

class DebianContainerImage(ContainerImage):

    mirror: str
    distribution: str

    def __init__(self, name:str = "base-debian",
                 mirror: str = None, distribution: str = None, **kwargs):
        super().__init__(name = name, **kwargs)
        self.mirror = self.config_layout.debian.stage1_mirror
        self.distribution = self.config_layout.debian.distribution
        if mirror: self.mirror = mirror
        if distribution: self.distribution = distribution

    @setup_task("unpack")
    async def unpack_container_image(self):
        await sh.debootstrap('--include=openssh-server',
                             self.distribution,
                             self.path, self.mirror,
                             _bg = True,
                             _bg_exc = False)

    debian_customizations = customization_task(DebianContainerCustomizations)

    @setup_task("Update mirror")
    def update_mirror(self):
        update_mirror(self.path, self.mirror, self.distribution)

__all__ += ['DebianContainerImage']

def update_mirror(path, mirror, distribution):
    etc_apt = Path(path)/"etc/apt"
    sources_list = etc_apt/"sources.list"
    if sources_list.exists():
        os.unlink(sources_list)
    debian_list = etc_apt/"sources.list.d/debian.list"
    os.makedirs(debian_list.parent, exist_ok = True)
    with debian_list.open("wt") as f:
        f.write(f'''
deb {mirror} {distribution} main contrib non-free
deb-src {mirror} {distribution} main contrib non-free
''')

@contextlib.asynccontextmanager
async def use_stage1_mirror(machine):
    debian = machine.config_layout.debian
    async with machine.filesystem_access() as path:
        try:
            update_mirror(path, debian.stage1_mirror, debian.distribution)
            if machine.running:
                await machine.ssh("apt", "update",
                                  _bg = True, _bg_exc = False)
            else:
                await machine.container_command(*bind_args_for_mirror(debian.stage1_mirror),
                                                "apt", "update")
            yield
        finally:
            update_mirror(path, debian.mirror, debian.distribution)
            try:
                if machine.running:
                    await machine.ssh("apt", "update",
                                      _bg = True, _bg_exc = False)
                else:
                    await machine.container_command(*bind_args_for_mirror(debian.mirror),
                                                    "apt", "update")
            except: logger.exception("Error cleaning up mirror")
            
__all__ += ['use_stage1_mirror']

def install_stage1_packages_task(packages):
    @setup_task(f'Install {packages} using stage 1 mirror')
    async def install_task(self):
        async with use_stage1_mirror(self):
            mirror = self.config_layout.debian.stage1_mirror
            await self.container_command(
                *bind_args_for_mirror(mirror),
                'apt', '-y',
                'install', *packages)
    return install_task

__all__ += ['install_stage1_packages_task']