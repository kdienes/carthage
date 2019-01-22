# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys
from .images import HadronContainerImageMount, HadronVmImage
from ..vmware.image import VmdkTemplate, VmwareDataStore
from ..dependency_injection import AsyncInjector, inject
from ..utils import when_needed
from ..config import ConfigLayout
from .. import sh
from ..image import setup_task
from ..container import Container, container_image, container_volume


@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector)
class HadronVmdkMount(HadronContainerImageMount):

    @setup_task("install-vm-tools")
    async def install_vm_tools(self):
        ainjector = self.injector(AsyncInjector)
        ainjector.add_provider(container_volume, self)
        ainjector.add_provider(container_image, self)
        container = await ainjector(Container, name = self.name,
                                    skip_ssh_keygen = True)
        process = await container.run_container("/usr/bin/apt", "-y", "install", "open-vm-tools")
        await process

@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector,
        store = VmwareDataStore)
class HadronVmdkBase(HadronVmImage):

    def __init__(self, *, ainjector, config_layout,
                 store,
                 name = "aces-vmdk", **kwargs):
        super().__init__(**kwargs, name = name,
                         ainjector = ainjector, config_layout = config_layout,
                         customize_mount = HadronVmdkMount, path = store.vmdk_path)

if __name__ == '__main__':
    from carthage import base_injector
    from asyncio import get_event_loop
    loop = get_event_loop()
    ainjector = base_injector(AsyncInjector)
    from carthage.vmware.image import NfsDataStore
    from carthage.config import inject_config
    inject_config(base_injector)
    base_injector.add_provider(NfsDataStore)
    cl = base_injector(ConfigLayout)
    cl.load_yaml(open(sys.argv[1]).read())
    base = loop.run_until_complete(ainjector(HadronVmdkBase))
    base.close()
    template = loop.run_until_complete(ainjector(VmdkTemplate, image = base))

    