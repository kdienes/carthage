# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging, tempfile, time
from carthage.debian import  debian_container_to_vm
from carthage.image import ContainerImage
from carthage.network import Network, V4Config
from carthage.container import Container, container_image
from carthage.utils import when_needed
from carthage.pytest import *
import os.path, pytest, posix
from carthage import base_injector, AsyncInjector, sh, MachineCustomization, customization_task, ConfigLayout
from carthage.dependency_injection import *
from carthage.systemd import SystemdNetworkModelMixin
from carthage.modeling import *
from carthage.ansible import *
from carthage.setup_tasks import *
import carthage.ssh


@pytest.fixture()
def container(test_ainjector, loop):
    ainjector = test_ainjector
    container = loop.run_until_complete(ainjector(Container, name = "container-1"))
    yield container
    if container.running:
        loop.run_until_complete(container.stop_container())

@async_test
async def test_start_container(container, loop):
    await container.start_container()

@async_test
async def test_container_running(container, loop):
    async with container.container_running():
        container.shell("/bin/ls")
        

class LayoutTest(ModelGroup):

    @provides("test_net")
    class test_net(NetworkModel,  AsyncInjectable):
        name = "test_net"

        async def async_ready(self):
            await             super().async_ready()
            from carthage.network import BridgeNetwork
            net = await self.ainjector(self.access_by, BridgeNetwork)
            sh.ip(
                "addr",
                "add", "10.2.0.1/24", "dev", net.bridge_name)

    class net_config(NetworkConfigModel):
        add('eth0', mac = None, net = InjectionKey("test_net", _ready = True),
            v4_config = V4Config(address = "10.2.0.2",
                                 network  = "10.2.0.0/24"))


    add_provider(machine_implementation_key, dependency_quote(Container))

    class test_container(MachineModel, SystemdNetworkModelMixin):
        name = "test-container"

        ip_address = "10.2.0.2"
        class cust(MachineCustomization):

                

            do_something = ansible_playbook_task("resources/test_ansible.yml")
            do_roles = ansible_role_task(os.path.dirname(__file__)+"/resources/test_ansible_role")
            
@async_test
@inject(config = ConfigLayout)
async def test_ansible_and_modeling(test_ainjector, config):
    ainjector = test_ainjector
    layout = await ainjector(LayoutTest)
    layout.injector.add_provider(when_needed(AnsibleInventory, config.state_dir+"/ansible.yml"))
    ainjector.add_provider(InjectionKey("layout"), layout) # So it is cleaned up
    await ainjector.get_instance_async(carthage.ssh.SshKey)
    await layout.generate()
    await layout.test_container.machine.async_become_ready()
    

@async_test
async def test_container_to_vm(test_ainjector):
    ainjector = test_ainjector
    image = await ainjector.get_instance_async(container_image)
    with tempfile.TemporaryDirectory() as tmp:
        await ainjector(
            debian_container_to_vm,
            volume = image,
            output = os.path.join(tmp,"container_to_vm.raw"),
        size = "4G")
    
