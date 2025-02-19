# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import contextlib
import json
import logging
from pathlib import Path
import tempfile
import dateutil.parser
from carthage.dependency_injection import *
from .. import sh
from ..machine import AbstractMachineModel, Machine
from ..utils import memoproperty
from ..oci import *
from ..setup_tasks import setup_task, SetupTaskMixin


logger = logging.getLogger('carthage.podman')


def podman_port_option(p: OciExposedPort):
    res = f'-p{p.host_ip}:{p.host_port}:{p.container_port}'
    if p.proto != 'tcp':
        res += f'/{p.proto}'
    return res


def podman_mount_option(injector: Injector, m: OciMount):
    res = f'--mount=type={m.mount_type}'
    if m.source:
        res += f',source={m.source_resolved(injector)}'
    if m.destination:
        res += f',destination={m.destination}'
    else:
        raise TypeError('destination is required')
    if m.options:
        res += f',{m.options}'
    return res


__all__ = []


class PodmanContainerHost(AsyncInjectable):

    def podman(self, *args,
               _bg=True, _bg_exc=True):
        raise NotImplementedError

    async def filesystem_access(self, container):
        raise NotImplementedError

    async def tar_volume_context(self, volume):
        '''
        An asynchronous context manager that tars up a volume and provides a path to that tar file usable in ``podman import``.  Typical usage::

            async with container_host.tar_volume_context(container_image) as path:
                await container_host.podman('import', path)

        On local systems this manages temporary directories.  For remote container hosts, this manages to get the tar file to the remote system and clean up later.
        '''
        raise NotImplementedError


class LocalPodmanContainerHost(PodmanContainerHost):

        
    
    @contextlib.asynccontextmanager
    async def filesystem_access(self, container):
        result = await self.podman(
            'container', 'mount',
            container,
            _bg=True, _bg_exc=False)
        try:
            path = str(result).strip()
            yield Path(path)
        finally:
            pass  # Perhaps we should unmount, but we'd need a refcount to do that.

    def podman(self, *args,
               _bg=True, _bg_exc=False):
        return sh.podman(
            *args,
            _bg=_bg, _bg_exc=_bg_exc,
            _encoding='utf-8')

    @contextlib.asynccontextmanager
    async def tar_volume_context(self, volume):
        assert hasattr(volume, 'path')
        with tempfile.TemporaryDirectory() as path_raw:
            path = Path(path_raw)
            await sh.tar(
                "-C", str(volume.path),
                "--xattrs",
                "--xattrs-include=*.*",
                "-czf",
                str(path / "container.tar.gz"),
                ".",
                _bg=True,
                _bg_exc=False)
            yield path / 'container.tar.gz'


@inject(
    podman_pod_options=InjectionKey('podman_pod_options', _optional=NotPresent),
)
class PodmanPod(OciPod):

    #: A list of extra options to pass to pod create
    podman_pod_options = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(InjectionKey(PodmanPod), dependency_quote(self))

    async def find(self):
        if self.id:
            inspect_arg = self.id
        else:
            inspect_arg = self.name
        try:
            result = await self.podman(
                'pod', 'inspect', inspect_arg)
        except sh.ErrorReturnCode:
            return False
        pod_info = json.loads(str(result.stdout, 'utf-8'))
        self.pod_info = pod_info
        return dateutil.parser.isoparse(pod_info['Created']).timestamp()

    async def do_create(self):
        options = []
        for p in self.exposed_ports:
            options.append(podman_port_option(p))
        options.extend(self.podman_pod_options)
        await self.podman(
            'pod', 'create',
            *options,
            '--name=' + self.name)

    async def delete(self, force=False):
        force_options = []
        if force:
            force_options.append('--force')
        await self.podman(
            'pod', 'rm',
            *force_options,
            self.name)

    @memoproperty
    def podman(self):
        return self.injector(LocalPodmanContainerHost).podman


__all__ += ['PodmanPod']


@inject_autokwargs(
    oci_container_image=InjectionKey(oci_container_image, _optional=NotPresent),
    podman_restart=InjectionKey('podman_restart', _optional=NotPresent),
    pod=InjectionKey(PodmanPod, _optional=True),
    podman_options=InjectionKey('podman_options', _optional=NotPresent),
)
class PodmanContainer(Machine, OciContainer):

    '''
An OCI container implemented using ``podman``.  While it is possible to set up a container to be accessible via ssh and to meet all the interfaces of :class:`~carthage.machine.SshMixin`, this is relatively uncommon.  Such containers often have an entry point that is not a full init, and only run one service or program.  Typically :meth:`container_exec` is used to execute an additional command in the scope of a container rather than using :meth:`ssh`.
    '''

    #: Timeout in seconds to wait when stopping a container
    stop_timeout = 10
    machine_running_ssh_online = False
    rsync_uses_filesystem_access = True

    #: restart containers (no, always, on-failure)
    podman_restart = 'no'

    #:Extra options (as a list) to be passed into podman create
    podman_options = []

    @memoproperty
    def ssh_options(self):
        if not hasattr(self, 'ssh_port'):
            raise ValueError('Set ssh_port before ssh')
        return (
            *super().ssh_options,
            f'-p{self.ssh_port}')

    #:The port on which to connect to for ssh
    ssh_port: int

    @memoproperty
    def ansible_inventory_overrides(self):
        return dict(
            ansible_connection='containers.podman.podman',
            ansible_pipelining=False,
            ansible_host=self.full_name,
        )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._operation_lock = asyncio.Lock()

    @memoproperty
    def podman(self):
        return self.container_host.podman

    @memoproperty
    def container_host(self):
        return self.injector(LocalPodmanContainerHost)

    async def find(self):
        try:
            result = await self.podman(
                'container', 'inspect', self.full_name,
                _bg=True, _bg_exc=False)
        except sh.ErrorReturnCode:
            return False
        containers = json.loads(str(result))
        self.container_info = containers[0]
        ports = self.container_info['NetworkSettings']['Ports']
        if not hasattr(self, 'ssh_port') and '22/tcp' in ports:
            self.ssh_port = ports['22/tcp'][0]['HostPort']
        self.id = self.container_info['Id']
        self.running = self.container_info['State']['Running']
        try:
            return dateutil.parser.isoparse(containers[0]['Created']).timestamp()
        except Exception as e:
            raise ValueError(f'Invalid ISO string: {self.container_info["Created"]}')

    async def do_create(self):
        image = self.oci_container_image
        if isinstance(image, OciImage):
            await image.async_become_ready()
            image = image.oci_image_tag
        if self.pod:
            await self.pod.async_become_ready()
        command_options = []
        if self.oci_command:
            command_options = list(self.oci_command)
        await self.podman(
            'container', 'create',
            f'--name={self.full_name}',
            *self._podman_create_options(),
            image,
            *command_options,
            _bg=True, _bg_exc=False)

    def _podman_create_options(self):
        options = []
        options.append('--restart=' + self.podman_restart)
        if self.oci_interactive:
            options.append('-i')
        if self.oci_tty:
            options.append('-t')
        for k, v in self.injector.filter_instantiate(
                OciEnviron, lambda k: 'name' in k.constraints and k.constraints.get('scope', 'all') in ('all', 'container')):
            options.append('-e' + v.assignment)
        if not self.pod:
            for p in self.exposed_ports:
                options.append(podman_port_option(p))
        else:  # there is a pod
            options.append('--pod=' + self.pod.name)
        for m in self.mounts:
            options.append(podman_mount_option(self.injector, m))
        options.extend(self.podman_options)
        return options

    async def delete(self, force=True, volumes=True):
        force_args = []
        if force:
            force_args.append('--force')
        if volumes:
            force_args.append('--volumes')
        await self.podman(
            'container', 'rm',
            *force_args, self.full_name,
            _bg=True, _bg_exc=False)

    async def is_machine_running(self):
        assert await self.find(), "Container does not exist"
        self.running = self.container_info['State']['Running']
        return self.running

    async def start_machine(self):
        async with self._operation_lock:
            await self.is_machine_running()
            if self.running:
                return
            await self.start_dependencies()
            await super().start_machine()
            logger.info(f'Starting {self.full_name}')
            await self.podman(
                'container', 'start', self.full_name,
                _bg=True, _bg_exc=False)
        await self.is_machine_running()

    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running:
                return
            await self.podman(
                'container', 'stop',
                f'-t{self.stop_timeout}',
                self.full_name,
                _bg=True, _bg_exc=False)
            self.running = False
            await super().stop_machine()

    def container_exec(self, *args):
        '''
'Execute a command in a running container and return stdout.  This function intentionally has a differentname than :meth:`carthage.container.Container.container_command` because that method does not expect the container to be running.
'''
        result = self.podman(
            'container', 'exec',
            self.full_name,
            *args,
        )
        return result

    #: An alias to be more compatible with :class:`carthage.container.Container`
    shell = container_exec

    def _apply_to_filesystem_customization(self, customization):
        @contextlib.asynccontextmanager
        async def customization_context():
            async with self.machine_running(ssh_online=False), self.filesystem_access() as path:
                customization.path = path
                yield
            return
        customization.customization_context = customization_context()
        customization.run_command = self.container_exec

    def filesystem_access(self):
        return self.container_host.filesystem_access(self.full_name)

    def __repr__(self):
        try:
            host = repr(self.container_host)
        except Exception:
            host = "repr failed"
        return f'<{self.__class__.__name__} {self.name} on {host}>'

    @memoproperty
    def stamp_path(self):
        state_dir = Path(self.config_layout.state_dir)
        result = state_dir.joinpath("podman", self.name)
        result.mkdir(exist_ok=True, parents=True)
        return result


    def check_stamp(self, stamp, raise_on_error=False):
        mtime, text = super().check_stamp(stamp, raise_on_error)
        creation = getattr(self, '_find_result', None)
        if creation and mtime < creation:
            return False, "" #stamp predates container creation
        return mtime, text

__all__ += ['PodmanContainer']


class PodmanImageBuilderContainer(PodmanContainer):

    oci_command = ['sleep', '3600']
    stop_timeout = 0

    def _apply_to_container_customization(self, customization):
        @contextlib.asynccontextmanager
        async def customization_context():
            async with self.machine_running(ssh_online=False), self.filesystem_access() as path:
                customization.path = path
                yield
            return
        customization.customization_context = customization_context()

    @memoproperty
    def model(self):
        from .modeling import PodmanImageModel
        res =  self.injector.get_instance(InjectionKey(PodmanImageModel, _ready=False, _optional=True))
        if res is None: raise AttributeError
        return res

@inject_autokwargs(
    base_image=InjectionKey(oci_container_image, _optional=NotPresent),
)
class PodmanImage(OciImage, SetupTaskMixin):

    '''
    Represents an OCI container image and provides facilities for building the image.

    :class:`customizations <carthage.machine.BaseCustomization>` can be turned into image layers using the :func:`image_layer_customization` function.  Note that :func:`setup_tasks <setup_task>` are only run when images are actually built.  By default, the image is only built if it does not exist, although see :meth:`should_build` to override.

    '''

    last_layer = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.layer_number = 1

    async def pull_base_image(self):
        if isinstance(self.base_image, OciImage):
            await self.base_image.async_become_ready()
            base_image = self.base_image.oci_image_tag
        else:
            base_image = self.base_image
        if not base_image.startswith('localhost/'):
            await self.podman(
                'pull', base_image,
            )
        inspect_result = await self.podman(
            'image', 'inspect',
            base_image)
        image_info = json.loads(str(inspect_result))[0]
        self.parse_base_image_info(image_info)

    def parse_base_image_info(self, image_info):
        config = image_info['Config']
        if not self.oci_image_command and 'Cmd' in config:
            self.oci_image_command = config['Cmd']
        if not self.oci_image_entry_point and 'EntryPoint' in config:
            self.oci_image_entry_point = config['EntryPoint']
        self.base_image_info = image_info
        self.last_layer = self.base_image_info['Id']

    async def find(self):
        if self.id:
            to_find = self.id
            self.oci_read_only = True
        else:
            to_find = self.oci_image_tag
        try:
            result = await self.podman(
                'image', 'inspect', to_find,
            )
        except sh.ErrorReturnCode:
            return False
        info = json.loads(str(result))[0]
        self.id = info['Id']
        self.image_info = info
        return dateutil.parser.isoparse(info['Created']).timestamp()

    async def should_build(self):
        '''If the image exists, this is called.  If it returns True, then the image will be rebuilt even though it exists.  If a caller wants to force a rebuild, it is better to call :meth:`build_image` than to patch this method.
        '''
        return False

    @contextlib.asynccontextmanager
    async def image_layer_context(self, commit_message=""):
        '''
        Generate a container to produce  a new image layer:

        * The image of the container will be either *self.last_layer* or *self.base_image* if *last_layer* is not set.

        * The container will be a :class:`PodmanImageBuilderContainer`, and as such will simply pause when started so that :meth:`container_exec` can be used to run commands in the container.

        Usage::

            async with self.image_layer_context() as layer_container:
                # Apply customizations/run commands in layer_container
            #Now, self.last_layer is the image ID of the new layer
        '''
        def container_delete(future):
            try:
                future.result()
            except Exception as e:
                logger.error('Error deleting %s: %s', layer_container, str(e))

        base_image = self.last_layer or self.base_image
        layer_container = await self.ainjector(
            PodmanImageBuilderContainer,
            oci_container_image=base_image,
            name=f'carthage-image-build-{id(self)}-l{self.layer_number}',
        )
        self.layer_number += 1
        try:
            await layer_container.start_machine()
            yield layer_container
            await self.commit_container(layer_container, commit_message)
        finally:
            delete_task = asyncio.get_event_loop().create_task(layer_container.delete())
            delete_task.add_done_callback(container_delete)

    def _commit_options(self):
        entrypoint = None
        cmd = None
        if self.oci_image_entry_point:
            entrypoint = json.dumps(self.oci_image_entry_point)
        if self.oci_image_command:
            cmd = json.dumps(self.oci_image_command)
        options = []
        if cmd:
            options.append('--change=CMD ' + cmd)
        if entrypoint:
            options.append('--change=ENTRYPOINT ' + entrypoint)
        for k, v in self.injector.filter_instantiate(
                OciEnviron, lambda k: 'name' in k.constraints and k.constraints.get('scope', 'all') in ('all','image')):
            options.append('--change=ENV '+v.assignment)
        return options

    async def commit_container(self, container, commit_message):
        options = self._commit_options()
        if self.oci_image_author:
            options.append('--author=' + self.oci_image_author)
        if commit_message:
            options.append('-fdocker')
            options.append('--message=' + commit_message)
        # options must be quoted if it's going through ssh or something that can split args on space
        commit_result = await self.podman(
            'container', 'commit',
            *options,
            container.id)
        self.last_layer = str(commit_result.stdout, 'utf-8').strip()

    async def tag_last_layer(self):
        assert self.last_layer
        await self.podman(
            'image', 'tag',
            self.last_layer, self.oci_image_tag)

    async def find_or_create(self):
        '''See if image exists otherwise rebuild the image.
        Note that this is not a :func:`setup_task` even though it is in the parent.  This is always run from :meth:`async_ready`
        '''
        if await self.find():
            if not await self.should_build():
                return
        return await self.build_image()

    async def build_image(self):
        await self.pull_base_image()
        # You might think that context for run_setup_tasks should be
        # self.image_layer_context().  If it worked that way, then
        # everything would end up in a single layer.  Instead, use
        # image_layer_task for wrapping customizations and explicitly
        # call image_layer_context in setup_tasks.
        await self.run_setup_tasks()
        if not self.last_layer:
            logger.warn('%s failed to generate any image layers', self)
            return
        await self.tag_last_layer()

    async def async_ready(self):
        await self.find_or_create()
        return await AsyncInjectable.async_ready(self)

    @memoproperty
    def podman(self):
        return self.container_host.podman

    @memoproperty
    def container_host(self):
        return self.injector(LocalPodmanContainerHost)


__all__ += ['PodmanImage']
podman_image_volume_key = InjectionKey('carthage.podman/image_volume')


@inject(base_image=None)
@inject_autokwargs(
    image_volume=podman_image_volume_key,
)
class PodmanFromScratchImage(PodmanImage):

    async def pull_base_image(self):
        await self.image_volume.async_become_ready()
        async with self.container_host.tar_volume_context(self.image_volume) as tar_path:
            result = await self.podman(
                'image', 'import',
                *self._commit_options(),
                tar_path,
            )
        id = str(result.stdout, 'utf-8').strip()
        inspect_result = await self.podman(
            'image', 'inspect',
            id)
        image_info = json.loads(str(inspect_result))[0]
        self.last_layer = id
        self.parse_base_image_info(image_info)


__all__ += ['PodmanFromScratchImage', 'podman_image_volume_key']


def image_layer_task(customization, **kwargs):
    '''Wrap a :class:`~carthage.machine.BaseCustomization` as a layer in a :class:`PodmanImage`.
    '''
    if getattr(customization, 'description', None):
        kwargs['description'] = customization.description
    if 'description' not in kwargs:
        kwargs['description'] = 'Image Layer: ' + customization.__name__

    @setup_task(**kwargs)
    async def task(image):
        async with image.image_layer_context(kwargs['description']) as container:
            await container.apply_customization(customization)

    @task.check_completed()
    def task(image):
        # We always want to re-run layers
        return False
    return task


__all__ += ['image_layer_task']

class ContainerfileImage(OciImage):

    '''
    Build an image using ``podman build`` from a context directory with a ``Containerfile``.

    :param container_context: A directory with a Containerfile and potentially other files used by the Containerfile.  This can be specified either in a call to the constructor or in a subclass definition.  In the constructor, this is resolved relative to the current directory.  In a subclass, this is resolved relative to the package (or module) in which the class is defined.

    This class does respect :class:`OciMount` and :class:`OciEnviron` in the injector hierarchy.

    '''

    def __init__(self, container_context=None, **kwargs):
        if container_context: self.container_context = container_context
        else:
            if not hasattr(self, 'container_context'):
                raise TypeError('container_context must be set on the class or in the constructor')
            try:
                import sys
                module = sys.modules[self.__class__.__module__]
            except Exception as e:
                module = None
                warnings.warn(f'Unable to find module for {self.__class__.__qualname__}: {e}')
            if module:
                try: path = Path(module.__path__[0])
                except Exception:
                    path = Path(module.__file__).parent
                self.container_context = path/self.container_context
        super().__init__(**kwargs)

    @memoproperty
    def container_host(self):
        return self.injector(LocalPodmanContainerHost)

    async def do_create(self):
        options = await self._build_options()
        return await self.container_host.podman(
            'build',
            '-t'+self.oci_image_tag,
            *options,
            self.container_context)

    async def find(self):
        try: inspect_result = await self.container_host.podman(
                'image', 'inspect',
                self.oci_image_tag)
        except sh.ErrorReturnCode: return False
        inspect_json = json.loads(str(inspect_result.stdout, 'utf-8'))
        created = dateutil.parser.isoparse(inspect_json[0]['Created']).timestamp()
        if self.container_context_mtime > created:
            return False
        return created

    @property
    def container_context_mtime(self):
        context = Path(self.container_context)
        mtime = 0.0
        for p in context.iterdir():
            stat = p.stat()
            if stat.st_mtime >mtime: mtime = stat.st_mtime
        return mtime
    
    

    async def _build_options(self):
        options = []
        # Instantiate a container simply so we can ask it for volume, mount, and environment options.
        with instantiation_not_ready():
            container = await self.ainjector(PodmanContainer, name='image_options')
        for k, v in self.injector.filter_instantiate(
                OciEnviron, lambda k: 'name' in k.constraints and k.constraints.get('scope', 'all') in ('all','image')):
            options.append('-e' + v.assignment)
        for m in container.mounts:
            options.append(podman_mount_option(self.injector, m))
            options.extend(self.podman_options)
        return options

__all__ += ['ContainerfileImage']
