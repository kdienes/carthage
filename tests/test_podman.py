# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import os
import pytest
import shutil
from pathlib import Path
from carthage.podman import *
from carthage.oci import oci_container_image, OciExposedPort, OciMount
from carthage.ansible import *
from carthage.container import container_image
from carthage.modeling import *
from carthage.image import SshAuthorizedKeyCustomizations
from carthage.ssh import SshKey
from carthage import *
from carthage.machine import FilesystemCustomization
import carthage
from carthage.pytest import *

state_dir = Path(__file__).parent.joinpath("test_state")


@pytest.fixture()
def ainjector(ainjector):
    ainjector = ainjector.claim("test_setup.py")
    config = ainjector.injector(carthage.ConfigLayout)
    config.state_dir = state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    yield ainjector
    shutil.rmtree(state_dir, ignore_errors=True)


class podman_layout(CarthageLayout):
    layout_name = 'podman'

    add_provider(machine_implementation_key, dependency_quote(PodmanContainer))
    add_provider(oci_container_image, 'debian:latest')
    #add_provider(ansible_log, "/tmp/ansible.log")

    oci_interactive = True

    class FromScratchDebian(PodmanFromScratchImage):
        oci_image_cmd = 'bash'
        oci_image_tag = 'localhost/from_scratch_debian'

    class DebianWithAuthorizedKeys(PodmanImage):
        oci_image_tag = 'localhost/authorized-debian:latest'
        authorized_keys = image_layer_task(SshAuthorizedKeyCustomizations)

    class foo(MachineModel):

        name = 'foo.com'

    class ssh_test(MachineModel):
        name = 'ssh-test.foo.com'
        ip_address = '127.0.0.1'

        add_provider(OciExposedPort(22))

    class mount_test(MachineModel):
        add_provider(OciMount(
            mount_type='bind',
            destination='/host',
            source='/',
        ))

    class ansible_test(MachineModel):

        class cust(FilesystemCustomization):

            @setup_task("Install Ansible")
            async def install_ansible(self):
                await self.run_command('apt', 'update')
                await self.run_command('apt', '-y', 'install', 'ansible')

            do_roles = ansible_role_task(os.path.dirname(__file__) + "/resources/test_ansible_role")

    class stamps_discarded(MachineModel):

        #A machine to confirm that stamps are ignored after a machine is deleted
        task_called = False
        class cust(FilesystemCustomization):

            @setup_task("Set variable")
            def set_variable(self):
                self.host.model.task_called = True
                
    class TrueImage(ContainerfileImageModel):

        oci_image_tag = 'localhost/true:latest'
        container_context = 'resources/true_container'

    class true_machine(MachineModel):
        add_provider(oci_container_image, injector_access(TrueImage))

        name = 'true-machine'

    class pod_group(ModelGroup):
        add_provider(OciExposedPort(22))

        @provides(InjectionKey(PodmanPod))
        class pod(PodmanPod):
            name = 'carthage-test-pod'

        class pod_member(MachineModel):
            pass


@async_test
async def test_podman_create(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.foo.machine
    await machine.async_become_ready()
    assert await machine.find()
    machine.stop_timeout = 1
    async with machine.machine_running(ssh_online=False):
        assert await machine.is_machine_running()
    await machine.delete()
    assert not await machine.find()


@async_test
async def test_container_exec(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.foo.machine
    try:
        await machine.async_become_ready()
        machine.stop_timeout = 1
        async with machine.machine_running(ssh_online=False):
            assert 'root' in str(await machine.container_exec('ls'))
    finally:
        await machine.delete()


@async_test
async def test_container_ssh(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.ssh_test.machine
    await ainjector.get_instance_async(SshKey)
    try:
        await machine.async_become_ready()
        machine.stop_timeout = 1
        async with machine.machine_running(ssh_online=False):
            await machine.container_exec('apt', 'update')
            await machine.container_exec(
                'apt', '-y', '--no-install-recommends', 'install', 'openssh-server')
            await machine.apply_customization(SshAuthorizedKeyCustomizations)
            await machine.container_exec('mkdir', '/run/sshd')
            await machine.container_exec('/usr/sbin/sshd')
            await machine.ssh_online()
    finally:
        await machine.delete()


@async_test
async def test_podman_image(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    await l.DebianWithAuthorizedKeys.async_become_ready()


@async_test
async def test_podman_mount(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.mount_test.machine
    assert machine.mounts
    try:
        machine.stop_timeout = 0
        await machine.async_become_ready()
        async with machine.machine_running(ssh_online=False):
            await machine.container_exec('ls', '/host/etc')
    finally:
        await machine.delete()


@async_test
async def test_from_scratch_image(test_ainjector):
    l = await test_ainjector(podman_layout)
    ainjector = l.ainjector
    config = await test_ainjector(ConfigLayout)
    config.delete_volumes = False
    ainjector.add_provider(podman_image_volume_key, injector_access(container_image))
    await l.FromScratchDebian.async_become_ready()


@async_test
async def test_podman_ansible(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.ansible_test.machine
    try:
        await machine.async_become_ready()
    finally:
        await machine.delete()


@async_test
async def test_podman_pod(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    pg = l.pod_group
    machine = pg.pod_member.machine
    try:
        await machine.async_become_ready()
    finally:
        try:
            await pg.pod.delete(force=True)
        except Exception:
            pass

@async_test
async def test_stamps_ignored(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    assert l.stamps_discarded.task_called is False
    await l.stamps_discarded.machine.async_become_ready()
    assert l.stamps_discarded.task_called is True
    await l.stamps_discarded.machine.delete()
    # Podman doesn't work very well if you do something to an instance after delete
    # So we instantiate a second instance directly with the class.
    # Note that we're instantiating to ready since we call the injector directly
    stamps_discarded_2 = await ainjector(podman_layout.stamps_discarded)
    try:
        await stamps_discarded_2.machine.async_become_ready()
        assert stamps_discarded_2.task_called
    finally:
        try: await stamps_discarded_2.machine.delete()
        except Exception: pass
        
@async_test
async def test_containerfile_image(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    try:
        await l.true_machine.machine.async_become_ready()
    finally:
        try: await l.true_machine.machine.delete()
        except Exception: pass
