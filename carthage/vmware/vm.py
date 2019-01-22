# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import carthage.ansible
import os.path
from ..machine import Machine
from ..dependency_injection import *
from ..config import ConfigLayout, config_defaults, config_key
from ..image import SetupTaskMixin, setup_task
from ..utils import memoproperty
from .image import VmwareDataStore, VmdkTemplate


config_defaults.add_config({
    'vmware': {
        'datacenter': None,
        'folder': 'carthage',
        'cluster': None,
        'hardware': {
            'boot_firmware': 'efi',
            'version': 14,
            'memory': 4096,
            'disk': 25,
            'cpus': 1,
            'paravirt': True,
            },
        }})
vmware_config = config_key('vmware')

class VmwareStampable(SetupTaskMixin, AsyncInjectable):
    stamp_type = None #: Override with type of object for which stamp is created
    folder = None


    @memoproperty
    def stamp_path(self):
        state_dir = self.config_layout.state_dir
        p = os.path.join(state_dir, "vmware_stamps", self.stamp_type)
        if self.folder:
            p = os.path.join(p, self.folder)
        p = os.path.join(p, self.name)
        p += ".stamps"
        os.makedirs(p, exist_ok = True)
        return p


@inject(config = vmware_config)
def vmware_dict(config, **kws):
    '''
:returns: A dictionary containing vmware common parameters to pass into Ansible
'''
    d = dict(
        datacenter = config.datacenter,
        username = config.username,
        hostname = config.hostname,
        validate_certs = config.validate_certs,
        password = config.password)
    d.update(kws)
    return d

@inject(config_layout = ConfigLayout,
        injector = Injector)
class VmFolder(VmwareStampable):

    stamp_type = "vm_folder"

    def __init__(self, name, *, config_layout, injector):
        self.name = name
        self.injector = injector.claim()
        self.config_layout = config_layout
        self.ainjector = injector(AsyncInjector)
        super().__init__()

    @setup_task("create_folder")
    async def create_folder(self):
        d = self.injector(vmware_dict)
        d['folder_name'] = self.name
        d['state'] = 'present'
        parent, sep, tail = self.name.rpartition('/')
        if sep != "":
            d['parent_folder'] = parent
            d['folder_name'] = tail
            self.parent = await self.ainjector(self.__class__, parent)
        else: d['folder_type'] = 'vm'
        await self.ainjector(carthage.ansible.run_play,
            [carthage.ansible.localhost_machine],
            [{'vcenter_folder': d}])

    def __repr__(self):
        return f"<VmFolder: {self.name}>"
    

        
            
@inject(
    config = ConfigLayout,
    injector = Injector,
    storage = VmwareDataStore,
    # We add a template VM to the dependencies later avoiding a forward reference
    )
class Vm(Machine, VmwareStampable):

    stamp_type = "vm"
    
    def __init__(self, name, template,
                 guest_id = "ubuntu64Guest",
                 *, injector, config, storage):
        super().__init__(name, injector, config)
        self.storage = storage
        vm_config = config.vmware
        self.cpus = vm_config.hardware.cpus
        self.memory = vm_config.hardware.memory
        self.nested_virt = False
        self.paravirt = vm_config.hardware.paravirt
        self.disk_size = vm_config.hardware.disk
        self.guest_id = guest_id
        self.template_name = None
        self.template_snapshot = None
        if template:
            self.template_name = template.full_name
            if template.clone_from_snapshot:
                self.template_snapshot = template.clone_from_snapshot
                
        
    @inject(folder = InjectionKey(VmFolder, optional = True))
    async def _ansible_dict(self, folder, **kwargs):
        config = self.config_layout.vmware
        if self.paravirt:
            scsi_type = "paravirtual"
            network_type = "vmxnet3"
        else:
            raise NotImplementedError("Loop up what we want to use in non-paravirt mode")
        if not folder:
            folder = await self.ainjector(VmFolder, config.folder)
        d = await self.ainjector(vmware_dict,
                                 name = self.full_name,
                                 folder = folder.name,
                                 cluster = config.cluster,
                                 datastore = self.storage.name,
                                 guest_id = self.guest_id,
                                 hardware = dict(
                                     boot_firmware = config.hardware.boot_firmware,
                                     num_cpus = self.cpus,
                                     memory_mb = self.memory,
                                     version = config.hardware.version,
                                     scsi = scsi_type,
                                     nested_virt = self.nested_virt),
                                 disk = [dict(
                                     size_gb = self.disk_size,
                                     type = "thin")],
                                 networks = [dict(
                                     name = "VM Network",
                                     device_type = network_type)],
        )
        if self.template_name:
            d['template'] = self.template_name
            if self.template_snapshot:
                d['linked_clone'] = True
                d['src_snapshot'] = self.template_snapshot
        d.update(kwargs)
        return d

    async def _ansible_op(self, **kwargs):
        d = await self.ainjector(self._ansible_dict, **kwargs)
        return await self.ainjector(carthage.ansible.run_play,
                                    [carthage.ansible.localhost_machine],
                                    {'vmware_guest': d})

    
    @setup_task("provision_vm")
    async def create_vm(self):
        return await self.ainjector(self._ansible_op, state='present')

    
    async def delete_vm(self):
        return await self.ainjector(self._ansible_op, state = 'absent', force = True)

@inject(
    config = ConfigLayout,
    injector = Injector,
    storage = VmwareDataStore,
    disk = VmdkTemplate
    )
class VmTemplate(Vm):
    clone_from_snapshot = None

    def __init__(self, disk, **kwargs):
        self.disk = disk
        super().__init__(disk.image.name, template = None, **kwargs)

    async def _ansible_dict(self, **kwargs):
        d = await self.ainjector(super()._ansible_dict, **kwargs)
        d.update(is_template = True,
                 )
        d['disk'][0]['filename'] = self.disk.disk_path
        return d
    